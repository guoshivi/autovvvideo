import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from workbench.renderer import _apply_brand
from workbench.settings import ProductionSettings

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def test_logo_and_watermark_settings_roundtrip(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(logo)
    s = ProductionSettings.from_dict({
        "logo_enabled": True, "logo_path": str(logo), "logo_pos": "br",
        "logo_scale": 0.2, "logo_opacity": 0.8,
        "watermark_enabled": True, "watermark_text": "TEST", "watermark_pos": "tl",
        "watermark_size": 30, "watermark_opacity": 0.5,
    })
    assert s.logo_path == str(logo)
    assert s.watermark_text == "TEST"


def test_brand_rejects_bad_values(tmp_path):
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"logo_enabled": True, "logo_path": ""})
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"watermark_enabled": True, "watermark_text": "  "})
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"logo_scale": 0.9})


@pytest.mark.skipif(not Path(FFMPEG).is_file(), reason="ffmpeg not on this host")
def test_brand_pass_renders_logo_and_watermark(tmp_path):
    src = tmp_path / "draft.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=540x960:d=1.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    logo = tmp_path / "logo.png"
    im = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 199, 79), fill=(220, 30, 40, 230))
    im.save(logo)
    d = tmp_path / "work"
    d.mkdir()
    cfg = SimpleNamespace(ffmpeg=FFMPEG, ffprobe=FFPROBE, font_path=FONT, max_stage_seconds=120)
    filters = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    has_drawtext = " drawtext " in filters
    out = _apply_brand(cfg, src, {
        "overlay_enabled": True,
        "overlay_images": [
            {"path": str(logo), "sha256": "", "pos": "tr", "scale": 0.16, "opacity": 0.9},
            {"path": str(logo), "sha256": "", "pos": "bl", "scale": 0.10, "opacity": 0.7},
        ],
        "watermark_enabled": has_drawtext, "watermark_text": "TEST", "watermark_pos": "bl",
        "watermark_size": 28, "watermark_opacity": 0.6,
    }, d, None)
    assert out.is_file()
    probe = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                       "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 1.2) < 0.08


def test_overlay_images_accepts_up_to_twenty(tmp_path):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (32, 32), (0, 0, 255, 255)).save(logo)
    row = {"path": str(logo), "sha256": "", "pos": "tr", "scale": 0.16, "opacity": 0.9}
    s = ProductionSettings.from_dict({"overlay_enabled": True, "overlay_images": [row] * 20})
    assert len(s.overlay_images) == 20
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"overlay_enabled": True, "overlay_images": [row] * 21})
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"overlay_images": [dict(row, pos="xx")]})
