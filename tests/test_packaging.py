from workbench import aicopy, packaging


def lines(n=6):
    return [f'第{i}句文案' for i in range(1, n + 1)]


def test_sticker_density_is_capped_and_lines_are_unique():
    data = {'stickers': [
        {'line': 1, 'icon': 'check'}, {'line': 2, 'icon': 'fire'},
        {'line': 3, 'icon': 'money'}, {'line': 4, 'icon': 'box'},
        {'line': 4, 'icon': 'cry'},
    ]}
    plan = packaging.validate(lines(6), data)
    assert len(plan['stickers']) == 2  # ceil(6/3)
    assert [s['line'] for s in plan['stickers']] == [1, 2]
    assert plan['stickers'][0]['side'] == 'right' and plan['stickers'][0]['at'] == 'start'


def test_unknown_icon_or_line_is_dropped():
    data = {'stickers': [{'line': 99, 'icon': 'check'}, {'line': 1, 'icon': 'not_a_real_icon'}, {'line': 2, 'icon': 'box'}]}
    assert [s['line'] for s in packaging.validate(lines(4), data)['stickers']] == [2]


def test_sfx_only_on_sticker_or_number_lines_and_gain_is_clamped():
    data = {
        'stickers': [{'line': 1, 'icon': 'check'}],
        'sfx': [{'line': 1, 'sound': 'ding', 'gain': -30}, {'line': 2, 'sound': 'pop'}, {'line': 3, 'sound': 'coin'}],
    }
    plan = packaging.validate(['第一句', '第二句', '第三句99块9'], data)
    assert [s['line'] for s in plan['sfx']] == [1, 3]
    assert plan['sfx'][0]['gain'] == -12.0
    assert plan['sfx'][1]['gain'] == -6.0


def test_unknown_sound_is_dropped():
    data = {'stickers': [{'line': 1, 'icon': 'check'}], 'sfx': [{'line': 1, 'sound': 'boom'}]}
    assert packaging.validate(lines(3), data)['sfx'] == []


def test_plan_parses_and_validates_model_json(monkeypatch):
    body = '{"highlights":{"1":["第一句"]},"stickers":[{"line":2,"icon":"money"}],"sfx":[{"line":2,"sound":"coin","gain":-7}]}'
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': body}, 'finish_reason': 'stop'}]})
    plan = packaging.plan(['第一句文案', '第二句99块9', '第三句'], '商品', 'https://synthetic.invalid', 'TEST', 'stub')
    assert plan['highlights'][0] == ['第一句']
    assert plan['stickers'] == [{'line': 2, 'icon': 'money', 'at': 'start', 'side': 'right'}]
    assert plan['sfx'] == [{'line': 2, 'sound': 'coin', 'gain': -7.0}]


def test_plan_falls_back_to_empty_on_bad_json(monkeypatch):
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': '不是 JSON'}, 'finish_reason': 'stop'}]})
    plan = packaging.plan(['一句'], 'x', 'https://synthetic.invalid', 'TEST', 'stub')
    assert plan == {'highlights': [[]], 'stickers': [], 'sfx': [], 'flower': []}


def test_plan_flags_drop_disabled_categories(monkeypatch):
    body = '{"highlights":{"1":["第一句"]},"stickers":[{"line":2,"icon":"money"}],"sfx":[{"line":2,"sound":"coin"}]}'
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': body}, 'finish_reason': 'stop'}]})
    plan = packaging.plan(['第一句文案', '第二句99块9'], '商品', 'https://synthetic.invalid', 'TEST', 'stub',
                          want_highlights=False, want_stickers=False, want_sfx=True)
    assert plan['highlights'] == [[], []]
    assert plan['stickers'] == []
    assert plan['sfx'] == [{'line': 2, 'sound': 'coin', 'gain': -6.0}]  # price beat still qualifies
    muted = packaging.plan(['第一句文案', '第二句99块9'], '商品', 'https://synthetic.invalid', 'TEST', 'stub', want_sfx=False)
    assert muted['sfx'] == []
