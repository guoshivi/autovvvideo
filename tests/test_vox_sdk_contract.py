"""Strict pinned-SDK call contract. Synthetic waveforms are not real speech.
The signature fixture was extracted from the SHA-verified voxcpm 2.0.3 wheel.
"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from workbench.voxcpm_worker import synthesize
from workbench.voice_engines import preset
from test_vox_parent_contract import SyntheticCore


def test_full_adapter_uses_native_sdk_signature_and_voice_design_format(tmp_path):
    fixture=json.loads((Path(__file__).parent/'fixtures/vox_sdk_contract.json').read_text())
    manifest=json.loads((Path(__file__).parents[1]/'deploy/vox_dependencies.json').read_text())
    assert fixture['sdk_sha256']==manifest['sdk']['sha256']
    parameters=[inspect.Parameter(name,inspect.Parameter.POSITIONAL_OR_KEYWORD,
                 default=fixture['defaults'].get(name,inspect.Parameter.empty)) for name in fixture['parameters']]
    signature=inspect.Signature(parameters);calls=[]
    class StrictSyntheticSDK:
        sample_rate=48000
        def __init__(self):self.tts_model=SyntheticCore()
        def generate(self,**kwargs):
            # This is the real pinned SDK's generate -> _generate binding shape.
            bound=signature.bind(streaming=False,**kwargs);bound.apply_defaults();calls.append(dict(bound.arguments))
            generator=self.tts_model._inference(min_len=bound.arguments['min_len'],max_len=bound.arguments['max_len'],streaming=False)
            try:next(generator)
            finally:generator.close()
            return np.sin(np.arange(48000,dtype=np.float64)*0.05)*0.1
    model=tmp_path/'model';model.mkdir()
    with patch('workbench.voxcpm_worker._seed_all') as seed, \
         patch('workbench.voxcpm_worker._normalize_loudness',return_value=None):
        result=synthesize({'lines':['SDK协议第一句','SDK协议第二句'],'voice':'natural_female',
                           'voxcpm_model':str(model),'voxcpm_cache':str(tmp_path/'cache'),
                           'ffmpeg':'ffmpeg','speed':1},tmp_path/'output',model_factory=StrictSyntheticSDK)
    assert result['success'] and len(result['voices'])==2
    style=preset('natural_female')
    assert calls[0]['text']==fixture['voice_design_format'].format(description=style['description'],text=style['anchor'])
    assert calls[0]['reference_wav_path'] is None
    assert calls[1]['reference_wav_path']==calls[2]['reference_wav_path']
    assert calls[1]['text']=='SDK协议第一句' and calls[2]['text']=='SDK协议第二句'
    assert all('seed' not in call for call in calls)
    assert [call.args[0] for call in seed.call_args_list]==[style['seed']]*3
