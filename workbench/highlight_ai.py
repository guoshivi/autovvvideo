"""Ask the configured chat model which words deserve emphasis.

The answer is advisory: every word is validated against the sentence before it
can reach the subtitle renderer.  Any transport/parse failure returns an empty
result so the job falls back to the deterministic rules.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import aicopy

_MAX_PER_LINE = 2
_MAX_WORD = 12

_SYSTEM = (
    '你是短视频字幕编辑。下面按行给你口播文案，每行一句。'
    '请为每一句挑出 1 到 2 个最值得在字幕里强调的词。'
    '要求：1) 必须原样摘自该句，不能改写、不能拼接；2) 优先价格数字、产品名、真假/防伪、关键动作；'
    '3) 不要选医疗、功效、疾病、身体症状类词；4) 每句最多两个。'
    '只输出 JSON，键是行号（从 1 开始），值是词语数组，例如 {"1":["分辨真假"],"2":["99块9"]}；'
    '不要 Markdown、不要解释。'
)


def credentials(config):
    base = getattr(config, 'ai_base_url', '') or 'https://api.deepseek.com'
    key = getattr(config, 'ai_api_key', '') or ''
    model = getattr(config, 'ai_model', '') or 'deepseek-chat'
    path = Path(getattr(config, 'data_root', '') or '.') / 'ai-config.json'
    if path.is_file():
        try:
            override = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            override = {}
        base = override.get('base_url') or base
        key = override.get('api_key') or key
        model = override.get('model') or model
    return base, key, model


def _parse_json(text: str):
    value = str(text or '').strip()
    if value.startswith('```'):
        value = re.sub(r'^```[a-zA-Z]*\s*', '', value)
        value = re.sub(r'\s*```$', '', value)
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(value[start:end + 1])
    except ValueError:
        return None


def validate(lines, data):
    """Keep only words that really occur in their own line."""
    if not isinstance(data, dict):
        return []
    result = []
    for index, line in enumerate(lines, start=1):
        raw = data.get(str(index)) or data.get(index) or []
        if not isinstance(raw, list):
            raw = []
        picked = []
        for word in raw:
            if not isinstance(word, str):
                continue
            word = word.strip()
            if not word or len(word) > _MAX_WORD or word not in line:
                continue
            if any(hit in word for hit in aicopy._REWRITE_BANNED):
                continue
            if any(word in other or other in word for other in picked):
                continue
            picked.append(word)
            if len(picked) >= _MAX_PER_LINE:
                break
        result.append(picked)
    return result


def select(lines, name, base_url, api_key, model, timeout=60):
    """Return one list of highlight words per line (possibly empty lists)."""
    lines = [str(line or '') for line in (lines or [])]
    if not lines or not api_key:
        return []
    numbered = '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, start=1))
    user = (f'产品名：{name or "未提供"}\n文案：\n{numbered}\n\n请按 JSON 返回每句的重点词。')
    payload = {
        'model': str(model or 'deepseek-chat'),
        'messages': [{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': user}],
        'temperature': 0.2,
        'max_tokens': 800,
        'stream': False,
    }
    try:
        url = aicopy.normalize_endpoint(base_url, True)
        data = aicopy._post(url, payload, api_key, timeout=timeout)
        choice = data['choices'][0]
        if 'finish_reason' in choice and choice['finish_reason'] != 'stop':
            return []
        text = aicopy.extract_text(data)
    except Exception:
        return []
    return validate(lines, _parse_json(text))
