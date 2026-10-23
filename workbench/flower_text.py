"""Flower text (花字) stickers: text + shape baked into one transparent PNG.

The AI only chooses a short phrase and a style slot; this module draws the
pixels locally, so no third-party text art is shipped and the wording always
matches the current copy.  Every style is deterministic for the same text.
"""
from __future__ import annotations

import hashlib
import io
import math
from functools import lru_cache
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STYLES = (
    'burst_red', 'burst_orange', 'glow_title', 'bottom_glow', 'badge',
    'tape', 'bubble', 'neon', 'brush', 'shadow3d',
    'ribbon_badge', 'pendant_tag', 'seal', 'frame_gold',
)

STYLE_MEANINGS = {
    'burst_red': '红黄爆炸，情绪/人群',
    'burst_orange': '橙黄爆炸，提醒/卖点',
    'glow_title': '顶部光晕标题，品牌/品名',
    'bottom_glow': '底部发光强调，结论/价格',
    'badge': '圆形徽章，认证/推荐',
    'tape': '黄色胶带标签，手作/生活感',
    'bubble': '白色对话气泡，口播/疑问',
    'neon': '霓虹灯牌，夜感/潮流',
    'brush': '黄色笔刷下划线，重点',
    'shadow3d': '立体投影字，力量/强调',
    'ribbon_badge': '绶带横幅，颁奖/纪念',
    'pendant_tag': '吊牌挂坠，价格/标签',
    'seal': '圆形印章，认证/盖章',
    'frame_gold': '金色画框，高级/礼盒',
}

_FONT_CANDIDATES = (
    'C:/Windows/Fonts/msyhbd.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
)


def _font(size: int, font_path: str | None = None) -> ImageFont.FreeTypeFont:
    candidates = []
    if font_path:
        p = Path(font_path)
        candidates.append(str(p.with_name('msyhbd.ttc')))
        candidates.append(str(p))
    candidates.extend(_FONT_CANDIDATES)
    for item in candidates:
        try:
            return ImageFont.truetype(item, size)
        except OSError:
            continue
    raise ValueError('花字字体不可用')


def _angle(text: str, style: str) -> float:
    h = int(hashlib.sha256((style + text).encode('utf8')).hexdigest()[:8], 16)
    return -8 + (h % 160) / 10.0


def _text_layer(text, size, stroke, fill=(255, 255, 255, 255), stroke_fill=(0, 0, 0, 255), font_path=None, pad=48):
    font = _font(size, font_path)
    probe = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w = int(box[2] - box[0] + pad * 2)
    h = int(box[3] - box[1] + pad * 2)
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - box[0], pad - box[1]), text, font=font,
                            fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return im


def _star(size, outer, inner, fill, inner_fill, outline, points=14, width=5):
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = size // 2
    pts = []
    for i in range(points * 2):
        ang = math.pi * i / points - math.pi / 2
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=fill, outline=outline, width=width)
    pts2 = []
    for i in range(points * 2):
        ang = math.pi * i / points - math.pi / 2
        r = (outer if i % 2 == 0 else inner) * 0.70
        pts2.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts2, fill=inner_fill)
    return im


def _fit_into(background, layer, pad=24):
    """Scale a text layer into a shape, returning (canvas, center)."""
    scale = min((background.width - pad * 2) / layer.width,
                (background.height - pad * 2) / layer.height)
    if scale < 1:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.Resampling.LANCZOS)
    canvas = background.copy()
    x = (canvas.width - layer.width) // 2
    y = (canvas.height - layer.height) // 2
    canvas.alpha_composite(layer, (x, y))
    return canvas


def _rotated(layer, angle):
    return layer.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC) if angle else layer


def render(text: str, style: str, font_path: str | None = None, scale: float = 1.0) -> Image.Image:
    """Return one transparent RGBA flower-text image."""
    if style not in STYLES:
        raise ValueError('未知的花字样式')
    text = str(text or '').strip()
    if not 2 <= len(text) <= 6:
        raise ValueError('花字必须 2..6 个字')
    if any(unicodedata.category(ch).startswith('P') or ch == '～' for ch in text):
        raise ValueError('花字不能带标点')

    angle = _angle(text, style)
    if style in ('burst_red', 'burst_orange'):
        size = int(440 * scale)
        outer = size // 2
        if style == 'burst_red':
            bg = _star(size, outer, int(outer * 0.68), (226, 36, 24, 255), (255, 205, 40, 255), (120, 0, 0, 255))
        else:
            bg = _star(size, outer, int(outer * 0.68), (255, 150, 20, 255), (255, 240, 120, 255), (160, 70, 0, 255))
        txt = _text_layer(text, int(62 * scale), max(6, int(9 * scale)), font_path=font_path)
        out = _fit_into(bg, txt, pad=int(40 * scale))
        return _rotated(out, angle)

    if style == 'glow_title':
        txt = _text_layer(text, int(92 * scale), int(10 * scale), font_path=font_path)
        pad = int(90 * scale)
        canvas = Image.new('RGBA', (txt.width + pad * 2, txt.height + pad * 2), (0, 0, 0, 0))
        cx, cy = canvas.width // 2, canvas.height // 2
        rays = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(rays)
        for k in range(24):
            ang = 2 * math.pi * k / 24
            r1, r2 = canvas.width * 0.24, canvas.width * (0.40 if k % 2 == 0 else 0.32)
            rd.line([(cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)),
                     (cx + r2 * math.cos(ang), cy + r2 * math.sin(ang))],
                    fill=(255, 235, 120, 230), width=max(6, int(14 * scale)))
        rays = rays.filter(ImageFilter.GaussianBlur(int(9 * scale)))
        canvas.alpha_composite(rays)
        canvas.alpha_composite(txt, ((canvas.width - txt.width) // 2, (canvas.height - txt.height) // 2))
        return _rotated(canvas, angle * 0.3)

    if style == 'bottom_glow':
        txt = _text_layer(text, int(64 * scale), int(8 * scale), font_path=font_path)
        halo = txt.filter(ImageFilter.GaussianBlur(int(14 * scale)))
        tint = Image.new('RGBA', halo.size, (255, 220, 80, 255))
        tint.putalpha(halo.getchannel('A').point(lambda v: int(v * 0.9)))
        canvas = Image.new('RGBA', (txt.width + 40, txt.height + 40), (0, 0, 0, 0))
        canvas.alpha_composite(tint, (20, 20))
        canvas.alpha_composite(txt, (20, 20))
        return canvas

    if style == 'badge':
        size = int(340 * scale)
        im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = size // 2 - 8
        pts = []
        for i in range(48):
            ang = 2 * math.pi * i / 48
            rr = r if i % 2 == 0 else int(r * 0.92)
            pts.append((size // 2 + rr * math.cos(ang), size // 2 + rr * math.sin(ang)))
        d.polygon(pts, fill=(210, 30, 28, 255), outline=(255, 220, 120, 255), width=max(4, int(8 * scale)))
        txt = _text_layer(text, int(70 * scale), int(9 * scale), font_path=font_path)
        return _rotated(_fit_into(im, txt, pad=int(38 * scale)), angle)

    if style == 'tape':
        txt = _text_layer(text, int(58 * scale), int(7 * scale), fill=(30, 30, 30, 255), stroke_fill=(255, 255, 255, 255), font_path=font_path, pad=int(56 * scale))
        canvas = Image.new('RGBA', (txt.width, txt.height), (0, 0, 0, 0))
        tape = Image.new('RGBA', (txt.width, txt.height), (255, 214, 64, 235))
        canvas.alpha_composite(tape)
        canvas.alpha_composite(txt)
        return _rotated(canvas, angle)

    if style == 'bubble':
        txt = _text_layer(text, int(56 * scale), int(6 * scale), fill=(25, 25, 25, 255), stroke_fill=(255, 255, 255, 255), font_path=font_path, pad=int(56 * scale))
        w, h = txt.width + int(60 * scale), txt.height + int(70 * scale)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle([4, 4, w - 4, h - int(26 * scale)], radius=int(26 * scale), fill=(255, 255, 255, 245), outline=(20, 20, 20, 255), width=max(4, int(6 * scale)))
        d.polygon([(w * 0.28, h - int(28 * scale)), (w * 0.40, h - int(28 * scale)), (w * 0.32, h - 4)], fill=(255, 255, 255, 245), outline=(20, 20, 20, 255))
        canvas.alpha_composite(txt, ((w - txt.width) // 2, (h - int(26 * scale) - txt.height) // 2 + 4))
        return _rotated(canvas, angle)

    if style == 'neon':
        txt = _text_layer(text, int(66 * scale), int(8 * scale), fill=(255, 255, 255, 255), stroke_fill=(20, 0, 30, 255), font_path=font_path)
        glow = txt.filter(ImageFilter.GaussianBlur(int(12 * scale)))
        tint = Image.new('RGBA', glow.size, (255, 60, 200, 255))
        tint.putalpha(glow.getchannel('A'))
        canvas = Image.new('RGBA', (txt.width + 80, txt.height + 80), (0, 0, 0, 0))
        canvas.alpha_composite(tint, (40, 40))
        canvas.alpha_composite(tint, (40, 40))
        canvas.alpha_composite(txt, (40, 40))
        return _rotated(canvas, angle * 0.6)

    if style == 'brush':
        txt = _text_layer(text, int(66 * scale), int(8 * scale), font_path=font_path)
        canvas = Image.new('RGBA', (txt.width + 30, txt.height + int(40 * scale)), (0, 0, 0, 0))
        d = ImageDraw.Draw(canvas)
        y = txt.height + int(10 * scale)
        d.line([(int(20 * scale), y), (canvas.width - int(20 * scale), y)], fill=(255, 205, 40, 235), width=max(8, int(18 * scale)))
        canvas.alpha_composite(txt, (15, 0))
        return _rotated(canvas, angle * 0.5)

    if style == 'ribbon_badge':
        txt = _text_layer(text, int(58 * scale), int(7 * scale), font_path=font_path, pad=int(40 * scale))
        w, h = txt.width + int(150 * scale), txt.height + int(100 * scale)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(canvas)
        top, bottom = int(h * 0.26), int(h * 0.74)
        d.polygon([(int(18*scale), top), (int(62*scale), top), (int(62*scale), bottom), (int(18*scale), bottom), (int(30*scale), (top+bottom)//2)], fill=(150, 20, 24, 255))
        d.polygon([(w-int(18*scale), top), (w-int(62*scale), top), (w-int(62*scale), bottom), (w-int(18*scale), bottom), (w-int(30*scale), (top+bottom)//2)], fill=(150, 20, 24, 255))
        d.rectangle([int(48*scale), int(14*scale), w-int(48*scale), h-int(14*scale)], fill=(210, 34, 38, 255), outline=(255, 214, 92, 255), width=max(4, int(5*scale)))
        canvas.alpha_composite(txt, ((w - txt.width)//2, (h - txt.height)//2))
        return _rotated(canvas, angle * 0.5)

    if style == 'pendant_tag':
        txt = _text_layer(text, int(54 * scale), int(6 * scale), fill=(60, 42, 20, 255), stroke_fill=(255, 248, 225, 255), font_path=font_path, pad=int(38 * scale))
        w, h = txt.width + int(70 * scale), txt.height + int(160 * scale)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(canvas)
        cx = w // 2
        d.line([(cx, int(6*scale)), (cx, int(34*scale))], fill=(120, 88, 40, 255), width=max(4, int(5*scale)))
        d.ellipse([cx-int(16*scale), int(30*scale), cx+int(16*scale), int(62*scale)], outline=(198, 154, 72, 255), width=max(5, int(7*scale)))
        d.rounded_rectangle([int(8*scale), int(58*scale), w-int(8*scale), h-int(8*scale)], radius=int(18*scale), fill=(255, 246, 222, 255), outline=(176, 128, 58, 255), width=max(4, int(6*scale)))
        d.ellipse([cx-int(7*scale), int(70*scale), cx+int(7*scale), int(84*scale)], fill=(176, 128, 58, 255))
        canvas.alpha_composite(txt, ((w - txt.width)//2, int(90*scale)))
        return _rotated(canvas, angle * 0.35)

    if style == 'seal':
        txt = _text_layer(text, int(46 * scale), int(6 * scale), fill=(120, 18, 18, 255), stroke_fill=(255, 238, 200, 255), font_path=font_path, pad=int(40 * scale))
        size = max(txt.width + int(90*scale), int(300*scale))
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(canvas)
        cx = cy = size // 2; r = size // 2 - int(8*scale)
        pts = []
        for i in range(40):
            ang = 2*math.pi*i/40; rr = r if i % 2 == 0 else int(r*0.94)
            pts.append((cx + rr*math.cos(ang), cy + rr*math.sin(ang)))
        d.polygon(pts, fill=(190, 32, 32, 255), outline=(255, 214, 120, 255), width=max(4, int(7*scale)))
        d.ellipse([cx-int(r*0.78), cy-int(r*0.78), cx+int(r*0.78), cy+int(r*0.78)], outline=(255, 224, 150, 220), width=max(3, int(5*scale)))
        canvas.alpha_composite(txt, ((size - txt.width)//2, (size - txt.height)//2))
        return _rotated(canvas, angle * 0.4)

    if style == 'frame_gold':
        txt = _text_layer(text, int(58 * scale), int(8 * scale), font_path=font_path, pad=int(54 * scale))
        w, h = txt.width + int(110 * scale), txt.height + int(90 * scale)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(canvas)
        d.rectangle([int(6*scale), int(6*scale), w-int(6*scale), h-int(6*scale)], fill=(28, 26, 24, 210), outline=(226, 178, 78, 255), width=max(6, int(9*scale)))
        d.rectangle([int(22*scale), int(22*scale), w-int(22*scale), h-int(22*scale)], outline=(255, 226, 150, 230), width=max(2, int(3*scale)))
        for px, py in ((14,14),(w-14,14),(14,h-14),(w-14,h-14)):
            d.ellipse([px-int(9*scale), py-int(9*scale), px+int(9*scale), py+int(9*scale)], fill=(255, 214, 110, 255))
        canvas.alpha_composite(txt, ((w - txt.width)//2, (h - txt.height)//2))
        return _rotated(canvas, angle * 0.25)

    # shadow3d
    txt = _text_layer(text, int(70 * scale), int(9 * scale), font_path=font_path)
    canvas = Image.new('RGBA', (txt.width + int(40 * scale), txt.height + int(40 * scale)), (0, 0, 0, 0))
    shadow = Image.new('RGBA', txt.size, (20, 20, 20, 255))
    shadow.putalpha(txt.getchannel('A').point(lambda v: int(v * 0.55)))
    off = max(4, int(10 * scale))
    canvas.alpha_composite(shadow, (20 + off, 20 + off))
    canvas.alpha_composite(txt, (20, 20))
    return _rotated(canvas, angle * 0.7)


@lru_cache(maxsize=64)
def preview_png(text: str, style: str, font_path: str | None = None, scale: float = 0.5) -> bytes:
    im = render(text, style, font_path=font_path, scale=scale)
    buf = io.BytesIO(); im.save(buf, 'PNG'); return buf.getvalue()


def save(text: str, style: str, path, font_path: str | None = None, scale: float = 1.0):
    im = render(text, style, font_path=font_path, scale=scale)
    im.save(path, 'PNG')
    return path
