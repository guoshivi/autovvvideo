"""Ask the model for one packaging plan: highlights, stickers and sound cues.

The model chooses semantic slots from a fixed menu, never concrete files.  The
server validates every slot, caps density and freezes the plan into the job so a
re-render stays identical.  Unusable answers fall back to "no sticker / no sfx".
"""
from __future__ import annotations

import hashlib
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
    'crown': '冠军/尊贵', 'rosette': '奖项/认可', 'ribbon': '纪念/节日', 'bell': '提醒/通知',
    'trophy': '成就/胜利', 'tag': '标签/分类', 'coin': '省钱/成本', 'chain': '牢固/连接',
    'handbag': '随身/送礼', 'purse': '预算/省钱',
    'shopping_cart': '购物车/下单',
    'shopping_bags': '购物袋/逛店',
    'money_bag': '省钱/划算',
    'money_wings': '花钱/预算',
    'credit_card': '支付/刷卡',
    'receipt': '小票/账单',
    'package': '包裹/发货',
    'delivery_truck': '物流/到货',
    'gem_stone': '品质/珍贵',
    'chart_up': '增长/效果',
    'chart_down': '下降/避坑',
    'bar_chart': '数据/对比',
    'handshake': '成交/合作',
    'abacus': '算账/精打细算',
    'briefcase': '商务/通勤',
    'money_face': '划算/真香',
    'dollar_note': '价格/预算',
    'label': '标签/分类',
    'pin_round': '定位/重点',
    'lipstick': '口红/妆容',
    'nail_polish': '美甲/精致',
    'lotion': '护肤/滋润',
    'soap': '清洁/温和',
    'sponge': '柔软/清洁',
    'mirror': '照镜/颜值',
    'toothbrush': '清洁/护理',
    'dress': '穿搭/裙装',
    'high_heel': '高跟鞋/气质',
    'handbag_new': '包包/通勤',
    'clutch_bag': '手拿包/晚宴',
    'crown_new': '皇冠/尊贵',
    'rose': '玫瑰/浪漫',
    'sunflower': '阳光/元气',
    'tulip': '郁金香/春日',
    'biceps': '力量/健身',
    'running': '运动/坚持',
    'bicycle': '骑行/有氧',
    'running_shoe': '跑鞋/轻便',
    'stopwatch': '计时/效率',
    'brain': '头脑/聪明',
    'anatomical_heart': '健康/心肺',
    'leafy_green': '蔬菜/轻食',
    'green_apple': '苹果/低卡',
    'lemon': '柠檬/清爽',
    'honey_pot': '蜂蜜/滋养',
    'sleeping_face': '睡眠/休息',
    'sparkles': '闪亮/新品',
    'red_heart': '喜欢/心动',
    'fire_new': '热门/火爆',
    'party_popper': '庆祝/上新',
    'wrapped_gift': '礼物/心意',
    'trophy_new': '冠军/第一',
    'medal_gold': '金牌/第一',
    'clapping': '鼓掌/好评',
    'thumbs_up': '点赞/认可',
    'thumbs_down': '不推荐/避雷',
    'folded_hands': '感谢/拜托',
    'hundred': '满分/绝了',
    'star_struck': '惊艳/追星',
    'heart_eyes': '心动/喜欢',
    'thinking': '思考/疑问',
    'screaming': '震惊/害怕',
    'crying': '难过/踩雷',
    'check_mark': '对/正确',
    'cross_mark': '错/不要',
    'question_red': '疑问/为什么',
    'exclaim_red': '注意/重点',
    'prohibited': '禁止/不可以',
    'bell_new': '提醒/通知',
    'alarm_clock': '时间/限时',
    'hourglass': '倒计时/等待',
    'calendar': '日期/活动',
    'pushpin': '固定/重点',
    'paperclip': '附件/清单',
    'scissors': '剪/裁剪',
    'hammer': '打造/修复',
    'gear': '设置/参数',
    'bulb_new': '灵感/亮点',
    'megaphone': '宣传/喊话',
    'lock': '锁定/安全',
    'key': '关键/解锁',
    'shield_new': '保护/安心',
    'scale': '平衡/对比',
    'magnet': '吸引/吸睛',
    'rocket': '起飞/爆款',
    'fireworks': '庆祝/爆单',
    'rainbow': '彩虹/美好',
    'sun_new': '阳光/能量',
    'cloud': '云/轻松',
    'umbrella': '防护/雨天',
    'snowflake': '冰凉/冬季',
    'star_yellow': '好评/推荐',
    'star_glow': '闪亮/重点',
    'heart_ribbon': '心意/礼物',
    'theater': '表演/舞台',
    'clapper': '开拍/视频',
}
ICONS = tuple(ICON_MEANINGS)

_ICON_FAMILIES = {
    'check': ('check', 'star', 'clap'), 'star': ('star', 'check', 'clap'),
    'clap': ('clap', 'star', 'heart'), 'muscle': ('muscle', 'clap'),
    'question': ('question', 'eyes'), 'warning': ('warning', 'eyes'),
    'eyes': ('eyes', 'question'), 'money': ('money', 'cart', 'gift'),
    'cart': ('cart', 'money'), 'gift': ('gift', 'heart', 'cart'),
    'fire': ('fire', 'party'), 'party': ('party', 'fire'),
    'sparkle': ('sparkle', 'bulb'), 'bulb': ('bulb', 'sparkle'),
    'box': ('box', 'home', 'truck'), 'home': ('home', 'box'),
    'truck': ('truck', 'box'), 'leaf': ('leaf', 'sun'), 'sun': ('sun', 'leaf'),
    'crown': ('crown', 'trophy', 'rosette'), 'trophy': ('trophy', 'crown', 'rosette'),
    'rosette': ('rosette', 'crown', 'ribbon'), 'ribbon': ('ribbon', 'gift', 'party'),
    'bell': ('bell', 'clock', 'tag'), 'tag': ('tag', 'coin', 'ribbon'),
    'coin': ('coin', 'money', 'purse'), 'chain': ('chain', 'shield', 'muscle'),
    'handbag': ('handbag', 'purse', 'gift'), 'purse': ('purse', 'coin', 'money'),
    'heart': ('heart', 'clap'), 'thumb': ('thumb', 'clap'),
}
_ICON_FAMILIES.update({
    'shopping_cart': ('shopping_cart', 'shopping_bags'),
    'shopping_bags': ('shopping_bags', 'shopping_cart'),
    'money_bag': ('money_bag', 'money_wings', 'credit_card'),
    'money_wings': ('money_wings', 'money_bag', 'credit_card'),
    'credit_card': ('credit_card', 'money_bag', 'money_wings'),
    'receipt': ('receipt', 'money_bag', 'money_wings'),
    'package': ('package', 'briefcase'),
    'delivery_truck': ('delivery_truck'),
    'gem_stone': ('gem_stone', 'handbag_new', 'clutch_bag'),
    'coin': ('coin', 'money_bag', 'money_wings'),
    'chart_up': ('chart_up', 'chart_down', 'bar_chart'),
    'chart_down': ('chart_down', 'chart_up', 'bar_chart'),
    'bar_chart': ('bar_chart', 'chart_up', 'chart_down'),
    'handshake': ('handshake', 'clapping', 'folded_hands'),
    'abacus': ('abacus', 'money_bag', 'money_wings'),
    'briefcase': ('briefcase', 'package'),
    'money_face': ('money_face', 'money_bag', 'money_wings'),
    'dollar_note': ('dollar_note', 'money_bag', 'money_wings'),
    'label': ('label', 'pin_round', 'pushpin'),
    'pin_round': ('pin_round', 'label', 'pushpin'),
    'lipstick': ('lipstick', 'nail_polish', 'lotion'),
    'nail_polish': ('nail_polish', 'lipstick', 'lotion'),
    'lotion': ('lotion', 'lipstick', 'nail_polish'),
    'soap': ('soap', 'lipstick', 'nail_polish'),
    'sponge': ('sponge', 'lipstick', 'nail_polish'),
    'mirror': ('mirror', 'lipstick', 'nail_polish'),
    'toothbrush': ('toothbrush', 'lipstick', 'nail_polish'),
    'dress': ('dress', 'lipstick', 'nail_polish'),
    'high_heel': ('high_heel', 'lipstick', 'nail_polish'),
    'handbag_new': ('handbag_new', 'gem_stone', 'clutch_bag'),
    'clutch_bag': ('clutch_bag', 'gem_stone', 'handbag_new'),
    'crown_new': ('crown_new', 'trophy_new', 'medal_gold'),
    'rose': ('rose', 'gem_stone', 'handbag_new'),
    'sunflower': ('sunflower', 'rainbow', 'sun_new'),
    'tulip': ('tulip', 'gem_stone', 'handbag_new'),
    'biceps': ('biceps', 'running', 'bicycle'),
    'running': ('running', 'biceps', 'bicycle'),
    'bicycle': ('bicycle', 'biceps', 'running'),
    'running_shoe': ('running_shoe', 'biceps', 'running'),
    'stopwatch': ('stopwatch', 'sleeping_face', 'alarm_clock'),
    'brain': ('brain', 'bulb_new'),
    'anatomical_heart': ('anatomical_heart', 'lipstick', 'nail_polish'),
    'leafy_green': ('leafy_green', 'green_apple', 'lemon'),
    'green_apple': ('green_apple', 'leafy_green', 'lemon'),
    'lemon': ('lemon', 'leafy_green', 'green_apple'),
    'honey_pot': ('honey_pot', 'leafy_green', 'green_apple'),
    'sleeping_face': ('sleeping_face', 'stopwatch', 'alarm_clock'),
    'sparkles': ('sparkles'),
    'red_heart': ('red_heart', 'lipstick', 'nail_polish'),
    'fire_new': ('fire_new', 'rocket'),
    'party_popper': ('party_popper', 'fireworks', 'theater'),
    'wrapped_gift': ('wrapped_gift', 'gem_stone', 'handbag_new'),
    'trophy_new': ('trophy_new', 'crown_new', 'medal_gold'),
    'medal_gold': ('medal_gold', 'crown_new', 'trophy_new'),
    'clapping': ('clapping', 'handshake', 'folded_hands'),
    'thumbs_up': ('thumbs_up', 'thumbs_down'),
    'thumbs_down': ('thumbs_down', 'thumbs_up'),
    'folded_hands': ('folded_hands', 'handshake', 'clapping'),
    'hundred': ('hundred', 'star_struck', 'magnet'),
    'star_struck': ('star_struck', 'hundred', 'magnet'),
    'heart_eyes': ('heart_eyes', 'lipstick', 'nail_polish'),
    'thinking': ('thinking', 'question_red'),
    'screaming': ('screaming', 'exclaim_red', 'prohibited'),
    'crying': ('crying'),
    'check_mark': ('check_mark'),
    'cross_mark': ('cross_mark'),
    'question_red': ('question_red', 'thinking'),
    'exclaim_red': ('exclaim_red', 'screaming', 'prohibited'),
    'prohibited': ('prohibited', 'screaming', 'exclaim_red'),
    'bell_new': ('bell_new', 'megaphone'),
    'alarm_clock': ('alarm_clock', 'stopwatch', 'sleeping_face'),
    'hourglass': ('hourglass', 'stopwatch', 'sleeping_face'),
    'calendar': ('calendar', 'stopwatch', 'sleeping_face'),
    'pushpin': ('pushpin', 'label', 'pin_round'),
    'paperclip': ('paperclip', 'label', 'pin_round'),
    'scissors': ('scissors', 'label', 'pin_round'),
    'hammer': ('hammer', 'label', 'pin_round'),
    'gear': ('gear', 'label', 'pin_round'),
    'bulb_new': ('bulb_new', 'brain'),
    'megaphone': ('megaphone', 'bell_new'),
    'lock': ('lock', 'key', 'shield_new'),
    'key': ('key', 'lock', 'shield_new'),
    'shield_new': ('shield_new', 'lock', 'key'),
    'scale': ('scale', 'chart_up', 'chart_down'),
    'magnet': ('magnet', 'hundred', 'star_struck'),
    'rocket': ('rocket', 'fire_new'),
    'fireworks': ('fireworks', 'party_popper', 'theater'),
    'rainbow': ('rainbow', 'sunflower', 'sun_new'),
    'sun_new': ('sun_new', 'sunflower', 'rainbow'),
    'cloud': ('cloud', 'sunflower', 'rainbow'),
    'umbrella': ('umbrella', 'lock', 'key'),
    'snowflake': ('snowflake', 'sunflower', 'rainbow'),
    'star_yellow': ('star_yellow', 'hundred', 'star_struck'),
    'star_glow': ('star_glow', 'hundred', 'star_struck'),
    'heart_ribbon': ('heart_ribbon', 'gem_stone', 'handbag_new'),
    'theater': ('theater', 'party_popper', 'fireworks'),
    'clapper': ('clapper', 'label', 'pin_round'),
})
_FLOWER_FAMILIES = {
    'burst_red': ('burst_red', 'burst_orange'), 'burst_orange': ('burst_orange', 'burst_red'),
    'glow_title': ('glow_title', 'neon'), 'neon': ('neon', 'glow_title'),
    'bottom_glow': ('bottom_glow', 'brush', 'shadow3d'), 'brush': ('brush', 'bottom_glow', 'shadow3d'),
    'shadow3d': ('shadow3d', 'brush', 'bottom_glow'),
    'badge': ('badge', 'tape', 'bubble'), 'tape': ('tape', 'badge', 'bubble'), 'bubble': ('bubble', 'tape', 'badge'),
    'kuaile_pop': ('kuaile_pop', 'qingke_banner', 'gradient_fill'),
    'qingke_banner': ('qingke_banner', 'kuaile_pop', 'ribbon_badge'),
    'masha_ink': ('masha_ink', 'longcang_script', 'seal'),
    'longcang_script': ('longcang_script', 'masha_ink', 'brush'),
    'gradient_fill': ('gradient_fill', 'neon', 'outline_glow'),
    'outline_glow': ('outline_glow', 'glow_title', 'gradient_fill'),
}


def _pick(options, seed, salt):
    if len(options) <= 1:
        return options[0]
    h = hashlib.sha256(f'{seed}|{salt}'.encode('utf8')).digest()
    return options[h[0] % len(options)]


def _diversify(plan, seed, flower_locked=False):
    """Give every job a different but still menu-valid combination."""
    for n, row in enumerate(plan.get('stickers') or []):
        row['icon'] = _pick(_ICON_FAMILIES.get(row['icon'], (row['icon'],)), seed, f'st{n}')
        if hashlib.sha256(f'{seed}|side{n}'.encode()).digest()[1] & 1:
            row['side'] = 'left' if row.get('side') == 'right' else 'right'
    for n, row in enumerate(plan.get('flower') or []):
        if not flower_locked:
            row['style'] = _pick(_FLOWER_FAMILIES.get(row['style'], (row['style'],)), seed, f'fl{n}')
        if hashlib.sha256(f'{seed}|fside{n}'.encode()).digest()[1] & 1:
            row['side'] = 'left' if row.get('side') == 'right' else 'right'
    return plan
SOUND_MEANINGS = {
    'ding': '提示/强调', 'pop': '弹出/出现', 'whoosh': '转场/快速', 'click': '点击/确认',
    'coin': '价格/省钱', 'success': '成功/完成', 'switch': '切换/对比', 'rollover': '轻提示/悬停',
    'error': '错误/否定', 'notify': '提醒/通知', 'sparkle': '亮点/闪光', 'powerup': '升级/增强',
    'slide': '滑入/转场', 'cash': '收钱/成交', 'win': '胜利/达成',
    'cart': '加购/下单', 'purchase': '成交/付款', 'coupon': '优惠券/省钱', 'checkout': '结算',
    'reward': '奖励', 'achievement': '成就', 'levelup': '升级', 'warn': '警告',
    'swipe': '滑动', 'hover': '悬停', 'toggle': '开关', 'complete': '完成',
    'tap1': '轻点 1', 'tap2': '轻点 2', 'tap3': '轻点 3', 'tap4': '轻点 4', 'tap5': '轻点 5',
    'hover_a': '悬停轻响 A', 'hover_b': '悬停轻响 B', 'hover_c': '悬停轻响 C',
    'switch_a': '切换 A', 'switch_b': '切换 B', 'switch_c': '切换 C',
    'mouse_click': '鼠标点击', 'mouse_release': '鼠标松开',
    'impact1': '重击/转场 1', 'impact2': '重击/转场 2', 'impact3': '重击/转场 3',
    'coin_8bit': '8bit 金币', 'level_clear': '过关', 'fanfare': '号角/开场',
}
SOUNDS = tuple(SOUND_MEANINGS)
FLOWER_DENSITY = {'low': 4, 'normal': 3, 'high': 2, 'max': 1}

_SYSTEM = (
    '你是短视频包装编辑。给你按行编号的口播文案，请一次给出三样东西：'
    '1) 每句 1-2 个值得在字幕里变色的重点词；2) 最多每 3 句 1 个贴图；3) 与贴图同帧的音效。'
    '贴图只能从这些类型里选：' + '、'.join(f'{k}({v})' for k, v in ICON_MEANINGS.items()) + '。'
    '音效只能从这些类型里选：' + '、'.join(f'{k}({v})' for k, v in SOUND_MEANINGS.items()) + '。'
    '贴图不要每句都加，只在语义真的匹配时加；医疗、功效、身体症状的句子不许配庆祝/正向类贴图（money/fire/sparkle/star/heart/party/clap/muscle）。'
    '重点词必须原样摘自该句，不得选医疗功效词。'
    '另外给 0-2 个花字：从该句原样摘 2-6 个字（不能带标点），'
    '花字风格只能从这些里选：' + '、'.join(f'{k}({v})' for k, v in flower_text.STYLE_MEANINGS.items()) + '。'
    '花字最多每 3 句 1 个，整条片子尽量给到 2-3 个（文案不足 4 句可只给 1 个）；医疗、功效、身体症状的句子不许配花字。'
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


def _validate_flower(lines, raw, density='normal'):
    count = len(lines)
    per = FLOWER_DENSITY.get(density, 3)
    cap = max(1, math.ceil(count / per))
    if count >= 4 and density in ('normal', 'high', 'max'): cap = max(2, cap)
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


def validate(lines, data, flower_density='normal'):
    if not isinstance(data, dict):
        return {'highlights': [[] for _ in lines], 'stickers': [], 'sfx': [], 'flower': []}
    highlights = highlight_ai.validate(lines, data.get('highlights'))
    stickers = _validate_stickers(lines, data.get('stickers'))
    sfx = _validate_sfx(lines, data.get('sfx'), {row['line'] for row in stickers})
    flower = _validate_flower(lines, data.get('flower'), flower_density)
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


def plan(lines, name, base_url, api_key, model, timeout=60, want_highlights=True, want_stickers=True, want_sfx=True, want_flower=True, flower_style='auto', flower_density='high', seed=''):
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
    per = FLOWER_DENSITY.get(flower_density, 3)
    user = f'产品名：{name or "未提供"}\n文案：\n{numbered}\n\n本次只需要：{"、".join(asks)}。'
    if want_flower:
        user += f'花字最多每 {per} 句 1 个（全片最多 {max(1, math.ceil(len(lines) / per))} 个）。'
    user += '请按 JSON 返回。'
    payload = {
        'model': str(model or 'deepseek-chat'),
        'messages': [{'role': 'system', 'content': _SYSTEM}, {'role': 'user', 'content': user}],
        'temperature': 0.8,
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
    plan = validate(lines, _parse(text), flower_density)
    if flower_style in flower_text.STYLES:
        for row in plan['flower']:
            row['style'] = flower_style
    if seed:
        plan = _diversify(plan, seed, flower_locked=flower_style in flower_text.STYLES)
    if not want_highlights: plan['highlights'] = [[] for _ in lines]
    if not want_stickers: plan['stickers'] = []
    if not want_flower: plan['flower'] = []
    if not want_sfx: plan['sfx'] = []
    return plan
