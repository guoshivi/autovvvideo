from workbench.renderer import _filter
from workbench.settings import ProductionSettings
from workbench.subtitles import build_ass

import pytest


def test_both_canvas_profiles_are_accepted():
    for w, h in ((1080, 1920), (1920, 1080)):
        s = ProductionSettings.from_dict({"width": w, "height": h})
        s.validate()
        assert (s.width, s.height) == (w, h)


def test_other_canvas_is_rejected():
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"width": 1080, "height": 1080}).validate()


def test_filter_scales_to_horizontal_canvas():
    vf = _filter({"dedup": "off", "visual_filter": "original"}, None, None, None, 1920, 1080)
    assert vf.startswith("scale=1920:1080:")
    assert "pad=1920:1080" in vf


def test_ass_uses_horizontal_play_res():
    ass = build_ass("测试一行字幕", "classic", width=1920, height=1080)
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
