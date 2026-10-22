"""Ask the model for one packaging plan: highlights, stickers and sound cues.

The model chooses semantic slots from a fixed menu, never concrete files.  The
server validates every slot, caps density and freezes the plan into the job so a
re-render stays identical.  Unusable answers fall back to "no sticker / no sfx".
"""
from __future__ import annotations

import json
import math
import re

from . import aicopy, flower_text, highlight_ai

ICON_MEANINGS = {
    'check': '对/正确', 'cross': '错/不要', 'question': '疑问', 'warning': '注意',
    'cry': '糟糕', 'money': '价格/划算', 'fire': '热门', 'sparkle': '亮点',
    'arrow': '指引', 'box': '产品/开箱',
    'heart': '喜欢/心动', 'star': '好评/推荐', 'clock': '时间/限时', 'gift': '送礼/心意',
    'cart': '下单/购买', 'truck': '发货/物流', 'home': '家庭/居家', 'leaf': '天然/草本',
    'sun': '日常/好天气', 'shield': '防护/保护', 'muscle': '结实/有力', 'clap': '叫好/认可',
    'eyes': '注意/看这里', 'bulb': '提示/妙招', 'party': '庆祝/节日', 'phone': '咨询/联系',
}
ICONS = tuple(ICON_MEANINGS)
SOUNDS = ('ding', 'pop', 'whoosh', 'click', 'coin', 'success')

_SYSTEM = (
    '你是短视频包装编辑。给你按行编号的口播文案，请一次给出三样东西：'
    '1) 每句 1-2 个值得在字幕里变色的重点词；2) 最多每 3 句 1 个贴图；3) 与贴图同帧的音效。'
    '贴图只能从这些类型里选：' + '、'.join(f'{k}({v})' for k, v in ICON_MEANINGS.items()) + '。'
    '音效只能从这些类型里选：ding、pop、whoosh、click、coin、success。'
    '贴图不要每句都加，只在语义真的匹配时加；医疗、功效、身体症状的句子不许配庆祝/正向类贴图（money/fire/sparkle/star/heart/party/clap/muscle）。'
    '重点词必须原样摘自该句，不得选医疗功效词。'
    '另外给 0-2 个花字：从该句原样摘 2-6 个字（不能带标点），'
    '花字风格只能从这些里选：' + '、'.join(f'{k}({v})' for k, v in flower_text.STYLE_MEANINGS.items()) + '。'
    '花字最多每 4 句 1 个，医疗、功效、身体症状的句子不许配花字。'
    '只输出 JSON：{"highlights":{"1":["词"]},"stickers":[{"line":2,"icon":"check","at":"start","side":"right"}],'
    '"sfx":[{"line":2,"sound":"ding","gain":-6}],"flower":[{"line":2,"text":"中老年人","style":"burst_red","side":"right"}]}。不要 Markdown、不要解释。'
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


import unicodedata

def _is_punct(ch):
    return unicodedata.category(ch).startswith('P') or ch == '～'

_FLOWER_BANNED = ('治疗', '缓解', '止痛', '消炎', '活血', '活络', '通络', '药酒', '药油', '膏药', '药方', '偏方',
                  '关节', '血压', '血糖', '疼痛', '病症', '疾病', '癌', '炎', '痛', '病', '酸', '胀', '麻', '肿')


def _validate_flower(lines, raw):
    count = len(lines)
    cap = max(1, count // 4)
    seen, rows = set(), []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        line = _int(item.get('line'))
        text = item.get('text')
        style = item.get('style')
        if line is None or not 1 <= line <= count or line in seen:
            continue
        if not isinstance(text, str):
            continue
        text = text.strip()
        if not 2 <= len(text) <= 6 or text not in lines[line - 1]:
            continue
        if any(_is_punct(ch) for ch in text):
            continue
        if any(word in text for word in _FLOWER_BANNED) or any(word in lines[line - 1] for word in _FLOWER_BANNED):
            continue
        if style not in flower_text.STYLES:
            style = flower_text.STYLES[line % len(flower_text.STYLES)]
        side = item.get('side') if item.get('side') in ('left', 'right') else ('right' if line % 2 else 'left')
        seen.add(line)
        rows.append({'line': line, 'text': text, 'style': style, 'side': side})
        if len(rows) >= cap:
            break
    return rows


def validate(lines, data):
    if not isinstance(data, dict):
        return {'highlights': [[] for _ in lines], 'stickers': [], 'sfx': [], 'flower': []}
    highlights = highlight_ai.validate(lines, data.get('highlights'))
    stickers = _validate_stickers(lines, data.get('stickers'))
    sfx = _validate_sfx(lines, data.get('sfx'), {row['line'] for row in stickers})
    flower = _validate_flower(lines, data.get('flower'))
    return {'highlights': highlights, 'stickers': stickers, 'sfx': sfx, 'flower': flower}


def _parse(text):
    value = str(text or '').strip()
    start, end = value.find('{'), value.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(value[start:end + 1])
    except ValueError:
        return None


def plan(lines, name, base_url, api_key, model, timeout=60, want_highlights=True, want_stickers=True, want_sfx=True, want_flower=True):
    lines = [str(line or '') for line in (lines or [])]
    if not lines or not api_key:
        return validate(lines, {})
    numbered = '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, start=1))
    asks = []
    if want_highlights: asks.append('重点词')
    if want_stickers: asks.append('贴图')
    if want_sfx: asks.append('音效')
    if want_flower: asks.append('花字')
    if not asks:
        return validate(lines, {})
    user = f'产品名：{name or "未提供"}\n文案：\n{numbered}\n\n本次只需要：{"、".join(asks)}。请按 JSON 返回。'
    payload = {
        'model': str(model or 'deepseek-chat'),
        'messages': [{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': user}],
        'temperature': 0.3,
        'max_tokens': 1600,
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
    if not want_flower: plan['flower'] = []
    if not want_sfx: plan['sfx'] = []
    return plan
