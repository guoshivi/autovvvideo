import json

import pytest

from workbench import aicopy, highlight_ai


def test_validate_keeps_only_words_that_exist_in_their_own_line():
    lines = ['怎么分辨真假呢', '今天50贴只要99块9']
    data = {'1': ['分辨真假', '不存在的词'], '2': ['99块9', '50贴', '第三个']}
    assert highlight_ai.validate(lines, data) == [['分辨真假'], ['99块9', '50贴']]


def test_validate_drops_medical_claims_and_bad_types():
    lines = ['贴上就酸胀缓解', '正宗测试草本膏']
    data = {'1': ['酸胀', '缓解'], '2': ['正宗', 3, '']}
    assert highlight_ai.validate(lines, data) == [[], ['正宗']]


def test_validate_rejects_overlapping_words():
    lines = ['正宗测试草本膏']
    assert highlight_ai.validate(lines, {'1': ['正宗', '正宗金门']}) == [['正宗']]


def test_select_parses_json_and_rejects_markdown_noise(monkeypatch):
    seen = {}

    def fake_post(url, payload, key, timeout=60):
        seen['payload'] = payload
        return {'choices': [{'message': {'content': '```json\n{"1":["分辨真假"],"2":["99块9"]}\n```'}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr(aicopy, '_post', fake_post)
    out = highlight_ai.select(['怎么分辨真假呢', '今天只要99块9'], '测试草本膏', 'https://synthetic.invalid', 'TEST', 'stub')
    assert out == [['分辨真假'], ['99块9']]
    assert seen['payload']['messages'][0]['role'] == 'system'
    assert '不可信' not in seen['payload']['messages'][1]['content']


def test_select_returns_empty_on_transport_or_json_failure(monkeypatch):
    def broken(*args, **kwargs):
        raise aicopy.AIError('offline')

    monkeypatch.setattr(aicopy, '_post', broken)
    assert highlight_ai.select(['原稿'], 'x', 'https://synthetic.invalid', 'TEST', 'stub') == []

    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: {'choices': [{'message': {'content': '不是 JSON'}, 'finish_reason': 'stop'}]})
    assert highlight_ai.select(['原稿'], 'x', 'https://synthetic.invalid', 'TEST', 'stub') == []


def test_select_without_key_never_calls_the_provider(monkeypatch):
    monkeypatch.setattr(aicopy, '_post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not call')))
    assert highlight_ai.select(['原稿'], 'x', 'https://synthetic.invalid', '', 'stub') == []
