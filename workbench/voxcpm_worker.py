"""One-shot VoxCPM2 adapter.

This process is deliberately separate from the web service.  It loads only a
local, pinned VoxCPM2 snapshot, writes real PCM, and fails closed when the
upstream stop decision was not observed.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import traceback
import wave
from pathlib import Path

import numpy as np

from .browser import reject_links
from .tts_worker import apply_speed_wav, _normalize_loudness
from .pacing import trim_wav_edges
from .voice_engines import (VoiceEngineError, VOX_PRESETS, VOX_PROMPT_PHRASE,
                            anchor_metadata, prompt_metadata, preset,
                            resolve_clone_voice, validate_vox_voice)


MODEL_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"


class VoxStopObserver:
    """Observe upstream ``stop_head`` without changing its return value."""

    def __init__(self):
        self.invocations: list[dict] = []
        self._hook = None
        self._original = None

    def install(self, pipeline):
        core = getattr(pipeline, "tts_model", pipeline)
        stop_head = getattr(core, "stop_head", None)
        inference = getattr(core, "_inference", None)
        if stop_head is None or not callable(getattr(stop_head, "register_forward_hook", None)) or not callable(inference):
            raise RuntimeError("VoxCPM2 stop observer unavailable; refusing unverified termination")

        def hook(_module, _args, output):
            if not self.invocations:
                return None
            row = self.invocations[-1]
            row["executed"] = int(row.get("executed", 0)) + 1
            try:
                value = output.argmax(dim=-1)[0].detach().cpu().item()
                row["last_stop_flag"] = int(value)
            except Exception as exc:
                row["hook_error"] = str(exc)
            return None  # non-mutating hook: upstream output is untouched

        self._hook = stop_head.register_forward_hook(hook)
        self._original = inference

        def wrapped(*args, **kwargs):
            # _inference's public defaults are stable in the pinned SDK.  Read
            # positional values too so a test double exercises the same path.
            min_len = kwargs.get("min_len", args[4] if len(args) > 4 else 2)
            max_len = kwargs.get("max_len", args[5] if len(args) > 5 else 2000)
            row = {"min_len": int(min_len), "max_len": int(max_len), "executed": 0, "last_stop_flag": None}
            self.invocations.append(row)
            gen = inference(*args, **kwargs)
            try:
                while True:
                    yield next(gen)
            except StopIteration:
                executed = int(row.get("executed", 0))
                last = row.get("last_stop_flag")
                row["natural_stop"] = bool(last == 1 and executed > int(min_len) + 1 - 0 and executed <= int(max_len))
                # The upstream loop breaks at i > min_len.  A generator which
                # runs to max_len with stop=0 is limit exhaustion and is never
                # accepted; a stop on the final permitted step remains an
                # observed natural stop.
                row["limit_exhausted"] = bool(executed >= int(max_len) and not row["natural_stop"])
                raise
            except GeneratorExit:
                # next_and_close() closes the non-streaming generator after
                # its final yield, before Python emits StopIteration.
                executed = int(row.get("executed", 0)); last = row.get("last_stop_flag")
                row["natural_stop"] = bool(last == 1 and executed > int(min_len) + 1 and executed <= int(max_len))
                row["limit_exhausted"] = bool(executed >= int(max_len) and not row["natural_stop"])
                raise
            except BaseException:
                row["natural_stop"] = False
                row["limit_exhausted"] = False
                raise

        core._inference = wrapped

    def close(self):
        if self._hook is not None:
            try:
                self._hook.remove()
            except Exception:
                pass
            self._hook = None

    def final(self) -> dict:
        if not self.invocations:
            raise RuntimeError("VoxCPM2 did not expose an inference invocation")
        row = dict(self.invocations[-1])
        if row.get("hook_error"):
            raise RuntimeError("VoxCPM2 stop observation failed: " + row["hook_error"])
        if row.get("natural_stop") is not True:
            if row.get("limit_exhausted"):
                raise RuntimeError("VoxCPM2 reached its generation limit before natural stop")
            raise RuntimeError("VoxCPM2 did not provide a natural stop decision")
        return row


def _model_factory(model_path: Path):
    try:
        if importlib.metadata.version("voxcpm") != "2.0.3":
            raise RuntimeError("需要 voxcpm==2.0.3")
        from voxcpm import VoxCPM
    except Exception as exc:
        raise RuntimeError("VoxCPM 2.0.3 环境不可用，请运行可选安装程序") from exc
    # local_files_only and a local path make accidental downloads impossible.
    return VoxCPM.from_pretrained(
        str(model_path), load_denoiser=False, local_files_only=True,
        optimize=False, device="cuda",
    )


def _as_audio(result, model):
    rate = int(getattr(model, "sample_rate", 48000) or 48000)
    arr = result
    if isinstance(result, tuple) and len(result) == 2:
        arr, rate = result
    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim == 2:
        if 1 in arr.shape:
            arr = arr.reshape(-1)
        else:
            raise RuntimeError("VoxCPM 输出不是单声道")
    if arr.ndim != 1 or not len(arr) or not np.isfinite(arr).all():
        raise RuntimeError("VoxCPM 输出为空或含非有限采样")
    if int(rate) != 48000:
        raise RuntimeError(f"VoxCPM 输出采样率异常：{rate}Hz（应为 48000Hz）")
    peak = float(np.max(np.abs(arr))); rms = float(np.sqrt(np.mean(arr * arr)))
    if peak >= 1.0 or rms < 1e-5:
        raise RuntimeError(f"VoxCPM 输出电平异常：peak={peak:.4f}, rms={rms:.6f}")
    return arr, int(rate)


def _convert_48k_to_24k(arr, source_rate, dest: Path, ffmpeg: str):
    """Use ffmpeg for the contractual 48k float -> 24k PCM16 conversion."""
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    raw = dest.with_name(dest.name + ".f32.tmp")
    tmp = dest.with_name(dest.name + ".wav.tmp.wav")
    raw.write_bytes(np.asarray(arr, dtype="<f4").tobytes())
    try:
        cmd = [str(ffmpeg), "-nostdin", "-n", "-v", "error", "-f", "f32le", "-ar", str(source_rate), "-ac", "1", "-i", str(raw), "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode or not tmp.is_file():
            raise RuntimeError("VoxCPM 48k→24k 转换失败：" + (r.stderr or "")[-240:])
        with wave.open(str(tmp), "rb") as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 24000):
                raise RuntimeError("VoxCPM 转换后的 WAV 格式不正确")
            samples = f.getnframes()
            pcm = np.frombuffer(f.readframes(samples), dtype="<i2").copy()
        if not len(pcm) or not np.isfinite(pcm.astype(np.float64)).all():
            raise RuntimeError("VoxCPM 转换后的 WAV 为空或无效")
        with wave.open(str(dest), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(pcm.tobytes())
        return int(samples)
    finally:
        for p in (raw, tmp):
            try: p.unlink()
            except OSError: pass


def _check_pcm16(path: Path):
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 24000):
            raise RuntimeError("Vox 输出 WAV 必须是 24kHz 单声道 PCM16")
        arr = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    if arr.ndim != 1 or not len(arr) or not np.isfinite(arr).all(): raise RuntimeError("Vox 输出采样无效")
    peak = float(np.max(np.abs(arr))); rms = float(np.sqrt(np.mean(arr * arr)))
    if peak >= 1.0 or rms < 1e-5: raise RuntimeError(f"Vox 归一化后电平异常：peak={peak:.4f}, rms={rms:.6f}")
    return len(arr)


def prompt_periodicity_median(path: Path, *, sample_rate: int = 24000) -> float:
    """Median voiced-frame periodicity used by the fixed prompt quality gate."""
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, sample_rate):
            raise RuntimeError("Vox 提示 WAV 必须是 24kHz 单声道 PCM16")
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    frame_len, hop, nfft = 1024, 256, 4096
    if len(pcm) < frame_len:
        return 0.0
    window = np.hanning(frame_len)
    ratios = []
    for start in range(0, len(pcm) - frame_len + 1, hop):
        z = pcm[start:start + frame_len]
        if float(np.sqrt(np.mean(z * z))) < 0.02:
            continue
        z = z * window
        ac = np.fft.irfft(np.fft.rfft(z, nfft) * np.conjugate(np.fft.rfft(z, nfft)), nfft)[:2048]
        if not len(ac) or ac[0] <= 0:
            continue
        lo, hi = max(1, int(sample_rate / 400)), min(2047, int(sample_rate / 70))
        peak = lo + int(np.argmax(ac[lo:hi + 1]))
        ratio = float(ac[peak] / ac[0])
        if ratio > 0.3:
            ratios.append(ratio)
    return float(np.median(ratios)) if ratios else 0.0


def _prompt_error(message="Vox 提示缓存校验失败，请清理该音色的提示缓存后重试"):
    raise RuntimeError(message)


def _validate_prompt(cache_root: Path, voice: str, model: Path, reference_wav: Path,
                     anchor_meta: dict, kind: str):
    folder = cache_root / voice
    wav = folder / "prompt.wav"
    meta = folder / "prompt.json"
    if not wav.exists() and not meta.exists():
        return None
    try:
        reject_links(folder); reject_links(wav); reject_links(meta)
    except Exception as exc:
        raise RuntimeError("Vox 提示缓存校验失败，请清理该音色的提示缓存后重试") from exc
    if not wav.is_file() or not meta.is_file():
        _prompt_error()
    try:
        data = json.loads(meta.read_text(encoding="utf8"))
    except Exception as exc:
        raise RuntimeError("Vox 提示缓存校验失败，请清理该音色的提示缓存后重试") from exc
    required = {"engine", "preset", "model", "model_revision", "kind", "seed",
                "phrase", "source_sha256", "prompt_sha256"}
    base = int(anchor_meta["seed"])
    valid_seeds = {base, base + 1103, base + 2206}
    model_value = data.get("model") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or not required.issubset(data) or
            data.get("engine") != "vox" or data.get("preset") != voice or
            data.get("model_revision") != MODEL_REVISION or data.get("kind") != kind or
            data.get("phrase") != VOX_PROMPT_PHRASE or
            type(data.get("seed")) is not int or data.get("seed") not in valid_seeds or
            not isinstance(model_value, str) or
            str(Path(model_value).absolute()) != str(model.absolute())):
        _prompt_error()
    try:
        prompt_hash = hashlib.sha256(wav.read_bytes()).hexdigest()
        source_hash = hashlib.sha256(reference_wav.read_bytes()).hexdigest()
    except Exception as exc:
        raise RuntimeError("Vox 提示缓存校验失败，请清理该音色的提示缓存后重试") from exc
    if prompt_hash != data.get("prompt_sha256"):
        _prompt_error()
    # A changed reference is the one supported reason to rebuild an existing
    # prompt pair; all other identity drift fails closed above.
    return wav, data, source_hash != data.get("source_sha256")


def _write_prompt(cache_root: Path, voice: str, model: Path, model_obj, observer,
                  ffmpeg: str, reference_wav: Path, anchor_meta: dict, kind: str):
    folder = cache_root / voice
    folder.mkdir(parents=True, exist_ok=True); reject_links(folder)
    target = folder / "prompt.wav"
    source_hash = hashlib.sha256(reference_wav.read_bytes()).hexdigest()
    base = int(anchor_meta["seed"])
    seeds = [base, base + 1103, base + 2206]
    attempts = 0
    with tempfile.TemporaryDirectory(dir=str(cache_root)) as td:
        for seed in seeds:
            attempts += 1
            temp = Path(td) / f"prompt-{attempts}.wav"
            try:
                _seed_all(seed)
                generated = model_obj.generate(
                    text=VOX_PROMPT_PHRASE,
                    reference_wav_path=str(reference_wav),
                    normalize=False, denoise=False,
                )
                stop = observer.final()
                if isinstance(stop, dict) and stop.get("natural_stop") is not True:
                    raise RuntimeError("VoxCPM2 did not provide a natural stop decision")
                arr, rate = _as_audio(generated, model_obj)
                _convert_48k_to_24k(arr, rate, temp, ffmpeg)
                receipt = {"voices": [{"file": temp.name, "samples": 0, "sha256": ""}]}
                _normalize_loudness(ffmpeg, temp.parent, receipt)
                if receipt.get("loudness_warning"):
                    raise RuntimeError(receipt["loudness_warning"])
                samples = _check_pcm16(temp)
                seconds = samples / 24000.0
                if not 1.6 <= seconds <= 6.5 or prompt_periodicity_median(temp) < 0.60:
                    raise RuntimeError("Vox 提示音频未通过时长或周期度质检")
                os.replace(temp, target)
                reject_links(target)
                metadata = prompt_metadata(
                    voice, str(model), target, seed=seed, phrase=VOX_PROMPT_PHRASE,
                    source_sha256=source_hash, kind=kind,
                )
                metadata_tmp = folder / ("prompt.json.tmp-" + next(tempfile._get_candidate_names()))
                metadata_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf8")
                os.replace(metadata_tmp, folder / "prompt.json")
                reject_links(folder / "prompt.json")
                return target, metadata, attempts
            except Exception:
                try: temp.unlink()
                except OSError: pass
                continue
    raise VoiceEngineError("该音色的示范录音生成未通过质检，请更换或重新放入更清晰的参考音频后再试")


def _ensure_prompt_pair(cache_root: Path, voice: str, model: Path, model_obj, observer,
                        ffmpeg: str, reference_wav: Path, anchor_meta: dict, kind: str):
    """Validate or cold-build the fixed prompt pair for one voice."""
    found = _validate_prompt(cache_root, voice, model, reference_wav, anchor_meta, kind)
    if found is not None:
        wav, metadata, source_changed = found
        if not source_changed:
            return wav, {"path": str(wav), "phrase": VOX_PROMPT_PHRASE,
                         "sha256": metadata["prompt_sha256"],
                         "source_sha256": metadata["source_sha256"],
                         "cold_start": False, "rebuilt": False, "attempts": 0,
                         "seed": metadata["seed"]}
        _, metadata, attempts = _write_prompt(cache_root, voice, model, model_obj, observer,
                                               ffmpeg, reference_wav, anchor_meta, kind)
        wav = cache_root / voice / "prompt.wav"
        return wav, {"path": str(wav), "phrase": VOX_PROMPT_PHRASE,
                     "sha256": metadata["prompt_sha256"],
                     "source_sha256": metadata["source_sha256"],
                     "cold_start": False, "rebuilt": True, "attempts": attempts,
                     "seed": metadata["seed"]}
    _, metadata, attempts = _write_prompt(cache_root, voice, model, model_obj, observer,
                                          ffmpeg, reference_wav, anchor_meta, kind)
    wav = cache_root / voice / "prompt.wav"
    return wav, {"path": str(wav), "phrase": VOX_PROMPT_PHRASE,
                 "sha256": metadata["prompt_sha256"],
                 "source_sha256": metadata["source_sha256"],
                 "cold_start": True, "rebuilt": False, "attempts": attempts,
                 "seed": metadata["seed"]}


def _validate_anchor(cache_root: Path, voice: str, model: Path):
    p = preset(voice); folder = cache_root / voice; wav = folder / "anchor.wav"; meta = folder / "anchor.json"
    if not wav.is_file() or not meta.is_file(): return None
    reject_links(wav); reject_links(meta)
    try: data = json.loads(meta.read_text(encoding="utf8"))
    except Exception as exc: raise RuntimeError("Vox 预设缓存元数据损坏，请清理该预设后重试") from exc
    required = {"engine", "preset", "model", "model_revision", "seed", "description", "anchor_sha256"}
    if (not required.issubset(data) or data.get("engine") != "vox" or
            data.get("preset") != voice or data.get("model_revision") != MODEL_REVISION or
            data.get("seed") != p["seed"] or data.get("description") != p["description"]):
        raise RuntimeError("Vox 预设缓存身份不匹配，已拒绝使用")
    if str(Path(data.get("model", "")).absolute()) != str(model.absolute()):
        raise RuntimeError("Vox 预设缓存对应的模型已变化，请重新生成")
    got = hashlib.sha256(wav.read_bytes()).hexdigest()
    if got != data.get("anchor_sha256"):
        raise RuntimeError("Vox 预设缓存校验失败，请重新生成")
    return wav, data


def _seed_all(seed):
    # The pinned VoxCPM.generate API has no seed keyword. Seed its torch RNG
    # outside the call rather than inventing an SDK argument.
    import torch
    torch.manual_seed(int(seed))


def _write_anchor(cache_root, voice, model, model_obj, observer, ffmpeg):
    p = preset(voice); folder = cache_root / voice; folder.mkdir(parents=True, exist_ok=True); reject_links(folder)
    target = folder / "anchor.wav"
    if target.exists():
        found = _validate_anchor(cache_root, voice, model)
        if found: return found
        raise RuntimeError("Vox 预设缓存已存在但无效，拒绝覆盖原始材料")
    with tempfile.TemporaryDirectory(dir=str(cache_root)) as td:
        temp = Path(td) / "anchor.wav"
        _seed_all(p["seed"])
        result = model_obj.generate(text="(" + p["description"] + ")" + p["anchor"], reference_wav_path=None, normalize=False, denoise=False)
        row = observer.final()
        arr, rate = _as_audio(result, model_obj)
        _convert_48k_to_24k(arr, rate, temp, ffmpeg)
        receipt = {"voices": [{"file": temp.name, "samples": 1, "sha256": ""}]}
        _normalize_loudness(ffmpeg, temp.parent, receipt)
        if receipt.get("loudness_warning"): raise RuntimeError("Vox 预设归一化失败：" + receipt["loudness_warning"])
        _check_pcm16(temp)
        # The helper expects a named file in its output folder; rename the
        # normalized temporary WAV atomically into the preset cache.
        os.replace(temp, target)
    meta = anchor_metadata(voice, str(model), target, seed=p["seed"], description=p["description"])
    mt = folder / ("anchor.json.tmp-" + next(tempfile._get_candidate_names()))
    mt.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf8"); os.replace(mt, folder / "anchor.json")
    return target, meta


def _prepare_clone_reference(src: Path, cache_root: Path, ffmpeg: str) -> Path:
    """Reference WAV for a dropped clone voice.

    True WAV files are used as-is (bytes never modified).  Other formats are
    transcoded once into the cache, keyed by the source bytes, so the model
    always sees a plain mono PCM WAV and the dropped file stays untouched.
    """
    if src.suffix.lower() == ".wav":
        return src
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    folder = cache_root / "_imports"; folder.mkdir(parents=True, exist_ok=True); reject_links(folder)
    dest = folder / f"{src.stem}-{digest}.wav"
    if dest.is_file():
        return dest
    tmp = dest.with_name(dest.name + ".tmp.wav")
    try:
        cmd = [str(ffmpeg), "-nostdin", "-y", "-v", "error", "-i", str(src),
               "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode or not tmp.is_file():
            raise RuntimeError("克隆音频转码失败：" + (r.stderr or "")[-240:])
        _check_pcm16(tmp)
        os.replace(tmp, dest)
        return dest
    finally:
        try: tmp.unlink()
        except OSError: pass


def _clone_seed(voice: str) -> int:
    return int(hashlib.sha256(("clone:" + voice).encode("utf8")).hexdigest()[:8], 16)


def synthesize(request: dict, out_dir: str | Path, *, model_factory=None) -> dict:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True); reject_links(out)
    clone_dir_raw = str(request.get("vox_clone_dir") or "").strip()
    clone_dir = Path(clone_dir_raw) if clone_dir_raw else None
    voice = validate_vox_voice(request.get("voice", request.get("vox_voice")), clone_dir)
    lines = request.get("lines") or [request.get("text", "")]
    if not isinstance(lines, list) or not lines or any(not isinstance(x, str) or not x.strip() for x in lines):
        raise VoiceEngineError("Vox 配音文字为空")
    speed = float(request.get("speed", 1.0));
    if not 0.85 <= speed <= 1.5: raise VoiceEngineError("语速需在 0.85–1.5 之间")
    model_raw = str(request.get("voxcpm_model") or "").strip(); cache_raw = str(request.get("voxcpm_cache") or "").strip()
    if not model_raw: raise VoiceEngineError("Vox 模型未就绪，请先运行可选的 install-vox.ps1")
    if not cache_raw: raise VoiceEngineError("Vox 缓存目录未配置")
    model_path = Path(model_raw); cache_root = Path(cache_raw); ffmpeg = str(request.get("ffmpeg") or "ffmpeg")
    if not model_path.is_dir(): raise VoiceEngineError("Vox 模型未就绪，请先运行可选的 install-vox.ps1")
    reject_links(model_path); reject_links(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True); reject_links(cache_root)
    factory = model_factory or (lambda: _model_factory(model_path))
    model = factory(); observer = VoxStopObserver(); observer.install(model)
    try:
        if voice in VOX_PRESETS:
            anchor = _validate_anchor(cache_root, voice, model_path)
            anchor_created = anchor is None
            if anchor is None:
                anchor = _write_anchor(cache_root, voice, model_path, model, observer, ffmpeg)
            reference_wav, anchor_meta = anchor
        else:
            clone_src = resolve_clone_voice(clone_dir, voice)
            if clone_src is None:
                raise VoiceEngineError("Vox 克隆音色不可用：请把音频放进克隆文件夹后刷新列表")
            reference_wav = _prepare_clone_reference(clone_src, cache_root, ffmpeg)
            anchor_meta = {"engine": "vox", "preset": voice, "kind": "clone",
                           "seed": _clone_seed(voice), "source_file": clone_src.name,
                           "source_sha256": hashlib.sha256(clone_src.read_bytes()).hexdigest(),
                           "reference": str(reference_wav)}
            anchor_created = False
        prompt_wav, prompt_info = _ensure_prompt_pair(
            cache_root, voice, model_path, model, observer, ffmpeg,
            reference_wav, anchor_meta, "preset" if voice in VOX_PRESETS else "clone",
        )
        result = {"success": False, "engine": "vox", "preset": voice, "completion": "natural_stop", "anchor": anchor_meta, "anchor_cold_start": anchor_created, "prompt": prompt_info, "voices": [], "speed": speed}
        for i, line in enumerate(lines):
            before = len(observer.invocations)
            _seed_all(anchor_meta["seed"])
            generated = model.generate(
                text=line, reference_wav_path=str(reference_wav),
                prompt_wav_path=str(prompt_wav), prompt_text=VOX_PROMPT_PHRASE,
                normalize=False, denoise=False,
            )
            stop = observer.final()
            attempts = observer.invocations[before:]
            if not attempts:
                raise RuntimeError(f"Vox 停止观察缺少句子调用：sentence={i}")
            arr, rate = _as_audio(generated, model)
            dest = out / f"voice_{i:03d}.wav"
            if dest.exists(): raise RuntimeError(f"voice output already exists: {dest.name}")
            _convert_48k_to_24k(arr, rate, dest, ffmpeg)
            if abs(speed - 1.0) > 1e-9:
                apply_speed_wav(ffmpeg, dest, speed)
            receipt = {"voices": [{"file": dest.name, "samples": 0, "sha256": ""}]}
            _normalize_loudness(ffmpeg, out, receipt)
            if receipt.get("loudness_warning"): raise RuntimeError("Vox 音量归一化失败：" + receipt["loudness_warning"])
            trim_wav_edges(dest)
            samples = _check_pcm16(dest)
            if samples <= 0: raise RuntimeError(f"Vox 输出为空：sentence={i}")
            result["voices"].append({"index": i, "file": dest.name, "samples": samples, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "eos": {"engine": "vox", "natural_stop": True, "observed_natural_stop": True, "min_len": stop["min_len"], "max_len": stop["max_len"], "executed": stop["executed"], "last_stop_flag": stop["last_stop_flag"], "limit_exhausted": False, "attempts": len(attempts)}, "completion": "natural_stop"})
        result["success"] = True
        return result
    finally:
        observer.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    result = {"success": False, "engine": "vox", "voices": []}
    try:
        req = json.loads(Path(argv[0]).read_text(encoding="utf8")); result = synthesize(req, argv[1])
    except Exception as exc:
        result.update(error=str(exc), traceback=traceback.format_exc())
    Path(argv[1]).mkdir(parents=True, exist_ok=True)
    (Path(argv[1]) / "voice-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")
    return 0 if result.get("success") else 1


if __name__ == "__main__": sys.exit(main())
