from pathlib import Path

from PIL import Image

from workbench.packaging import ICON_MEANINGS, SOUND_MEANINGS, ICONS, SOUNDS
from workbench import flower_text

ASSETS = Path(__file__).resolve().parents[1] / "workbench" / "assets"


def test_every_icon_menu_entry_has_a_png():
    assert len(ICONS) >= 130
    missing = [i for i in ICONS if not (ASSETS / "stickers" / f"{i}.png").is_file()]
    assert not missing, missing


def test_every_sound_menu_entry_has_a_wav():
    assert len(SOUNDS) >= 45
    missing = [s for s in SOUNDS if not (ASSETS / "sfx" / f"{s}.wav").is_file()]
    assert not missing, missing


def test_bundled_display_fonts_exist():
    for name in ("ZCOOLKuaiLe-Regular.ttf", "MaShanZheng-Regular.ttf",
                 "LongCang-Regular.ttf", "ZCOOLQingKeHuangYou-Regular.ttf"):
        assert (ASSETS / "fonts" / name).is_file(), name


def test_flower_styles_include_font_and_gradient_variants():
    for style in ("kuaile_pop", "masha_ink", "longcang_script", "qingke_banner",
                  "gradient_fill", "outline_glow"):
        assert style in flower_text.STYLES
        im = flower_text.render("爆款", style, scale=0.3)
        assert im.mode == "RGBA" and im.width > 10 and im.height > 10
