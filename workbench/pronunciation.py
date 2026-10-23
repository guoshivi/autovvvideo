"""Pronunciation replacements applied only to the TTS input.

The subtitle still shows the author's original sentence; only the text sent to
the voice engine is rewritten.  Rules are ordered longest-first so a short rule
cannot corrupt a longer phrase that contains it.
"""
from __future__ import annotations


class PronunciationError(ValueError):
    pass


def normalize_rules(rules) -> list[dict]:
    if rules is None:
        return []
    if not isinstance(rules, list):
        raise PronunciationError("读音矫正必须是列表")
    if len(rules) > 50:
        raise PronunciationError("读音矫正最多 50 条")
    out = []
    for row in rules:
        if not isinstance(row, dict) or set(row) - {"from", "to"}:
            raise PronunciationError("每条读音矫正只能包含原词和读作")
        src = row.get("from")
        dst = row.get("to")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise PronunciationError("读音矫正的原词和读作必须是文字")
        src, dst = src.strip(), dst.strip()
        if not src or not dst:
            raise PronunciationError("读音矫正的原词和读作不能为空")
        if src == dst:
            raise PronunciationError("读音矫正的原词和读作不能相同")
        if len(src) > 20 or len(dst) > 40:
            raise PronunciationError("读音矫正的词过长")
        if any(ord(c) < 32 for c in src + dst):
            raise PronunciationError("读音矫正不能包含控制字符")
        out.append({"from": src, "to": dst})
    return out


def apply_rules(lines, rules) -> list[str]:
    clean = normalize_rules(rules)
    ordered = sorted(clean, key=lambda r: len(r["from"]), reverse=True)
    out = []
    for line in lines or []:
        text = str(line)
        for rule in ordered:
            text = text.replace(rule["from"], rule["to"])
        out.append(text)
    return out
