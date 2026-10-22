import pytest

from workbench import renderer
from workbench.settings import ProductionSettings
from workbench.subtitles import SUBTITLE_STYLES


def test_default_subtitle_style_is_classic():
    assert ProductionSettings().subtitle_style == 'classic'


@pytest.mark.parametrize('style', SUBTITLE_STYLES)
def test_every_advertised_style_is_accepted(style):
    assert ProductionSettings(subtitle_style=style).validate().subtitle_style == style


def test_unknown_subtitle_style_is_rejected():
    with pytest.raises(ValueError, match='subtitle_style'):
        ProductionSettings(subtitle_style='rainbow').validate()


def test_filter_uses_ass_track_and_skips_drawtext():
    settings = ProductionSettings(subtitle_style='classic').snapshot()
    vf = renderer._filter(settings, None, 'msyh.ttc', 'cap_000.ass')
    assert vf.endswith(',ass=cap_000.ass')
    assert 'drawtext' not in vf


def test_legacy_style_still_uses_drawtext(tmp_path):
    caption = tmp_path / 'cap_000.txt'
    caption.write_text('怎么分辨真假呢', encoding='utf-8')
    settings = ProductionSettings(subtitle_style='legacy').snapshot()
    vf = renderer._filter(settings, str(caption), 'msyh.ttc', None)
    assert 'drawtext' in vf
    assert 'ass=' not in vf


def test_disabled_subtitles_produce_no_caption_filter():
    settings = ProductionSettings(subtitles=False).snapshot()
    assert renderer._caption_vf(settings, None, None, 'msyh.ttc') is None


def test_default_highlight_source_is_ai_and_manual_is_accepted():
    assert ProductionSettings().highlight_source == 'ai'
    assert ProductionSettings(highlight_source='manual').validate().highlight_source == 'manual'
    assert ProductionSettings(highlight_source='rules').validate().highlight_source == 'rules'
    with pytest.raises(ValueError, match='highlight_source'):
        ProductionSettings(highlight_source='magic').validate()


def test_explicit_marks_win_and_manual_mode_never_auto_picks():
    from workbench.subtitles import build_ass
    ass = build_ass('怎么分辨真假，今天99块9', 'classic', marks=['分辨真假'], auto=False)
    assert '分辨真假' in ass and '99块9' in ass
    event = ass.split('[Events]')[1]
    assert event.count('\\c&H0000FFFF&') == 1
    assert '99块9' in event


def test_three_feature_toggles_default_on_and_validate_boolean():
    s = ProductionSettings()
    assert s.subtitle_highlight is True and s.sticker_enabled is True and s.sfx_enabled is True
    assert ProductionSettings(sticker_enabled=False).validate().sticker_enabled is False
    with pytest.raises(ValueError, match='sticker_enabled'):
        ProductionSettings(sticker_enabled='yes').validate()


def test_beauty_without_subtitles_produces_no_caption_filter():
    settings = ProductionSettings(subtitles=False, beauty=True).snapshot()
    assert renderer._caption_vf(settings, None, None, 'msyh.ttc') is None


def test_flower_toggle_defaults_on_and_requires_bool():
    from workbench.settings import ProductionSettings
    import pytest
    assert ProductionSettings.from_dict({}).snapshot()['flower_enabled'] is True
    assert ProductionSettings.from_dict({'flower_enabled': False}).snapshot()['flower_enabled'] is False
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({'flower_enabled': 'yes'})
