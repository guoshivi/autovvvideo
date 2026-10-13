"""Small, explicit voice-engine boundary shared by jobs and previews.

The module intentionally contains no model imports.  That keeps the Mac
workbench importable without CUDA, while making the selected engine a required
part of every immutable request.
"""
from __future__ import annotations

import hashlib
import stat
import uuid
from pathlib import Path


VOICE_ENGINES = {"qwen", "vox"}
QWEN_SPEAKERS = {"Serena", "Vivian", "Uncle_Fu"}
VOX_PRESETS = {
    "natural_female": {
        "label": "自然分享女声",
        "description": "自然分享女声：像熟人聊天，轻松、亲近，适合日常种草。",
        "anchor": "大家好，今天用很轻松的方式，和你分享一个实用的小发现。",
        "seed": 4101,
    },
    "bright_female": {
        "label": "清晰活力女声",
        "description": "清晰活力女声：明快有精神但不喊叫，适合卖点介绍。",
        "anchor": "这个小细节很值得注意，清楚看一遍，你马上就能用起来。",
        "seed": 4102,
    },
    "calm_male": {
        "label": "沉稳成熟男声",
        "description": "沉稳成熟男声：语气平稳、可靠，适合说明和测评。",
        "anchor": "先把重点说清楚，再看它在真实使用里能解决什么问题。",
        "seed": 4103,
    },
    "story_female": {
        "label": "温暖叙事女声",
        "description": "温暖叙事女声：柔和有画面感，适合故事和生活方式内容。",
        "anchor": "慢一点听，你会发现这个平常的小场景，也有自己的温度。",
        "seed": 4104,
    },
}


class VoiceEngineError(ValueError):
    pass


def validate_engine(value: object) -> str:
    if not isinstance(value, str) or value not in VOICE_ENGINES:
        raise VoiceEngineError("配音引擎只能选择 Qwen（原配音）或 Vox（新配音·试用）")
    return value


def validate_vox_voice(value: object) -> str:
    if not isinstance(value, str) or value not in VOX_PRESETS:
        raise VoiceEngineError("Vox 声音预设不可用，请重新选择")
    return value


def preset(voice: object) -> dict:
    voice = validate_vox_voice(voice)
    return dict(VOX_PRESETS[voice])


def _reject_link(path: Path) -> Path:
    p = path.absolute()
    for item in (p, *p.parents):
        try:
            mode = item.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or getattr(item.lstat(), "st_file_attributes", 0) & 0x400:
            raise VoiceEngineError("配音目录不能使用符号链接或重解析点")
    return p


def require_vox_config(cfg) -> tuple[Path, Path, Path]:
    """Fail before enqueue/preview if the optional environment is absent."""
    py = str(getattr(cfg, "voxcpm_python", "") or "").strip()
    model = str(getattr(cfg, "voxcpm_model", "") or "").strip()
    cache = str(getattr(cfg, "voxcpm_cache", "") or "").strip()
    if not py or not Path(py).is_file():
        raise VoiceEngineError("Vox 试用环境未安装，请先运行可选的 install-vox.ps1")
    if not model or not Path(model).is_dir():
        raise VoiceEngineError("Vox 模型未就绪，请先运行可选的 install-vox.ps1")
    if not cache:
        raise VoiceEngineError("Vox 缓存目录未配置，请联系管理员完成试用配置")
    _reject_link(Path(py)); _reject_link(Path(model)); cache_path = _reject_link(Path(cache))
    return Path(py), Path(model), cache_path


def require_qwen_config(cfg) -> tuple[Path, Path]:
    py = str(getattr(cfg, "tts_python", "") or "").strip()
    model = str(getattr(cfg, "tts_model", "") or "").strip()
    if not py or not Path(py).is_file():
        raise VoiceEngineError("Qwen 配音环境未就绪，请检查原配音配置")
    if not model or not Path(model).is_dir():
        raise VoiceEngineError("Qwen 配音模型未就绪，请检查原配音配置")
    _reject_link(Path(py)); _reject_link(Path(model))
    return Path(py), Path(model)


def validate_preview_payload(body: object) -> dict:
    if not isinstance(body, dict):
        raise VoiceEngineError("预览请求格式无效")
    if set(body) - {"engine", "voice", "text", "speed"}:
        raise VoiceEngineError("预览请求只支持引擎、声音、文字和语速")
    engine = validate_engine(body.get("engine", "qwen"))
    voice = body.get("voice", "natural_female" if engine == "vox" else "Serena")
    if engine == "vox":
        voice = validate_vox_voice(voice)
    elif not isinstance(voice, str) or voice not in QWEN_SPEAKERS:
        raise VoiceEngineError("Qwen 说话人无效")
    text = body.get("text", "")
    if not isinstance(text, str):
        raise VoiceEngineError("预览文字无效")
    text = " ".join(text.split()).strip()
    if not text:
        text = "这是当前配音的试听片段，确认声音后再开始出片。"
    if len(text) > 240:
        text = text[:240]
    speed = body.get("speed", 1.0)
    if type(speed) not in (int, float) or not 0.85 <= float(speed) <= 1.5:
        raise VoiceEngineError("语速需在 0.85–1.5 之间")
    return {"engine": engine, "voice": voice, "text": text, "speed": float(speed)}


def anchor_metadata(voice: str, model: str, wav: Path, *, seed: int, description: str) -> dict:
    return {
        "engine": "vox", "preset": validate_vox_voice(voice), "model": str(model),
        "model_revision": "32279effe8c19989596f05d353d1447f51d9e915",
        "seed": int(seed), "description": str(description),
        "anchor_sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
    }


def opaque_preview_id() -> str:
    return uuid.uuid4().hex
