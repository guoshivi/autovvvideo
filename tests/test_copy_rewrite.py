import json
import pytest
from workbench import aicopy


def test_rewrite_build_messages_has_independent_untrusted_copy_prompt():
    messages = aicopy.build_messages(
        'rewrite',
        '第一稿内容\n---\n第二稿内容',
        {'name': '清洁刷', 'reason': '平台提示功效夸大'},
    )
    assert len(messages) == 2
    system = messages[0]['content']
    user = messages[1]['content']
    assert '改写' in system
    assert system != aicopy._SYSTEM
    assert '不可信' in system
    assert '清洁刷' in user and '平台提示功效夸大' in user
    assert '第一稿内容' in user and '第二稿内容' in user
    assert '图片' not in user and '音频' not in user and '路径' not in user


def test_rewrite_run_uses_stub_post_and_preserves_two_script_shape(monkeypatch):
    seen = {}

    def fake_post(url, payload, key, timeout=90):
        seen['payload'] = payload
        return {'choices': [{'message': {'content': '第一稿改写\n---\n第二稿改写'}}]}

    monkeypatch.setattr(aicopy, '_post', fake_post)
    out = aicopy.run('http://localhost:8080/v1', 'stub-key', 'stub', 'rewrite',
                     '第一稿内容\n---\n第二稿内容', {'name': '清洁刷', 'reason': '功效夸大'})
    assert out == '第一稿改写\n---\n第二稿改写'
    assert seen['payload']['messages'][0]['role'] == 'system'
    assert seen['payload']['messages'][1]['role'] == 'user'
    assert seen['payload']['temperature'] == 0.5


@pytest.mark.parametrize('text,reason,msg', [
    ('', '', '先在编辑框里写好文案'),
    ('x' * 3001, '', '文案不能超过 3000'),
    ('有效文案', 'x' * 2001, '拒审原因不能超过 2000'),
])
def test_rewrite_input_limits(text, reason, msg):
    with pytest.raises(aicopy.AIError, match=msg):
        aicopy.build_messages('rewrite', text, {'name': '商品', 'reason': reason})


def test_rewrite_rejects_block_count_mismatch(monkeypatch):
    monkeypatch.setattr(aicopy, '_post', lambda *args, **kwargs: {
        'choices': [{'message': {'content': '只返回一稿'}}]
    })
    with pytest.raises(aicopy.AIError, match='稿件数量'):
        aicopy.run('http://localhost:8080/v1', 'stub-key', 'stub', 'rewrite',
                   '第一稿\n---\n第二稿', {'name': '商品', 'reason': ''})


def test_rewrite_rejects_empty_and_overlong_model_output(monkeypatch):
    monkeypatch.setattr(aicopy, '_post', lambda *args, **kwargs: {
        'choices': [{'message': {'content': '   '}}]
    })
    with pytest.raises(aicopy.AIError, match='没有返回文字'):
        aicopy.run('http://localhost:8080/v1', 'stub-key', 'stub', 'rewrite', '稿子', {})

    monkeypatch.setattr(aicopy, '_post', lambda *args, **kwargs: {
        'choices': [{'message': {'content': 'x' * 3001}}]
    })
    with pytest.raises(aicopy.AIError, match='过长'):
        aicopy.run('http://localhost:8080/v1', 'stub-key', 'stub', 'rewrite', '稿子', {})


def test_rewrite_maps_provider_failure_to_safe_ai_error(monkeypatch):
    def fail(*args, **kwargs):
        raise aicopy.AIError('连不上 AI 服务，请确认这台 Win 机可以上网')

    monkeypatch.setattr(aicopy, '_post', fail)
    with pytest.raises(aicopy.AIError, match='连不上 AI 服务'):
        aicopy.run('http://localhost:8080/v1', 'stub-key', 'stub', 'rewrite', '稿子', {})


@pytest.mark.parametrize('finish_reason', ['length', 'content_filter', 'tool_calls', 'function_call', None, ''])
def test_r2_rewrite_rejects_nonterminal_metadata_without_changing_legacy(monkeypatch, finish_reason):
    seen = []
    def fake_post(url, payload, key):
        seen.append(payload)
        return {'choices': [{'message': {'content': '未完成的半句'}, 'finish_reason': finish_reason}]}
    monkeypatch.setattr(aicopy, '_post', fake_post)
    with pytest.raises(aicopy.AIError, match='未完整结束'):
        aicopy.run('https://synthetic.invalid', 'TEST', 'stub', 'rewrite', '原稿', {})
    assert seen[-1]['max_tokens'] == 6000
    assert aicopy.run('https://synthetic.invalid', 'TEST', 'stub', 'polish', '原稿', {}) == '未完成的半句'
    assert seen[-1]['max_tokens'] == 800


def test_r2_prompt_requires_removing_entire_risk_chain_not_synonyms():
    # Prompt contract only; this is NOT a semantic evaluation of a live model.
    system = aicopy.build_messages('rewrite', '长辈不得劲，酸胀贴一贴，忙完松快',
                                  {'name': '日用贴布', 'reason': '非医疗日用品不能承诺缓解酸痛改善不适'})[0]['content']
    assert '删除整条症状、人群与体验因果链' in system
    assert '不能换成需要→使用→放松' in system
    assert '不得补写家人常备' in system
    assert '不凑长度、不补卖点' in system


def test_r3_rewrite_prompt_forbids_unsupported_convenience_replacements():
    # Prompt contract, not a claim about a live model's output.
    system = aicopy.build_messages('rewrite', '酸胀贴一贴就松快', {'name': '日用贴布'})[0]['content']
    assert '删除风险承诺后' in system
    assert '方便、省心、好用、舒适' in system
    assert '未证实评价' in system
    assert '中性名称和查看标签及使用说明的建议' in system
    assert '不评价易用性、不新增功效或使用场景' in system
    assert '删除风险承诺后' not in aicopy.build_messages('polish', '原稿', {})[0]['content']
