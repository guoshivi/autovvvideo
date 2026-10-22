import pytest

from workbench import flower_text


def test_every_style_renders_a_transparent_image():
    for style in flower_text.STYLES:
        im = flower_text.render('中老年人', style)
        assert im.mode == 'RGBA' and im.width > 80 and im.height > 40, style
        assert im.getchannel('A').getbbox() is not None


def test_style_is_deterministic_for_same_text():
    a = flower_text.render('上班族', 'burst_red').tobytes()
    b = flower_text.render('上班族', 'burst_red').tobytes()
    assert a == b


def test_text_contract():
    with pytest.raises(ValueError):
        flower_text.render('一', 'burst_red')
    with pytest.raises(ValueError):
        flower_text.render('这，里', 'burst_red')
    with pytest.raises(ValueError):
        flower_text.render('中“老”', 'burst_red')
    with pytest.raises(ValueError):
        flower_text.render('中老年人', 'nope')
