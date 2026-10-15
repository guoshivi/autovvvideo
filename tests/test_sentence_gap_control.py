import hashlib
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from workbench.settings import ProductionSettings
from workbench.timing import estimate_timing


def _write_wav(path, samples, value=1200):
    pcm = np.zeros(int(samples), dtype="<i2")
    pcm[120:-120] = value
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(pcm.tobytes())


def _render_job(tmp_path, *, gap=None, voice=True, lines=("甲", "乙"), candidate_seconds=6.0, sample_counts=None):
    from workbench import renderer
    from workbench.media import sha256

    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=1080x1920:r=30:d=6", "-an", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(source)], check=True,
    )
    voices = []
    counts=sample_counts or (12000, 14400)
    for i, samples in enumerate(counts[:len(lines)]):
        wav = tmp_path / f"voice_{i:03d}.wav"
        _write_wav(wav, samples)
        voices.append({"index": i, "file": wav.name, "samples": samples,
                       "sha256": hashlib.sha256(wav.read_bytes()).hexdigest()})
    candidate = {"path": str(source), "start": 0.0, "seconds": candidate_seconds,
                 "source_sha256": sha256(source), "index_id": "fixture"}
    settings = {"voice": voice, "subtitles": False, "beauty": False,
                "shot_reuse": 0, "dedup": "off", "visual_filter": "original"}
    if gap is not None:
        settings["sentence_gap_seconds"] = gap
    job = {"lines": list(lines), "mapping": [[candidate] for _ in lines],
           "voices": voices if voice else [], "variant": 0, "settings": settings}

    class Config:
        ffmpeg = "ffmpeg"
        ffprobe = "ffprobe"
        max_stage_seconds = 120
        project_src = str(tmp_path)
        font_path = ""

    result = renderer.render_video(job, Config(), tmp_path)
    return result, voices


def test_sentence_gap_setting_is_optional_and_strict():
    assert ProductionSettings.from_dict({}).snapshot()["sentence_gap_seconds"] is None
    assert ProductionSettings.from_dict({"sentence_gap_seconds": None}).snapshot()["sentence_gap_seconds"] is None
    for value in (True, False, "0.2", float("nan"), float("inf"), -0.01, 0.09, 1.01, [], {}):
        with pytest.raises(ValueError):
            ProductionSettings.from_dict({"sentence_gap_seconds": value})
    assert ProductionSettings.from_dict({"sentence_gap_seconds": 0.10}).snapshot()["sentence_gap_seconds"] == 0.10
    assert ProductionSettings.from_dict({"sentence_gap_seconds": 1.00}).snapshot()["sentence_gap_seconds"] == 1.00


def test_manual_gap_changes_estimate_only_between_sentences():
    auto = estimate_timing(["甲", "乙"], {"files": []}, settings={"voice": True})
    manual = estimate_timing(["甲", "乙"], {"files": []}, settings={"voice": True, "sentence_gap_seconds": 1.0})
    single_auto = estimate_timing(["甲"], {"files": []}, settings={"voice": True})
    single_manual = estimate_timing(["甲"], {"files": []}, settings={"voice": True, "sentence_gap_seconds": 1.0})
    assert manual.seconds > auto.seconds
    assert single_manual.seconds == pytest.approx(single_auto.seconds)


def test_renderer_explicit_gap_preserves_pcm_and_real_pause(tmp_path):
    (auto, voices_auto) = _render_job(tmp_path / "auto", gap=None)
    (manual, voices_manual) = _render_job(tmp_path / "manual", gap=1.0)
    assert [v["sha256"] for v in voices_auto] == [v["sha256"] for v in voices_manual]
    assert [v["samples"] for v in voices_auto] == [v["samples"] for v in voices_manual]
    auto_pause = auto[1][1]["start"] - (auto[1][0]["start"] + voices_auto[0]["samples"] / 24000)
    manual_pause = manual[1][1]["start"] - (manual[1][0]["start"] + voices_manual[0]["samples"] / 24000)
    assert 0.18 - 1e-6 <= auto_pause <= 0.35 + 1 / 30 + 1e-6
    assert 1.0 - 1e-6 <= manual_pause <= 1.0 + 1 / 30 + 1e-6
    assert manual[1][-1]["end"] > auto[1][-1]["end"]


def test_single_sentence_and_mute_ignore_manual_gap(tmp_path):
    (auto, voices) = _render_job(tmp_path / "single-auto", gap=None, lines=("甲",))
    (manual, _) = _render_job(tmp_path / "single-manual", gap=1.0, lines=("甲",))
    assert manual[1][0]["end"] == pytest.approx(auto[1][0]["end"])
    (silent_auto, _) = _render_job(tmp_path / "silent-auto", gap=None, voice=False, lines=("甲",))
    (silent_manual, _) = _render_job(tmp_path / "silent-manual", gap=1.0, voice=False, lines=("甲",))
    assert silent_manual[1][0]["end"] == pytest.approx(silent_auto[1][0]["end"])


def test_explicit_gap_keeps_a_one_frame_tail_sliver(tmp_path):
    (result, voices) = _render_job(tmp_path / "sliver", gap=0.10,
                                   candidate_seconds=0.60, sample_counts=(12001, 14400))
    pause=result[1][1]["start"] - (result[1][0]["start"] + voices[0]["samples"] / 24000)
    assert pause >= 0.10 - 1e-6
    assert pause <= 0.10 + 1 / 30 + 1e-6
    assert [c["frames"] for c in result[1][0]["cuts"]] == [19]


def test_last_sentence_and_mute_keep_legacy_sliver_behavior(tmp_path):
    (old, _) = _render_job(tmp_path / "last-old", gap=None, lines=("甲",),
                           candidate_seconds=0.90, sample_counts=(18000,))
    (manual, _) = _render_job(tmp_path / "last-manual", gap=1.0, lines=("甲",),
                              candidate_seconds=0.90, sample_counts=(18000,))
    assert round(old[1][0]["end"] * 30) == round(manual[1][0]["end"] * 30)
    (mute_old, _) = _render_job(tmp_path / "mute-old", gap=None, voice=False,
                                lines=("甲",), candidate_seconds=29 / 30)
    (mute_manual, _) = _render_job(tmp_path / "mute-manual", gap=1.0, voice=False,
                                   lines=("甲",), candidate_seconds=29 / 30)
    assert round(mute_old[1][0]["end"] * 30) == round(mute_manual[1][0]["end"] * 30)


def test_prefix_plan_matches_explicit_sliver_frame_plan(tmp_path):
    from workbench.selection import prefix_plan
    mapping=[[{"path":"clip.mp4","start":0.0,"seconds":0.60,"source_sha256":"x","index_id":"i"}],
             [{"path":"clip.mp4","start":0.0,"seconds":0.60,"source_sha256":"x","index_id":"i"}]]
    settings={"voice":True,"speed":1.0,"sentence_gap_seconds":0.10,"shot_reuse":0}
    plan=prefix_plan(mapping,["甲","乙"],settings,voices=[12001,14400],seconds=10)
    (rendered, voices)=_render_job(tmp_path / "plan",gap=0.10,candidate_seconds=0.60,
                                   sample_counts=(12001,14400))
    assert plan[0]["used_seconds"] == pytest.approx(19 / 30)
    assert rendered[1][0]["end"] == pytest.approx(plan[0]["used_seconds"])


def test_encode_phase_retry_passes_new_gap_only_when_explicit(tmp_path, monkeypatch):
    from workbench import renderer
    calls=[]
    def fake_mux(config,draft,master,phase,attempt,cancel):
        Path(attempt).mkdir(parents=True,exist_ok=True)
        return Path(attempt)/"final.mp4"
    def fake_verify(*args, **kwargs):
        calls.append(kwargs.copy())
        return {"ok":True,"checks":{"x":True}}
    monkeypatch.setattr(renderer,"_encode_mux",fake_mux)
    monkeypatch.setattr(renderer,"verify_media",fake_verify)
    cfg=type("Cfg",(),{"ffmpeg":"ffmpeg","ffprobe":"ffprobe","max_stage_seconds":120})()
    renderer.encode_with_phase_retry(cfg,Path("draft"),np.zeros(10),[],[],tmp_path/"old","voice")
    assert "sentence_gap_seconds" not in calls[-1]
    renderer.encode_with_phase_retry(cfg,Path("draft"),np.zeros(10),[],[],tmp_path/"new","voice",sentence_gap_seconds=0.73)
    assert calls[-1]["sentence_gap_seconds"] == 0.73


def test_service_save_reload_and_enqueue_freezes_per_product_gap(tmp_path, monkeypatch):
    from workbench.config import RuntimeConfig
    from workbench.queue import JobQueue
    from workbench.service import WorkbenchService
    from workbench.store import Store
    from workbench.media import sha256

    material=tmp_path/"materials";output=tmp_path/"output";material.mkdir();output.mkdir()
    source=material/"clip.mp4"
    subprocess.run(["ffmpeg","-nostdin","-y","-v","error","-f","lavfi","-i",
                    "color=c=black:s=1080x1920:r=30:d=2","-an","-c:v","libx264",
                    "-pix_fmt","yuv420p",str(source)],check=True)
    cfg=RuntimeConfig(data_root=str(tmp_path/"data"),nas_roots=[str(material)],project_src=str(tmp_path),
                      model_dir='',tts_model='',gpu_lock_path='')
    store=Store(tmp_path/"data"/"records.json");queue=JobQueue(tmp_path/"data"/"jobs.json")
    service=WorkbenchService(cfg,store,queue,start=False)
    a=service.create_product("A");b=service.create_product("B")
    service.select_path(a['id'],'source',material);service.select_path(a['id'],'output',output)
    service.select_path(b['id'],'source',material);service.select_path(b['id'],'output',output)
    base={"voice":False,"sentence_gap_seconds":0.73}
    settings_a=ProductionSettings.from_dict(base).snapshot();settings_b=ProductionSettings.from_dict({**base,"sentence_gap_seconds":0.10}).snapshot()
    store.set_settings(a['id'],settings_a);store.set_settings(b['id'],settings_b)
    store.update_product(a['id'],index_id='idx')
    assert Store(tmp_path/"data"/"records.json").get_product(a['id'])['settings']['sentence_gap_seconds']==0.73
    assert Store(tmp_path/"data"/"records.json").get_product(b['id'])['settings']['sentence_gap_seconds']==0.10
    candidate={"path":str(source),"start":0.0,"seconds":2.0,"source_sha256":sha256(source),"index_id":"idx"}
    rec={"id":"idx","product_id":a['id'],"source":str(material),"files":[],"count":1}
    monkeypatch.setattr(service,"_ready_index",lambda pid:(service.store.get_product(pid),rec))
    monkeypatch.setattr(service,"_retrieve_snapshot",lambda p,r,text:[{"sentence_id":0,"line":"甲","candidates":[candidate]}])
    monkeypatch.setattr(service,"_snapshot_fresh",lambda *args,**kwargs:True)
    monkeypatch.setattr(service,"_source_meta_matches",lambda *args,**kwargs:True)
    monkeypatch.setattr(service,"_source_fingerprints",lambda *args,**kwargs:[])
    job=service.enqueue(a['id'],text="甲",settings=settings_a)[0]
    store.set_settings(a['id'],ProductionSettings.from_dict({**base,"sentence_gap_seconds":1.0}).snapshot())
    frozen=queue.get(job['id'])['input']['settings']['sentence_gap_seconds']
    assert frozen==0.73
    assert store.get_product(b['id'])['settings']['sentence_gap_seconds']==0.10


def test_verify_explicit_gap_branch_reports_requested_and_actual(tmp_path, monkeypatch):
    from workbench import verify

    wavs = []
    for i in range(2):
        p = tmp_path / f"voice_{i:03d}.wav"
        _write_wav(p, 4800)
        wavs.append(p)
    timeline = [{"start": 0.0, "end": 0.9}, {"start": 1.0, "end": 2.0}]
    from workbench import verify
    pcm = np.zeros(round(2.0 * 24000), dtype=np.float64)
    pcm[:4800] = verify.read_wav(wavs[0])
    pcm[round(1 * 24000):round(1 * 24000) + 4800] = verify.read_wav(wavs[1])
    info = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080,
                          "height": 1920, "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
                          "duration": "2.0", "start_time": "0"},
                         {"codec_type": "audio", "codec_name": "aac", "sample_rate": "24000",
                          "channels": 1, "bit_rate": "128000", "duration": "2.0", "start_time": "0"}],
            "format": {"duration": "2.0"}, "packets": [{"size": 40000, "duration_time": "2.0"}]}
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
    path = tmp_path / "final.mp4"
    path.write_bytes(b"fixture")
    report = verify.verify_media(path, "ffmpeg", "ffprobe", timeline, expected=pcm,
                                 voice_wavs=wavs, mode="voice", sentence_gap_seconds=0.8)
    assert report["sentence_gap_seconds"] == 0.8
    assert report["sentence_pauses"]["values"] == pytest.approx([0.8])
    assert report["checks"]["sentence_pauses"] is True
    import workbench.media as media
    monkeypatch.setattr(media, "sha256", lambda p: "x")
    media.validate_report(report, path)
