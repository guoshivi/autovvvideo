"""Sentence pacing and deterministic WAV edge trimming helpers."""
from pathlib import Path
import wave
import math

import numpy as np


SENTENCE_GAP_SECONDS = 0.18
SENTENCE_MIN_SECONDS = 1.0
SENTENCE_PAUSE_CAP_SECONDS = 0.35
EDGE_HEAD_KEEP_MS = 20
EDGE_TAIL_KEEP_MS = 40
EDGE_ZERO_THRESHOLD = 12


def voice_slot_seconds(voice_seconds: float) -> float:
    """Return the video slot for one measured voice sentence."""
    voice_seconds = float(voice_seconds)
    return max(
        voice_seconds + SENTENCE_GAP_SECONDS,
        min(SENTENCE_MIN_SECONDS, voice_seconds + SENTENCE_PAUSE_CAP_SECONDS),
    )


def estimate_slot_seconds(chars: int, cps: float) -> float:
    """Estimate a sentence slot from character count and speaking rate."""
    cps = float(cps)
    if cps <= 0:
        raise ValueError("cps must be positive")
    return voice_slot_seconds(float(chars) / cps)


def sentence_slot_seconds(voice_seconds: float, sentence_gap_seconds=None, *, is_last=False) -> float:
    """Return one sentence's slot, preserving the legacy final tail pad.

    ``None`` keeps the established automatic 0.18–0.35 second rhythm.  An
    explicit gap applies only before a following sentence; the final sentence
    retains the old tail padding so a manual setting cannot add dead air at
    the end of a film.
    """
    if sentence_gap_seconds is None or is_last:
        return voice_slot_seconds(voice_seconds)
    gap=float(sentence_gap_seconds)
    if not math.isfinite(gap) or not 0.10-1e-9<=gap<=1.00+1e-9:
        raise ValueError('sentence gap must be 0.10..1.00 seconds')
    return float(voice_seconds)+gap


def _trim_error():
    return RuntimeError("voice/trim: 音频全静音或格式异常")


def trim_wav_edges(
    path,
    head_keep_ms=EDGE_HEAD_KEEP_MS,
    tail_keep_ms=EDGE_TAIL_KEEP_MS,
    threshold=EDGE_ZERO_THRESHOLD,
) -> int:
    """Trim only long silent edges from a mono 24 kHz PCM16 WAV in place."""
    p = Path(path)
    try:
        with wave.open(str(p), "rb") as f:
            if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 24000):
                raise _trim_error()
            raw = f.readframes(f.getnframes())
        samples = np.frombuffer(raw, dtype="<i2").copy()
        if samples.ndim != 1 or not len(samples):
            raise _trim_error()
        active = np.flatnonzero(np.abs(samples) > int(threshold))
        if not len(active):
            raise _trim_error()
        head = max(0, int(round(float(head_keep_ms) * 24)))
        tail = max(0, int(round(float(tail_keep_ms) * 24)))
        start = max(0, int(active[0]) - head)
        end = min(len(samples), int(active[-1]) + 1 + tail)
        clipped = samples[start:end]
        if not len(clipped):
            raise _trim_error()
        with wave.open(str(p), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(24000)
            f.writeframes(clipped.astype("<i2", copy=False).tobytes())
        return int(len(clipped))
    except RuntimeError:
        raise
    except Exception as exc:
        raise _trim_error() from exc
