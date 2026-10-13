"""Parent-owned voice routing/preview tests with explicit synthetic process receipts.
No generated fixture in this file is claimed to be real Qwen/Vox speech.
"""
import hashlib
import json
import wave
from pathlib import Path
from unittest.mock import patch
import pytest
from workbench.config import RuntimeConfig
from workbench.settings import ProductionSettings
from workbench.store import Store
from workbench.queue import JobQueue
from workbench.service import WorkbenchService, ServiceError
from workbench.voice_engines import validate_preview_payload, VoiceEngineError


@pytest.fixture
def service(tmp_path):
    data=tmp_path/'data';material=tmp_path/'materials';material.mkdir()
    qpy=tmp_path/'qwen-python';qpy.write_text('explicit synthetic executable fixture')
    vpy=tmp_path/'vox-python';vpy.write_text('explicit synthetic executable fixture')
    qm=tmp_path/'qwen-model';qm.mkdir();vm=tmp_path/'vox-model';vm.mkdir()
    cfg=RuntimeConfig(data_root=str(data),nas_roots=[str(material)],gpu_lock_path='',
                      tts_python=str(qpy),tts_model=str(qm),model_dir='',
                      voxcpm_python=str(vpy),voxcpm_model=str(vm),voxcpm_cache=str(data/'voice-cache'))
    return WorkbenchService(cfg,Store(data/'records.json'),JobQueue(data/'jobs.json'),start=False)


def test_legacy_settings_default_to_qwen():
    settings=ProductionSettings.from_dict({'speaker':'Vivian','speed':1.2}).snapshot()
    assert settings['voice_engine']=='qwen'
    assert settings['speaker']=='Vivian'
    assert settings['speed']==1.2


@pytest.mark.parametrize('engine',[None,True,[],{},'other'])
def test_invalid_engine_is_rejected_cleanly(engine):
    with pytest.raises(ValueError):
        ProductionSettings.from_dict({'voice_engine':engine})


def test_engine_dispatch_does_not_replace_original_qwen_module(service,tmp_path):
    qreq={'lines':['route test']};vreq={'lines':['route test']}
    qcommand=service._voice_command('qwen',qreq,tmp_path)
    vcommand=service._voice_command('vox',vreq,tmp_path)
    assert qcommand[0]==service.cfg.tts_python
    assert qcommand[2]=='workbench.tts_worker'
    assert vcommand[0]==service.cfg.voxcpm_python
    assert vcommand[2]=='workbench.voxcpm_worker'
    assert 'voxcpm_model' not in qreq
    assert vreq['voxcpm_model']==service.cfg.voxcpm_model


def test_selected_vox_missing_environment_never_falls_back_to_qwen(service):
    service.cfg.voxcpm_python=''
    with patch('workbench.service.run_owned') as runner:
        with pytest.raises(ServiceError,match='未安装'):
            service.preview_voice({'engine':'vox','voice':'natural_female','text':'preview test','speed':1})
        runner.assert_not_called()
    assert not service.queue.all()


@pytest.mark.parametrize('body',[{'engine':'vox','model':'outside'}, {'engine':'vox','python':'outside'}, {'engine':'vox','voice':[]}, {'engine':'qwen','speed':True}, {'engine':'qwen','text':[]}])
def test_preview_rejects_untrusted_fields_and_types(body):
    with pytest.raises(VoiceEngineError):
        validate_preview_payload(body)


def test_preview_and_hash_bound_audio_without_product_or_index(service):
    def synthetic_runner(command,**kwargs):
        folder=Path(command[-1]);path=folder/'voice_000.wav'
        with wave.open(str(path),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\x01\x00'*2400)
        receipt={'success':True,'engine':'qwen','voices':[{'index':0,'file':path.name,'samples':2400,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
        (folder/'voice-result.json').write_text(json.dumps(receipt),encoding='utf-8')
    with patch('workbench.service.run_owned',side_effect=synthetic_runner):
        result=service.preview_voice({'engine':'qwen','voice':'Serena','text':'explicit synthetic preview test','speed':1})
    audio,meta=service.preview_audio(result['id'],result['sha256'])
    assert audio.is_file() and meta['engine']=='qwen'
    assert not service.store.list_products() and not service.queue.all()
    audio.write_bytes(b'corrupted synthetic audio')
    with pytest.raises(ServiceError,match='变化'):
        service.preview_audio(result['id'],result['sha256'])


@pytest.mark.parametrize('identity',['../outside','x'*32,''])
def test_preview_path_identity_rejected(service,identity):
    with pytest.raises(ServiceError):
        service.preview_audio(identity,'0'*64)


def test_concurrent_preview_refuses_instead_of_queuing_and_releases_lock(service):
    def owned_preview(body):
        with pytest.raises(ServiceError,match='已有试听'):
            service.preview_voice(body)
        raise RuntimeError('explicit synthetic failure')
    with patch.object(service,'_preview_voice',side_effect=owned_preview):
        with pytest.raises(RuntimeError,match='synthetic'):
            service.preview_voice({})
    assert service._preview_generation_lock.acquire(blocking=False)
    service._preview_generation_lock.release()


def test_shutdown_cancel_is_propagated_to_owned_preview_process(service):
    def synthetic_cancel_runner(command,**kwargs):
        assert callable(kwargs['cancel']) and not kwargs['cancel']()
        service.stop_flag.set()
        assert kwargs['cancel']()
        raise RuntimeError('explicit synthetic shutdown')
    with patch('workbench.service.run_owned',side_effect=synthetic_cancel_runner):
        with pytest.raises(RuntimeError,match='shutdown'):
            service.preview_voice({'engine':'qwen','voice':'Serena','text':'synthetic cancellation test'})
    assert not list(service.preview_root.iterdir())
