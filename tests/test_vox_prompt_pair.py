"""Vox fixed prompt-pair cache contract (synthetic CPU fixtures only)."""
import hashlib
import json
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from workbench.voice_engines import VOX_PROMPT_PHRASE, VoiceEngineError
from workbench.voxcpm_worker import (
    _ensure_prompt_pair,
    prompt_periodicity_median,
)


def _wav(path: Path, *, seconds=2.0, kind="tone", freq=220.0):
    n = int(24000 * seconds)
    t = np.arange(n) / 24000.0
    if kind == "noise":
        rng = np.random.default_rng(7)
        x = rng.normal(0, 0.1, n)
    else:
        x = 0.16 * np.sin(2 * np.pi * freq * t)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
        f.writeframes(np.clip(x * 32767, -32767, 32767).astype("<i2").tobytes())


class Observer:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def final(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("synthetic non-natural stop")
        return {"natural_stop": True}


def _fake_model(calls, *, kind="tone"):
    class Model:
        sample_rate = 48000

        def generate(self, **kwargs):
            calls.append(dict(kwargs))
            n = 48000 * 2
            t = np.arange(n) / 48000.0
            if kind == "noise":
                return np.random.default_rng(len(calls)).normal(0, 0.1, n)
            return 0.16 * np.sin(2 * np.pi * (220.0 + len(calls)) * t)

    return Model()


def _setup(tmp_path, *, model_kind="tone"):
    cache = tmp_path / "cache"; cache.mkdir()
    model = tmp_path / "model"; model.mkdir()
    reference = tmp_path / "reference.wav"; _wav(reference)
    anchor = {"engine": "vox", "preset": "natural_female", "seed": 4101, "kind": "preset"}
    calls = []
    return cache, model, reference, anchor, calls, _fake_model(calls, kind=model_kind)


def test_cold_prompt_then_reuse_without_regeneration(tmp_path):
    cache, model, reference, anchor, calls, sdk = _setup(tmp_path)
    observer = Observer()
    with patch("workbench.voxcpm_worker._seed_all"), patch("workbench.voxcpm_worker._normalize_loudness"):
        first = _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
        second = _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
    assert first[0] == second[0]
    assert len(calls) == 1
    meta = json.loads((cache / "natural_female" / "prompt.json").read_text())
    assert meta["phrase"] == VOX_PROMPT_PHRASE
    assert meta["prompt_sha256"] == hashlib.sha256(first[0].read_bytes()).hexdigest()
    assert second[1]["cold_start"] is False


@pytest.mark.parametrize("mutation", ["json", "sha", "model", "revision"])
def test_invalid_prompt_cache_fails_closed(tmp_path, mutation):
    cache, model, reference, anchor, calls, sdk = _setup(tmp_path)
    observer = Observer()
    with patch("workbench.voxcpm_worker._seed_all"), patch("workbench.voxcpm_worker._normalize_loudness"):
        _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
    meta_path = cache / "natural_female" / "prompt.json"
    data = json.loads(meta_path.read_text())
    if mutation == "json": meta_path.write_text("{")
    elif mutation == "sha": data["prompt_sha256"] = "0" * 64; meta_path.write_text(json.dumps(data))
    elif mutation == "model": data["model"] = str(tmp_path / "other-model"); meta_path.write_text(json.dumps(data))
    else: data["model_revision"] = "old"; meta_path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="提示缓存"):
        _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")


def test_reference_sha_change_rebuilds_and_replaces_bytes(tmp_path):
    cache, model, reference, anchor, calls, sdk = _setup(tmp_path)
    observer = Observer()
    with patch("workbench.voxcpm_worker._seed_all"), patch("workbench.voxcpm_worker._normalize_loudness"):
        first = _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
    old = first[0].read_bytes()
    reference.write_bytes(reference.read_bytes() + b"changed")
    with patch("workbench.voxcpm_worker._seed_all"), patch("workbench.voxcpm_worker._normalize_loudness"):
        second = _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
    assert second[1]["rebuilt"] is True
    assert second[0].read_bytes() != old
    assert len(calls) == 2


def test_quality_gate_retries_seeds_and_raises_after_three_failures(tmp_path):
    cache, model, reference, anchor, calls, sdk = _setup(tmp_path, model_kind="noise")
    observer = Observer()
    seeded = []
    with patch("workbench.voxcpm_worker._seed_all", side_effect=lambda s: seeded.append(s)), patch("workbench.voxcpm_worker._normalize_loudness"):
        with pytest.raises(VoiceEngineError, match="示范录音生成未通过质检"):
            _ensure_prompt_pair(cache, "natural_female", model, sdk, observer, shutil.which("ffmpeg"), reference, anchor, "preset")
    assert seeded == [4101, 5204, 6307]
    assert len(calls) == 3


def test_periodicity_quality_gate_accepts_tone_and_rejects_noise(tmp_path):
    good = tmp_path / "good.wav"; bad = tmp_path / "bad.wav"
    _wav(good); _wav(bad, kind="noise")
    assert prompt_periodicity_median(good) >= 0.60
    assert prompt_periodicity_median(bad) < 0.60


def test_phrase_is_exact_contract_constant():
    assert VOX_PROMPT_PHRASE == "大家好，今天和你分享一个实用的小发现。"
