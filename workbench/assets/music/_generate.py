#!/usr/bin/env python3
"""Generate five royalty-free instrumental beds for the workbench music tab.

Everything is synthesized locally (no samples, no third-party audio), so the
output can be shipped with the app without a music licence.  The tracks are
deliberately simple loops: pads, bass, light percussion and a pluck line.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent


def env(n: int, a: float, d: float, s: float, r: float, dur: float) -> np.ndarray:
    t = np.arange(n) / SR
    at = np.clip(t / max(a, 1e-4), 0, 1)
    dt = np.clip((dur - t) / max(r, 1e-4), 0, 1)
    body = np.where(t < a, at, 1.0)
    body = np.where(t > dur - r, body * dt, body)
    return body * (s + (1 - s) * np.exp(-t / max(d, 1e-4)))


def tone(freq: float, dur: float, kind: str = "sine", gain: float = 0.2) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    if kind == "sine":
        x = np.sin(2 * np.pi * freq * t)
    elif kind == "tri":
        x = 2 * np.abs(2 * ((freq * t) % 1) - 1) - 1
    elif kind == "pluck":
        x = sum(np.sin(2 * np.pi * freq * k * t) / (k * 1.8) for k in range(1, 6))
        x *= np.exp(-4.5 * t)
    else:
        x = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
    return x * env(n, 0.01, 0.3, 0.6, 0.2, dur) * gain


def kick(dur: float = 0.28, gain: float = 0.7) -> np.ndarray:
    n = int(dur * SR); t = np.arange(n) / SR
    f = 120 * np.exp(-18 * t) + 44
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-9 * t)
    return x * gain


def hat(dur: float = 0.06, gain: float = 0.12) -> np.ndarray:
    n = int(dur * SR)
    x = np.random.default_rng(7).normal(0, 1, n) * np.exp(-60 * np.arange(n) / SR)
    return x * gain


def snare(dur: float = 0.18, gain: float = 0.3) -> np.ndarray:
    n = int(dur * SR); t = np.arange(n) / SR
    x = np.random.default_rng(11).normal(0, 1, n) * np.exp(-24 * t)
    x += 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-20 * t)
    return x * gain


def add(buf: np.ndarray, x: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(at * SR); j = min(len(buf), i + len(x))
    if i < len(buf):
        buf[i:j] += x[: j - i] * gain


def chord(root: float, intervals: list[int], dur: float, gain: float = 0.05) -> np.ndarray:
    n = int(dur * SR)
    out = np.zeros(n)
    for k, semi in enumerate(intervals):
        f = root * 2 ** (semi / 12)
        out += tone(f, dur, "tri", gain) * (1.0 if k == 0 else 0.7)
    return out


def make(mood: str, bpm: int, bars: int, root: float, prog: list[list[int]],
         drums: str, arp: bool, dur: float = 60.0) -> np.ndarray:
    beat = 60.0 / bpm
    bar = beat * 4
    total = int(dur * SR)
    mix = np.zeros(total)

    # Pads: one chord per bar, looping the progression.
    for b in range(int(dur / bar) + 1):
        c = prog[b % len(prog)]
        ch = chord(root, c, bar * 1.02, 0.045 if mood in ("calm", "sad") else 0.035)
        add(mix, ch, b * bar)

    # Bass: root on beats 1 and 3 (or eighths for dynamic).
    for b in range(int(dur / bar) + 1):
        c = prog[b % len(prog)]
        f = root * 2 ** (c[0] / 12) / 2
        if drums == "dynamic":
            for k in range(8):
                add(mix, tone(f, beat / 2 * 0.9, "sine", 0.11), b * bar + k * beat / 2)
        else:
            add(mix, tone(f, beat * 1.6, "sine", 0.12), b * bar)
            add(mix, tone(f, beat * 1.4, "sine", 0.09), b * bar + beat * 2)

    # Pluck arpeggio.
    if arp:
        pattern = [0, 2, 1, 3, 2, 1, 3, 2]
        for b in range(int(dur / bar) + 1):
            c = prog[b % len(prog)]
            for k, idx in enumerate(pattern):
                semi = c[idx % len(c)] + 12
                add(mix, tone(root * 2 ** (semi / 12), beat * 0.45, "pluck", 0.055),
                    b * bar + k * beat / 2)

    # Percussion.
    for b in range(int(dur / bar) + 1):
        base = b * bar
        if drums in ("four", "dynamic"):
            for k in range(4):
                add(mix, kick(gain=0.65 if k == 0 else 0.5), base + k * beat)
        elif drums == "soft":
            add(mix, kick(gain=0.45), base)
            add(mix, kick(gain=0.3), base + beat * 2.5)
        if drums in ("four", "dynamic", "soft"):
            for k in range(8):
                add(mix, hat(gain=0.09 if k % 2 else 0.05), base + k * beat / 2)
        if drums in ("four", "dynamic"):
            add(mix, snare(gain=0.22), base + beat)
            add(mix, snare(gain=0.22), base + beat * 3)

    # Gentle low-pass: one-pole, then soft clip and fades.
    alpha = 0.22 if mood in ("calm", "sad") else 0.35
    out = np.empty_like(mix)
    prev = 0.0
    for i, v in enumerate(mix):
        prev += alpha * (v - prev)
        out[i] = prev
    out = np.tanh(out * 1.6) / 1.6
    fade = int(1.2 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(np.abs(out))) or 1.0
    out = out / peak * 0.85
    return np.stack([out, out], axis=1)


TRACKS = [
    ("excited", "兴奋", 124, 4, 261.63, [[0, 4, 7], [5, 9, 12], [7, 11, 14], [5, 9, 12]], "four", True),
    ("happy", "愉悦", 104, 4, 392.00, [[0, 4, 7], [7, 11, 14], [9, 12, 16], [5, 9, 12]], "soft", True),
    ("calm", "平静", 72, 4, 293.66, [[0, 7, 14], [5, 12, 19], [9, 16, 21], [5, 12, 19]], "none", False),
    ("sad", "悲伤", 64, 4, 220.00, [[0, 3, 7], [5, 8, 12], [3, 7, 10], [7, 10, 14]], "none", True),
    ("dynamic", "动感", 128, 4, 164.81, [[0, 3, 7], [0, 3, 7], [5, 8, 12], [3, 7, 10]], "dynamic", True),
]

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for key, mood, bpm, bars, root, prog, drums, arp in TRACKS:
        data = make(key, bpm, bars, root, prog, drums, arp)
        pcm = np.clip(data, -1, 1)
        pcm = (pcm * 32767).astype("<i2")
        path = OUT / f"{key}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        print(path.name, path.stat().st_size)
