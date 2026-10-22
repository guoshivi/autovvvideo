"""Keyword-highlighted ASS subtitles.

The legacy renderer burns one drawtext per wrapped line, so every character in
a line shares one style.  Reference ads instead keep the sentence white and
paint one or two keywords yellow.  ASS (libass) supports per-run overrides, so
this module builds the whole subtitle track for a script sentence.

Only text is handled here; ffmpeg/libass does the actual burn-in.
"""
from __future__ import annotations

import re

LEGACY = 'legacy'

# Colours are ASS BGR (&HAABBGGRR).  A trailing & closes the override value.
ASS_PRESETS = {
    'classic': {'label': '白字黄重点（参考片同款）', 'highlight': '&H0000FFFF&', 'scale': 110, 'outline': 3, 'shadow': 1, 'bold': True},
    'neon':    {'label': '荧光放大（黄字 120%）',    'highlight': '&H0000FFFF&', 'scale': 120, 'outline': 4, 'shadow': 2, 'bold': True},
    'peach':   {'label': '蜜桃粉重点',              'highlight': '&H00A56FFF&', 'scale': 110, 'outline': 3, 'shadow': 1, 'bold': True},
    'mint':    {'label': '薄荷绿重点',              'highlight': '&H00B0E07B&', 'scale': 110, 'outline': 3, 'shadow': 1, 'bold': True},
    'orange':  {'label': '暖橙重点',                'highlight': '&H003C9AFF&', 'scale': 110, 'outline': 3, 'shadow': 1, 'bold': True},
    'minimal': {'label': '纯白极简（不标重点）',      'highlight': None,          'scale': 100, 'outline': 2, 'shadow': 0, 'bold': False},
}
SUBTITLE_STYLES = (LEGACY,) + tuple(ASS_PRESETS)

# Manual marks win over automatic picks: 【正宗】 is stripped from the visible
# text and highlighted verbatim.
_MARK = re.compile(r'【([^】\n]{1,24})】')
_NUMBER = re.compile(r'\d+(?:\.\d+)?\s*(?:块|元)(?:\d{1,2})?|\d+(?:\.\d+)?\s*(?:贴|片|盒|瓶|支|件|折|天|年|小时|分钟|秒|%|％)')
_LEXICON = (
    '正宗', '真假', '分辨', '认准', '正品', '防伪', '透气', '简单', '舒服', '好用',
    '免费', '包邮', '今天', '最后', '只要', '注意', '重点', '第一步', '第二步',
    '别再', '千万别', '限时', '到手', '划算', '值',
)
_MAX_HIGHLIGHTS = 2



_PUNCT = '，。！？；：、,.!?;:'


def _wrap_lines(text: str, max_chars: int = 12, protect=()):
    """Wrap at punctuation first; never cut a protected price/keyword in half."""
    out = []
    for raw in str(text or '').splitlines() or ['']:
        spans = sorted(protect) or [(m.start(), m.end()) for m in _NUMBER.finditer(raw)]
        pieces, buf = [], ''
        for ch in raw:
            buf += ch
            if ch in _PUNCT:
                pieces.append(buf)
                buf = ''
        if buf:
            pieces.append(buf)
        line = ''
        base = 0
        for piece in pieces:
            while len(piece) > max_chars:
                cut = max_chars
                for start, end in spans:
                    a, b = start - base, end - base
                    if a < cut < b:
                        cut = b if b - a <= max_chars + 4 else a
                        break
                cut = max(1, min(len(piece), cut))
                if line:
                    out.append(line)
                    line = ''
                out.append(piece[:cut])
                piece = piece[cut:]
                base += cut
            if len(line) + len(piece) <= max_chars:
                line += piece
            else:
                if line:
                    out.append(line)
                line = piece
            base += len(piece)
        if line:
            out.append(line)
    return out or ['']

def is_ass_style(value) -> bool:
    return isinstance(value, str) and value in ASS_PRESETS


def style_label(value: str) -> str:
    if value == LEGACY:
        return '经典整行字幕（旧）'
    return ASS_PRESETS.get(value, {}).get('label', value)


def parse_marks(text: str):
    """Return (visible_text, manual highlight words)."""
    marks = []

    def _keep(match):
        marks.append(match.group(1))
        return match.group(1)

    clean = _MARK.sub(_keep, str(text or ''))
    return clean, marks


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def auto_highlights(text: str):
    """Pick up to two emphasis spans; prices/numbers always outrank filler words."""
    numbers = [(m.start(), m.end()) for m in _NUMBER.finditer(text)]
    words = []
    for word in _LEXICON:
        start = text.find(word)
        if start >= 0:
            words.append((start, start + len(word)))
    picked = []
    for span in numbers + words:
        if any(_overlap(span, other) for other in picked):
            continue
        picked.append(span)
        if len(picked) >= _MAX_HIGHLIGHTS:
            break
    return sorted(picked)


def highlight_spans(line: str, marks=(), limit: int = _MAX_HIGHLIGHTS, auto: bool = True):
    spans = []
    for word in marks:
        if not word:
            continue
        start = line.find(word)
        if start >= 0:
            span = (start, start + len(word))
            if not any(_overlap(span, other) for other in spans):
                spans.append(span)
    if auto and not marks:
        for span in auto_highlights(line):
            if len(spans) >= limit:
                break
            if not any(_overlap(span, other) for other in spans):
                spans.append(span)
    return sorted(spans)[:limit]


def _escape(text: str) -> str:
    # Braces open ASS override blocks and \N is a hard line break.  Full-width
    # substitutes keep the copy readable without corrupting the script.
    return str(text).replace('\\', '＼').replace('{', '（').replace('}', '）').replace('\n', ' ')


def render_line(line: str, style: str, marks=(), auto: bool = True) -> str:
    preset = ASS_PRESETS[style]
    colour = preset['highlight']
    if not colour:
        return _escape(line)
    spans = highlight_spans(line, marks, auto=auto)
    if not spans:
        return _escape(line)
    scale = preset['scale']
    parts = []
    cursor = 0
    for start, end in spans:
        parts.append(_escape(line[cursor:start]))
        parts.append(f'{{\\c{colour}\\fscx{scale}\\fscy{scale}}}')
        parts.append(_escape(line[start:end]))
        parts.append('{\\c&H00FFFFFF&\\fscx100\\fscy100}')
        cursor = end
    parts.append(_escape(line[cursor:]))
    return ''.join(parts)




def _fit_size(text: str, width: int, base: int, margin: int = 72) -> int:
    """Largest font size that keeps one line inside the safe width."""
    units = sum(1.0 if ord(ch) > 127 else 0.55 for ch in text)
    if units <= 0:
        return base
    return max(22, min(base, int((width - 2 * margin) / units)))

def build_ass(text: str, style: str, size: int = 54, bottom: int = 380,
              width: int = 1080, height: int = 1920,
              font: str = 'Microsoft YaHei', hold: str = '9:59:59.99',
              marks=(), auto: bool = True, single_line: bool = True) -> str:
    """Build a one-sentence ASS script.

    ``bottom`` keeps the legacy meaning: distance from the bottom of the frame
    to the top of the first subtitle line.  Top alignment (8) reproduces that
    anchor, and each wrapped line is pushed down by the drawtext pitch.
    """
    if style not in ASS_PRESETS:
        raise ValueError('未知的字幕样式')
    if not isinstance(size, int) or not 24 <= size <= 100:
        raise ValueError('subtitle size out of range')
    clean, inline = parse_marks(text)
    marks = tuple(inline) + tuple(marks or ())
    protect = [(m.start(), m.end()) for m in _NUMBER.finditer(clean)] + [(clean.find(w), clean.find(w) + len(w)) for w in marks if clean.find(w) >= 0]
    lines = [clean] if single_line else _wrap_lines(clean, 12, protect)
    pitch = round(size * 2.606 + 25.35)
    margin_v = height - bottom - round(size * 0.22)
    preset = ASS_PRESETS[style]
    bold = -1 if preset['bold'] else 0
    head = (
        '[Script Info]\nScriptType: v4.00+\n'
        f'PlayResX: {width}\nPlayResY: {height}\n'
        'WrapStyle: 2\nScaledBorderAndShadow: yes\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,{bold},0,0,0,100,100,0,0,1,'
        f"{preset['outline']},{preset['shadow']},8,60,60,0,1\n\n"
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )
    events = []
    for index, line in enumerate(lines):
        body = render_line(line, style, marks, auto=auto)
        eff = _fit_size(line, width, size) if single_line else size
        if eff < size:
            body = f'{{\\fs{eff}}}' + body
            mv = margin_v + index * pitch
        else:
            mv = margin_v + index * pitch
        events.append(
            f'Dialogue: 0,0:00:00.00,{hold},Default,,0,0,{mv},,{body}'
        )
    return head + '\n'.join(events) + '\n'
