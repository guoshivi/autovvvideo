import pytest

from workbench.renderer import _display_line
from workbench.settings import ProductionSettings


def test_display_line_uses_override_only_when_non_empty():
    s = {"subtitle_overrides": ["改过的字幕", "", "  "]}
    assert _display_line(s, "原句一", 0) == "改过的字幕"
    assert _display_line(s, "原句二", 1) == "原句二"
    assert _display_line(s, "原句三", 2) == "原句三"
    assert _display_line(s, "原句四", 9) == "原句四"


def test_settings_validate_subtitle_overrides():
    s = ProductionSettings.from_dict({"subtitle_overrides": ["一句", ""]})
    assert s.subtitle_overrides == ["一句", ""]
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"subtitle_overrides": ["x" * 201]})
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"subtitle_overrides": ["x"] * 501})
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"subtitle_overrides": ["bad\x00text"]})
