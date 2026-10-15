import hashlib
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from workbench.pacing import (
    estimate_slot_seconds,
    trim_wav_edges,
    voice_slot_seconds,
)


def _write_wav(path, samples, *, rate=24000, channels=1, width=2):
    arr = np.asarray(samples, dtype="<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(width)
        f.setframerate(rate)
        f.writeframes(arr.tobytes())


def test_voice_slot_seconds_boundaries():
    assert voice_slot_seconds(0.5) == pytest.approx(0.85)
    assert voice_slot_seconds(0.7) == pytest.approx(1.0)
    assert voice_slot_seconds(0.9) == pytest.approx(1.08)
    assert voice_slot_seconds(2.5) == pytest.approx(2.68)
    assert estimate_slot_seconds(4, 4) == pytest.approx(1.18)
    assert estimate_slot_seconds(12, 4) == pytest.approx(3.18)


def test_trim_wav_edges_keeps_small_head_and_tail_windows(tmp_path):
    path = tmp_path / "voice.wav"
    source = np.zeros(24000, dtype="<i2")
    source[1000:2000] = 1000
    _write_wav(path, source)
    count = trim_wav_edges(path)
    assert count == 2000 - 1000 + 480 + 960
    with wave.open(str(path), "rb") as f:
        actual = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
    assert len(actual) == count
    assert np.array_equal(actual, source[520:2960])


def test_trim_wav_edges_rejects_silence_and_bad_format(tmp_path):
    silent = tmp_path / "silent.wav"
    _write_wav(silent, np.zeros(1000, dtype="<i2"))
    with pytest.raises(RuntimeError, match="voice/trim"):
        trim_wav_edges(silent)
    stereo = tmp_path / "stereo.wav"
    _write_wav(stereo, np.ones(1000, dtype="<i2"), channels=2)
    with pytest.raises(RuntimeError, match="voice/trim"):
        trim_wav_edges(stereo)


def test_renderer_voice_slots_use_ceil_frames(tmp_path):
    from workbench import renderer
    from workbench.media import sha256

    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=1080x1920:r=30:d=4", "-an", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )
    lines = ["甲", "乙"]
    voices = []
    for i, n in enumerate((21600, 16800)):
        wav = tmp_path / f"voice_{i:03d}.wav"
        pcm = np.zeros(n, dtype="<i2")
        pcm[240: n - 240] = 1000
        _write_wav(wav, pcm)
        voices.append({"index": i, "file": wav.name, "samples": n,
                       "sha256": hashlib.sha256(wav.read_bytes()).hexdigest()})
    candidate = {"path": str(source), "start": 0.0, "seconds": 4.0,
                 "source_sha256": sha256(source), "index_id": "fixture"}
    job = {"lines": lines, "mapping": [[candidate], [candidate]], "voices": voices,
           "variant": 0, "settings": {"voice": True, "subtitles": False,
           "beauty": False, "shot_reuse": 0, "dedup": "none",
           "visual_filter": "original"}}

    class Config:
        ffmpeg = "ffmpeg"
        ffprobe = "ffprobe"
        max_stage_seconds = 120
        project_src = str(tmp_path)
        font_path = ""

    _, timeline, _, _ = renderer.render_video(job, Config(), tmp_path)
    for i, row in enumerate(timeline):
        expected = math.ceil(voice_slot_seconds(voices[i]["samples"] / 24000) * 30)
        actual = round((row["end"] - row["start"]) * 30)
        assert expected - actual <= 3
        assert actual <= expected


def test_renderer_voice_margin_rejects_a_short_measured_slot(tmp_path, monkeypatch):
    from workbench import renderer
    from workbench.media import sha256

    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=1080x1920:r=30:d=2", "-an", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )
    wav = tmp_path / "voice_000.wav"
    _write_wav(wav, np.ones(24000, dtype="<i2") * 1000)
    candidate = {"path": str(source), "start": 0.0, "seconds": 2.0,
                 "source_sha256": sha256(source), "index_id": "fixture"}
    job = {"lines": ["甲"], "mapping": [[candidate]], "voices": [{
        "index": 0, "file": wav.name, "samples": 24000,
        "sha256": hashlib.sha256(wav.read_bytes()).hexdigest()}], "variant": 0,
        "settings": {"voice": True, "subtitles": False, "beauty": False,
                     "shot_reuse": 0, "dedup": "none", "visual_filter": "original"}}

    class Config:
        ffmpeg = "ffmpeg"
        ffprobe = "ffprobe"
        max_stage_seconds = 120
        project_src = str(tmp_path)
        font_path = ""

    # Force a slot that is shorter than the voice-plus-margin contract; the
    # renderer must still fail at its explicit voice margin gate.
    monkeypatch.setattr(renderer, "voice_slot_seconds", lambda seconds: seconds + 0.05)
    with pytest.raises(ValueError, match="render/voice margin"):
        renderer.render_video(job, Config(), tmp_path)


def test_verify_sentence_pause_distribution(monkeypatch, tmp_path):
    from workbench import verify

    wavs = []
    for i in range(2):
        p = tmp_path / f"voice_{i:03d}.wav"
        _write_wav(p, np.ones(19200, dtype="<i2") * 1000)
        wavs.append(p)
    timeline = [{"start": 0.0, "end": 1.0}, {"start": 1.0, "end": 2.0}]
    pcm = np.zeros(48000, dtype=np.float64)
    pcm[:19200] = 1000 / 32768
    second_start = 24000
    pcm[second_start: second_start + 19200] = 1000 / 32768
    info = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080,
                          "height": 1920, "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
                          "duration": "2.0", "start_time": "0"},
                         {"codec_type": "audio", "codec_name": "aac", "sample_rate": "24000",
                          "channels": 1, "bit_rate": "128000", "duration": "2.0",
                          "start_time": "0"}],
            "format": {"duration": "2.0"},
            "packets": [{"size": 40000, "duration_time": "2.0"}]}
    monkeypatch.setattr(verify, "probe", lambda *a, **k: info)
    monkeypatch.setattr(verify, "decode", lambda *a, **k: pcm)
    monkeypatch.setattr(verify, "sha256", lambda p: "x")
    def corr(x, y, start):
        good = bool(np.any(np.asarray(y)[start:start + len(x)]))
        return {"ok": good, "whole": 1.0 if good else 0.0, "head": 1.0,
                "tail": 1.0, "rms_ratio": 1.0, "offset_samples": 0,
                "non_silent_blocks": []}
    monkeypatch.setattr(verify, "correlate_pcm", corr)
    monkeypatch.setattr(verify, "local_gain_gate", lambda *a, **k: {"ok": True})
    (tmp_path / "final.mp4").write_bytes(b"fixture")
    report = verify.verify_media(tmp_path / "final.mp4", "ffmpeg", "ffprobe", timeline,
                                 expected=pcm, voice_wavs=wavs, mode="voice")
    assert report["checks"]["sentence_pauses"] is True
    assert report["sentence_pauses"]["values"] == pytest.approx([0.2])
    import workbench.media as media_mod
    monkeypatch.setattr(media_mod, "sha256", lambda p: "x")
    media_mod.validate_report(report, tmp_path / "final.mp4")
    bad = [dict(timeline[0]), {"start": 1.3, "end": 2.0}]
    bad[1]["end"] = 2.3
    info["streams"][0]["duration"] = info["streams"][1]["duration"] = "2.3"
    info["format"]["duration"] = "2.3"
    bad_expected = np.zeros(round(2.3 * 24000), dtype=np.float64)
    bad_expected[:19200] = 1000 / 32768
    bad_expected[31200:50400] = 1000 / 32768
    monkeypatch.setattr(verify, "decode", lambda *a, **k: bad_expected)
    with pytest.raises(verify.VerificationFailure, match="verify/sentence pauses"):
        verify.verify_media(tmp_path / "final.mp4", "ffmpeg", "ffprobe", bad,
                            expected=bad_expected, voice_wavs=wavs, mode="voice")
