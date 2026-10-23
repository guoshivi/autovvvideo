import pytest

from workbench.pronunciation import PronunciationError, apply_rules, normalize_rules
from workbench.settings import ProductionSettings


def test_rules_apply_longest_first_and_leave_other_text():
    lines = ["银行行长来了", "这一行很长"]
    out = apply_rules(lines, [{"from": "行", "to": "航"}, {"from": "行长", "to": "航长"}])
    assert out[0] == "银航航长来了"
    assert out[1] == "这一航很长"


def test_empty_rules_keep_lines():
    assert apply_rules(["原句"], []) == ["原句"]
    assert apply_rules(["原句"], None) == ["原句"]


def test_rules_reject_bad_rows():
    with pytest.raises(PronunciationError):
        normalize_rules([{"from": "", "to": "x"}])
    with pytest.raises(PronunciationError):
        normalize_rules([{"from": "行", "to": "行"}])
    with pytest.raises(PronunciationError):
        normalize_rules([{"from": "行", "to": "航", "extra": 1}])
    with pytest.raises(PronunciationError):
        normalize_rules([{"from": "x", "to": "y"}] * 51)


def test_settings_normalises_pronunciation_and_volume():
    s = ProductionSettings.from_dict({
        "voice_volume": 0.6,
        "pronunciations": [{"from": "行", "to": "航"}],
    })
    assert s.voice_volume == 0.6
    assert s.pronunciations == [{"from": "行", "to": "航"}]
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({"voice_volume": 1.5}).validate()
