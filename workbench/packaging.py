"""Ask the model for one packaging plan: highlights, stickers and sound cues.

The model chooses semantic slots from a fixed menu, never concrete files.  The
server validates every slot, caps density and freezes the plan into the job so a
re-render stays identical.  Unusable answers fall back to "no sticker / no sfx".
"""
from __future__ import annotations

import json
import math
import re

from . import aicopy, highlight_ai

ICONS = ('check', 'cross', 'question', 'warning', 'cry', 'money', 'fire', 'sparkle', 'arrow', 'box')
SOUNDS = ('ding', 'pop', 'whoosh', 'click', 'coin', 'success')

_SYSTEM = (
    '你是短视频包装编辑。给你按行编号的口播文案，请一次给出三样东西：'
    '1) 每句 1-2 个值得在字幕里变色的重点词；2) 最多每 3 句 1 个贴图；3) 与贴图同帧的音效。'
    '贴图只能从这些类型里选：check(对/正确)、cross(错/不要)、question(疑问)、warning(注意)、cry(糟糕)、'
    'money(价格/划算)、fire(热门)、sparkle(亮点)、arrow(指引)、box(产品/开箱)。'
    '音效只能从这些类型里选：ding、pop、whoosh、click、coin、success。'
    '贴图不要每句都加，只在语义真的匹配时加；医疗、功效、身体症状的句子不许配 money/fire/sparkle 正向贴图。'
    '重点词必须原样摘自该句，不得选医疗功效词。'
    '只输出 JSON：{"highlights":{"1":["词"]},"stickers":[{"line":2,"icon":"check","at":"start","side":"right"}],'
    '"sfx":[{"line":2,"sound":"ding","gain":-6}]}。不要 Markdown、不要解释。'
)


def _int(value):
    return value if type(value) is int else None


def _validate_stickers(lines, raw):
    count = len(lines)
    cap = max(1, math.ceil(count / 3))
    seen, rows = set(), []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        line = _int(item.get('line'))
        icon = item.get('icon')
        if line is None or not 1 <= line <= count or icon not in ICONS or line in seen:
            continue
        at = item.get('at') if item.get('at') in ('start', 'end') else 'start'
        side = item.get('side') if item.get('side') in ('left', 'right') else 'right'
        seen.add(line)
        rows.append({'line': line, 'icon': icon, 'at': at, 'side': side})
        if len(rows) >= cap:
            break
    return rows


def _validate_sfx(lines, raw, sticker_lines):
    count = len(lines)
    seen, rows = set(), []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        line = _int(item.get('line'))
        sound = item.get('sound')
        if line is None or not 1 <= line <= count or sound not in SOUNDS or line in seen:
            continue
        # A sound only makes sense under a sticker or on a number/price beat.
        if line not in sticker_lines and not re.search(r'\d', lines[line - 1]):
            continue
        try:
            gain = float(item.get('gain', -6))
        except (TypeError, ValueError):
            gain = -6.0
        gain = max(-12.0, min(-3.0, gain))
        seen.add(line)
        rows.append({'line': line, 'sound': sound, 'gain': round(gain, 1)})
    return rows


def validate(lines, data):
    if not isinstance(data, dict):
        return {'highlights': [[] for _ in lines], 'stickers': [], 'sfx': []}
    highlights = highlight_ai.validate(lines, data.get('highlights'))
    stickers = _validate_stickers(lines, data.get('stickers'))
    sfx = _validate_sfx(lines, data.get('sfx'), {row['line'] for row in stickers})
    return {'highlights': highlights, 'stickers': stickers, 'sfx': sfx}


def _parse(text):
    value = str(text or '').strip()
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(value[start:end + 1])
    except ValueError:
        return None


def plan(lines, name, base_url, api_key, model, timeout=60, want_highlights=True, want_stickers=True, want_sfx=True):
    lines = [str(line or '') for line in (lines or [])]
    if not lines or not api_key:
        return validate(lines, {})
    numbered = '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, start=1))
    asks = []
    if want_highlights: asks.append('重点词')
    if want_stickers: asks.append('贴图')
    if want_sfx: asks.append('音效')
    if not asks:
        return validate(lines, {})
    user = f'产品名：{name or "未提供"}\n文案：\n{numbered}\n\n本次只需要：{"、".join(asks)}。请按 JSON 返回。'
    payload = {
        'model': str(model or 'deepseek-chat'),
        'messages': [{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': user}],
        'temperature': 0.3,
        'max_tokens': 1200,
        'stream': False,
    }
    try:
        url = aicopy.normalize_endpoint(base_url, True)
        data = aicopy._post(url, payload, api_key, timeout=timeout)
        choice = data['choices'][0]
        if 'finish_reason' in choice and choice['finish_reason'] != 'stop':
            return validate(lines, {})
        text = aicopy.extract_text(data)
    except Exception:
        return validate(lines, {})
    plan = validate(lines, _parse(text))
    if not want_highlights: plan['highlights'] = [[] for _ in lines]
    if not want_stickers: plan['stickers'] = []
    if not want_sfx: plan['sfx'] = []
    return plan
