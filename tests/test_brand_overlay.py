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
        "logo_enabled": True, "logo_path": str(logo), "logo_sha256": "",
        "logo_pos": "tr", "logo_scale": 0.16, "logo_opacity": 0.9,
        "watermark_enabled": has_drawtext, "watermark_text": "TEST", "watermark_pos": "bl",
        "watermark_size": 28, "watermark_opacity": 0.6,
    }, d, None)
    assert out.is_file()
    probe = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                       "-of", "json", str(out)], capture_output=True, text=True, check=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 1.2) < 0.08
