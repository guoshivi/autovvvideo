"""Parent-authored adapter contract probes.

SDK protocol doubles below are deliberately synthetic; the WAV conversion test
uses actual ffmpeg. Neither proves target-GPU speech quality.
"""
import json
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pytest
from workbench.voxcpm_worker import VoxStopObserver, _convert_48k_to_24k, _write_anchor, _validate_anchor, synthesize
from workbench.voice_engines import preset, anchor_metadata


class Scalar:
    def __init__(self,value):self.value=value
    def argmax(self,dim=-1):return self
    def __getitem__(self,key):return self
    def detach(self):return self
    def cpu(self):return self
    def item(self):return self.value


class SyntheticStopHead:
    def register_forward_hook(self,hook):
        self.hook=hook
        return SimpleNamespace(remove=lambda:None)
    def decide(self,value):
        self.hook(self,(),Scalar(value))


class SyntheticCore:
    def __init__(self):self.stop_head=SyntheticStopHead()
    def _inference(self,*args,min_len=2,max_len=8,**kwargs):
        for i in range(4):self.stop_head.decide(1 if i==3 else 0)
        yield 'synthetic-latent', 'synthetic-feature', 0


def test_observer_handles_real_sdk_next_then_close_lifecycle():
    core=SyntheticCore();observer=VoxStopObserver();observer.install(core)
    generator=core._inference(min_len=2,max_len=8,streaming=False)
    try:
        next(generator)
    finally:
        generator.close()  # The pinned SDK's next_and_close does exactly this.
    result=observer.final()
    assert result['natural_stop'] is True
    assert result['executed']==4
    observer.close()


def test_actual_ffmpeg_conversion_can_choose_wav_muxer(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    assert ffmpeg, 'the parent ffmpeg tool must exist; do not skip real conversion'
    audio=np.sin(np.arange(48000,dtype=np.float64)*0.05)*0.1
    dest=tmp_path/'voice_000.wav'
    n=_convert_48k_to_24k(audio,48000,dest,ffmpeg)
    with wave.open(str(dest),'rb') as f:
        assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getnframes())==(1,2,24000,n)
    assert n==24000


def test_anchor_generation_really_uses_preset_description(tmp_path):
    cache=tmp_path/'cache';cache.mkdir();model=tmp_path/'model';model.mkdir();calls=[]
    def generate(**kw):
        calls.append(kw)
        return np.ones(4800)*0.1
    def synthetic_convert(arr,rate,dest,ffmpeg):
        with wave.open(str(dest),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(np.ones(2400,dtype='<i2').tobytes())
        return 2400
    observer=SimpleNamespace(final=lambda:{'natural_stop':True})
    with patch('workbench.voxcpm_worker._seed_all'), \
         patch('workbench.voxcpm_worker._convert_48k_to_24k',side_effect=synthetic_convert), \
         patch('workbench.voxcpm_worker._normalize_loudness',return_value=None):
        _write_anchor(cache,'natural_female',model,SimpleNamespace(generate=generate),observer,'synthetic-ffmpeg')
    assert preset('natural_female')['description'] in calls[0]['text']


def test_anchor_cache_rejects_seed_and_style_identity_drift(tmp_path):
    cache=tmp_path/'cache';cache.mkdir();model=tmp_path/'model';model.mkdir()
    # This file is an explicitly synthetic WAV fixture, never represented as TTS.
    def synthetic_convert(arr,rate,dest,ffmpeg):
        with wave.open(str(dest),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(np.ones(2400,dtype='<i2').tobytes())
        return 2400
    with patch('workbench.voxcpm_worker._seed_all'), \
         patch('workbench.voxcpm_worker._convert_48k_to_24k',side_effect=synthetic_convert), \
         patch('workbench.voxcpm_worker._normalize_loudness',return_value=None):
        _write_anchor(cache,'calm_male',model,SimpleNamespace(generate=lambda **kw:np.ones(4800)*0.1),SimpleNamespace(final=lambda:{'natural_stop':True}),'synthetic-ffmpeg')
    meta=cache/'calm_male'/'anchor.json';data=json.loads(meta.read_text())
    data['seed']=0;data['description']='another voice style'
    meta.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(RuntimeError):
        _validate_anchor(cache,'calm_male',model)


def test_synthesize_speed_changes_actual_samples_not_just_receipt(tmp_path):
    model=tmp_path/'model';model.mkdir();cache=tmp_path/'cache';folder=cache/'calm_male';folder.mkdir(parents=True)
    anchor=folder/'anchor.wav'
    with wave.open(str(anchor),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
        f.writeframes((np.sin(np.arange(24000)*0.1)*1000).astype('<i2').tobytes())
    style=preset('calm_male')
    metadata=anchor_metadata('calm_male',str(model),anchor,seed=style['seed'],description=style['description'])
    (folder/'anchor.json').write_text(json.dumps(metadata),encoding='utf-8')
    class SyntheticPipeline:
        sample_rate=48000
        def __init__(self):self.tts_model=SyntheticCore()
        def generate(self,**kw):
            # Follow the pinned SDK's non-streaming first-yield/close protocol.
            g=self.tts_model._inference(min_len=2,max_len=8,streaming=False)
            try:next(g)
            finally:g.close()
            n = 96000 if (kw.get('reference_wav_path') and kw.get('prompt_wav_path') is None) else 48000
            return np.sin(np.arange(n,dtype=np.float64)*0.05)*0.1
    request={'lines':['显式合成模型协议测试，不代表真实配音'], 'vox_voice':'calm_male',
             'voxcpm_model':str(model),'voxcpm_cache':str(cache),
             'ffmpeg':shutil.which('ffmpeg'),'speed':1.25}
    with patch('workbench.voxcpm_worker._seed_all'), patch('workbench.voxcpm_worker._normalize_loudness',return_value=None):
        result=synthesize(request,tmp_path/'output',model_factory=SyntheticPipeline)
    seconds=result['voices'][0]['samples']/24000
    assert 0.74<seconds<0.86, seconds
    assert result['speed']==1.25
