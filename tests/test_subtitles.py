import pytest

from workbench import subtitles


def test_parse_marks_strips_markers_and_keeps_words():
    clean, marks = subtitles.parse_marks('怎么【分辨真假】呢，【正宗】一条根')
    assert clean == '怎么分辨真假呢，正宗一条根'
    assert marks == ['分辨真假', '正宗']
    assert '【' not in clean and '】' not in clean


def test_auto_highlights_price_and_number_but_never_invents_text():
    spans = subtitles.auto_highlights('背面条码471开头，今天50贴只要99块9')
    words = ['背面条码471开头，今天50贴只要99块9'[a:b] for a, b in spans]
    assert words
    assert all(word in '背面条码471开头，今天50贴只要99块9' for word in words)
    assert len(spans) <= subtitles._MAX_HIGHLIGHTS


def test_highlight_spans_prefers_manual_marks_and_limits_to_two():
    spans = subtitles.highlight_spans('怎么分辨真假呢，认准正宗测试草本膏', ['认准', '正宗'])
    words = ['怎么分辨真假呢，认准正宗测试草本膏'[a:b] for a, b in spans]
    assert words[:2] == ['认准', '正宗']
    assert len(spans) <= 2


def test_render_line_paints_only_the_keyword_and_restores_white():
    out = subtitles.render_line('怎么分辨真假呢', 'classic', ['分辨真假'])
    assert '{\\c&H0000FFFF&\\fscx110\\fscy110}分辨真假' in out
    assert out.startswith('怎么')
    assert out.endswith('呢')
    assert '{\\c&H00FFFFFF&\\fscx100\\fscy100}' in out


def test_minimal_style_never_adds_override_tags():
    out = subtitles.render_line('怎么分辨真假呢', 'minimal', ['分辨真假'])
    assert '\\c' not in out and out == '怎么分辨真假呢'


def test_build_ass_keeps_one_line_and_shrinks_long_text():
    ass = subtitles.build_ass('一' * 26, 'classic', size=54, bottom=380)
    assert 'PlayResX: 1080' in ass and 'PlayResY: 1920' in ass
    assert ',8,60,60,0,1' in ass  # alignment 8 = top centre
    dialogues = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
    assert len(dialogues) == 1  # one row only, never a stacked second line
    assert '{\\fs36}' in dialogues[0]
    assert '\\N' not in ass
    short = subtitles.build_ass('短句', 'classic', size=54, bottom=380)
    assert '\\fs' not in short


def test_build_ass_rejects_unknown_and_legacy_styles():
    with pytest.raises(ValueError):
        subtitles.build_ass('文案', 'legacy')
    with pytest.raises(ValueError):
        subtitles.build_ass('文案', 'nope')


def test_build_ass_escapes_override_braces():
    ass = subtitles.build_ass('价格{99}元', 'classic')
    assert '{99}' not in ass.split('[Events]')[1]
    assert '（99）' in ass


def test_style_labels_cover_all_options():
    for style in subtitles.SUBTITLE_STYLES:
        assert subtitles.style_label(style)


def test_wrap_never_splits_a_price_or_keyword():
    lines = subtitles._wrap_lines('这款测试草本膏今天只要99块9还包邮到家哦亲', 12)
    assert any('99块9' in line for line in lines)
    assert ''.join(lines) == '这款测试草本膏今天只要99块9还包邮到家哦亲'


def test_number_regex_keeps_the_trailing_digit_after_kuai():
    spans = subtitles.auto_highlights('今天50贴只要99块9')
    words = ['今天50贴只要99块9'[a:b] for a, b in spans]
    assert '99块9' in words


def test_ai_marks_suppress_rule_fillers():
    spans = subtitles.highlight_spans('今天只要99块9，正宗测试草本膏', ['正宗'])
    words = ['今天只要99块9，正宗测试草本膏'[a:b] for a, b in spans]
    assert words == ['正宗']
