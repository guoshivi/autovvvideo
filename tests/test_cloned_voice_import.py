"""Cloned-voice folder contract: dropped audio becomes a selectable voice; WAV
files are used as-is, other formats are transcoded once into the cache; a
missing file is refused with a clear error and never replaced by another voice.
CPU-only synthetic doubles; not a target-GPU speech test.
"""
import hashlib
import shutil
import subprocess
import wave
from unittest.mock import patch
import numpy as np
import pytest
from workbench.settings import ProductionSettings
from workbench.voxcpm_worker import _prepare_clone_reference, synthesize
from workbench.voice_engines import (VoiceEngineError, VOX_PROMPT_PHRASE,
                                     is_clone_voice_id, list_clone_voices,
                                     resolve_clone_voice)
from test_vox_parent_contract import SyntheticCore


def test_clone_id_rules():
    assert is_clone_voice_id('女声 1') and is_clone_voice_id('voice_2')
    assert not is_clone_voice_id('') and not is_clone_voice_id('.') and not is_clone_voice_id('..')
    assert not is_clone_voice_id('.hidden') and not is_clone_voice_id('a/b') and not is_clone_voice_id('a\\b')
    assert not is_clone_voice_id(' x') and not is_clone_voice_id('x ') and not is_clone_voice_id('x' * 81)


def test_clone_folder_scan_and_resolve(tmp_path):
    d = tmp_path / 'voices'; d.mkdir()
    (d / '女声 1.wav').write_bytes(b'RIFF0000')
    (d / 'voice2.mp3').write_bytes(b'MP3')
    (d / 'voice2.wav').write_bytes(b'WAV2')     # same stem: wav wins by extension priority
    (d / '.hidden.wav').write_bytes(b'X')
    (d / 'natural_female.wav').write_bytes(b'X')  # built-in shadow is skipped
    (d / 'sub.wav').mkdir()                       # directories never count
    try: (d / 'link.wav').symlink_to(d / '女声 1.wav')
    except OSError: pass
    rows = list_clone_voices(d)
    assert [r['id'] for r in rows] == ['voice2', '女声 1']
    assert resolve_clone_voice(d, 'voice2') == d / 'voice2.wav'
    assert resolve_clone_voice(d, '女声 1') == d / '女声 1.wav'
    assert resolve_clone_voice(d, 'link') is None
    assert resolve_clone_voice(d, 'natural_female') is None
    assert resolve_clone_voice(d, 'missing') is None
    assert list_clone_voices(tmp_path / 'nope') == []


def test_settings_accept_clone_name_reject_path_like():
    s = ProductionSettings.from_dict({'voice_engine': 'vox', 'vox_voice': '女声 1'})
    assert s.vox_voice == '女声 1'
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({'vox_voice': 'a/b'})


def test_clone_reference_wav_used_as_is(tmp_path):
    ffmpeg = shutil.which('ffmpeg'); assert ffmpeg
    src = tmp_path / '女声 1.wav'
    with wave.open(str(src), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(44100)
        f.writeframes((np.sin(np.arange(44100) * 0.1) * 1000).astype('<i2').tobytes())
    assert _prepare_clone_reference(src, tmp_path / 'cache', ffmpeg) == src


def test_non_wav_clone_transcoded_once_into_cache(tmp_path):
    ffmpeg = shutil.which('ffmpeg'); assert ffmpeg
    src_wav = tmp_path / 'src.wav'
    with wave.open(str(src_wav), 'wb') as f:
        f.setnchannels(2); f.setsampwidth(2); f.setframerate(48000)
        left = (np.sin(np.arange(48000) * 0.1) * 1000).astype('<i2')
        f.writeframes(np.column_stack([left, left]).tobytes())
    src = tmp_path / 'voice.mp3'
    subprocess.run([ffmpeg, '-y', '-v', 'error', '-i', str(src_wav), '-c:a', 'libmp3lame', '-b:a', '96k', str(src)], check=True)
    cache = tmp_path / 'cache'
    out1 = _prepare_clone_reference(src, cache, ffmpeg)
    assert out1.parent.name == '_imports'
    with wave.open(str(out1), 'rb') as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 24000)
    mtime = out1.stat().st_mtime_ns
    assert _prepare_clone_reference(src, cache, ffmpeg) == out1
    assert out1.stat().st_mtime_ns == mtime


def test_dropped_voice_synthesis_uses_file_unchanged(tmp_path):
    model = tmp_path / 'model'; model.mkdir()
    voices = tmp_path / 'voices'; voices.mkdir()
    src = voices / '女声 1.wav'
    with wave.open(str(src), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(44100)
        f.writeframes((np.sin(np.arange(44100) * 0.1) * 1000).astype('<i2').tobytes())
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    cache = tmp_path / 'cache'
    calls = []

    class SyntheticPipeline:
        sample_rate = 48000
        def __init__(self): self.tts_model = SyntheticCore()
        def generate(self, **kw):
            calls.append(dict(kw))
            g = self.tts_model._inference(min_len=2, max_len=8, streaming=False)
            try: next(g)
            finally: g.close()
            n = 96000 if 'prompt_wav_path' not in kw else 48000
            return np.sin(np.arange(n, dtype=np.float64) * 0.05) * 0.1

    request = {'lines': ['克隆文件夹声音的合成测试'], 'voice': '女声 1', 'vox_clone_dir': str(voices),
               'voxcpm_model': str(model), 'voxcpm_cache': str(cache), 'ffmpeg': shutil.which('ffmpeg'), 'speed': 1.0}
    with patch('workbench.voxcpm_worker._seed_all'), \
         patch('workbench.voxcpm_worker._normalize_loudness', return_value=None):
        result = synthesize(request, tmp_path / 'output', model_factory=SyntheticPipeline)
    assert result['success'] is True and result['anchor_cold_start'] is False
    assert len(calls) == 2 and calls[0]['reference_wav_path'] == str(src)
    assert 'prompt_wav_path' not in calls[0] and 'prompt_text' not in calls[0]
    assert calls[1]['prompt_wav_path'] == str(cache / '女声 1' / 'prompt.wav')
    assert calls[1]['reference_wav_path'] == str(src) and calls[1]['prompt_text'] == VOX_PROMPT_PHRASE
    assert result['anchor']['kind'] == 'clone' and result['anchor']['source_file'] == '女声 1.wav'
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert (cache / '女声 1' / 'prompt.wav').is_file()
    assert (cache / '女声 1' / 'prompt.json').is_file()
    assert result['prompt']['cold_start'] is True and result['prompt']['rebuilt'] is False
    assert result['prompt']['phrase'] == VOX_PROMPT_PHRASE
    assert result['prompt']['path'] == str(cache / '女声 1' / 'prompt.wav')
    with wave.open(str(tmp_path / 'output' / 'voice_000.wav'), 'rb') as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (1, 2, 24000)


def test_missing_dropped_voice_is_refused(tmp_path):
    model = tmp_path / 'model'; model.mkdir()
    voices = tmp_path / 'voices'; voices.mkdir()
    cache = tmp_path / 'cache'

    class RefusingPipeline:
        sample_rate = 48000
        def __init__(self): self.tts_model = SyntheticCore()
        def generate(self, **kw): raise AssertionError('must not generate')

    request = {'lines': ['x'], 'voice': '女声 1', 'vox_clone_dir': str(voices),
               'voxcpm_model': str(model), 'voxcpm_cache': str(cache), 'ffmpeg': 'ffmpeg', 'speed': 1.0}
    with pytest.raises(VoiceEngineError):
        synthesize(request, tmp_path / 'output', model_factory=RefusingPipeline)
