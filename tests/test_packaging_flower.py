from workbench import flower_text, packaging


LINES = ['家里长辈上了年纪总爱念叨从前的事', '这款贴布中老年人用着顺手', '上班族放在包里也不占地方']


def test_validates_substring_text_and_keeps_style():
    rows = packaging.validate(LINES, {'flower': [{'line': 2, 'text': '中老年人', 'style': 'burst_red', 'side': 'right'}]})['flower']
    assert rows == [{'line': 2, 'text': '中老年人', 'style': 'burst_red', 'side': 'right'}]


def test_rejects_text_not_in_line_and_too_long():
    data = {'flower': [
        {'line': 1, 'text': '不存在的词', 'style': 'burst_red'},
        {'line': 2, 'text': '这款贴布中老年人用着', 'style': 'burst_red'},
    ]}
    assert packaging.validate(LINES, data)['flower'] == []


def test_unknown_style_falls_back_to_a_menu_style():
    rows = packaging.validate(LINES, {'flower': [{'line': 2, 'text': '中老年人', 'style': 'nope'}]})['flower']
    assert len(rows) == 1 and rows[0]['style'] in flower_text.STYLES


def test_caps_density_at_one_per_three_lines():
    lines = ['一二三四五'] * 7
    data = {'flower': [{'line': i, 'text': '一二三', 'style': 'badge'} for i in range(1, 8)]}
    assert len(packaging.validate(lines, data)['flower']) == 3


def test_rejects_medical_claims_even_when_the_phrase_is_neutral():
    assert packaging.validate(['这个能治疗关节痛'], {'flower': [{'line': 1, 'text': '治疗', 'style': 'badge'}]})['flower'] == []
    assert packaging.validate(['这个能治疗关节痛'], {'flower': [{'line': 1, 'text': '关节', 'style': 'badge'}]})['flower'] == []
    assert packaging.validate(['这个能降血压'], {'flower': [{'line': 1, 'text': '降血压', 'style': 'badge'}]})['flower'] == []


def test_rejects_quotes_and_ellipsis():
    lines = ['中“老”年人…']
    for text in ('中“老”', '中…老'):
        assert packaging.validate(lines, {'flower': [{'line': 1, 'text': text, 'style': 'badge'}]})['flower'] == []


def test_five_lines_allow_two_flowers():
    lines = ['一二三四五'] * 5
    data = {'flower': [{'line': i, 'text': '一二三', 'style': 'badge'} for i in range(1, 6)]}
    assert len(packaging.validate(lines, data)['flower']) == 2


def test_prompt_mentions_every_style_and_flower_output():
    for style in flower_text.STYLES:
        assert f'{style}(' in packaging._SYSTEM
    assert '"flower"' in packaging._SYSTEM


def test_forced_style_overrides_the_model_choice(monkeypatch):
    from workbench import aicopy
    payload = {'choices': [{'message': {'content': '{"flower":[{"line":1,"text":"一二三","style":"badge","side":"left"}]}'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: payload)
    plan = packaging.plan(['一二三四五'], 'x', 'https://synthetic.invalid', 'TEST', 'stub', flower_style='burst_red')
    assert plan['flower'][0]['style'] == 'burst_red'


def test_density_controls_the_cap():
    lines = ['一二三四五'] * 7
    data = {'flower': [{'line': i, 'text': '一二三', 'style': 'badge'} for i in range(1, 8)]}
    assert len(packaging.validate(lines, data, 'low')['flower']) == 2
    assert len(packaging.validate(lines, data, 'normal')['flower']) == 3
    assert len(packaging.validate(lines, data, 'high')['flower']) == 4
    assert len(packaging.validate(lines, data, 'max')['flower']) == 7


def test_seed_diversifies_same_ai_answer(monkeypatch):
    from workbench import aicopy
    body = '{"stickers":[{"line":1,"icon":"check","side":"right"},{"line":4,"icon":"money","side":"right"}],"flower":[{"line":1,"text":"一二三","style":"burst_red","side":"right"},{"line":4,"text":"四五六","style":"badge","side":"left"}]}'
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': body}, 'finish_reason': 'stop'}]})
    lines = ['一二三四五'] * 6
    a = packaging.plan(lines, 'x', 'https://synthetic.invalid', 'TEST', 'stub', seed='job-a|0|0')
    b = packaging.plan(lines, 'x', 'https://synthetic.invalid', 'TEST', 'stub', seed='job-b|0|0')
    assert [(r['icon'], r['side']) for r in a['stickers']] != [(r['icon'], r['side']) for r in b['stickers']] or [r['style'] for r in a['flower']] != [r['style'] for r in b['flower']]
    same = packaging.plan(lines, 'x', 'https://synthetic.invalid', 'TEST', 'stub', seed='job-a|0|0')
    assert a == same


def test_locked_style_is_not_diversified(monkeypatch):
    from workbench import aicopy
    body = '{"flower":[{"line":1,"text":"一二三","style":"badge","side":"right"}]}'
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': body}, 'finish_reason': 'stop'}]})
    plan = packaging.plan(['一二三四五'], 'x', 'https://synthetic.invalid', 'TEST', 'stub', flower_style='burst_red', seed='s1')
    assert plan['flower'][0]['style'] == 'burst_red'
