"""Optional installer helper tests; no real models, network or CUDA involved."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

SPEC = importlib.util.spec_from_file_location('vox_setup', Path(__file__).resolve().parents[1]/'deploy'/'vox_setup.py')
assert SPEC is not None and SPEC.loader is not None
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def synthetic_model(tmp_path):
    cfg = b'{"architecture":"voxcpm2"}'
    payload = b'explicit synthetic test model bytes, not weights'
    d = tmp_path/'model';d.mkdir()
    blobs = {'config.json':cfg,'model.safetensors':payload}
    for name,raw in blobs.items():
        (d/name).write_bytes(raw)
    spec = {'repo_id':'openbmb/VoxCPM2','revision':'a'*40,
            'files_sha256':{k:hashlib.sha256(v).hexdigest() for k,v in blobs.items()},
            'files_bytes':{k:len(v) for k,v in blobs.items()}}
    return d,spec


def test_complete_synthetic_model_manifest_accepts(tmp_path):
    d,spec = synthetic_model(tmp_path)
    result = setup.verify_model(d,spec)
    assert result['verified_files']==2
    assert result['revision']==spec['revision']


@pytest.mark.parametrize('kind',['missing','length','bytes'])
def test_model_corruption_is_rejected(tmp_path,kind):
    d,spec = synthetic_model(tmp_path);p=d/'model.safetensors'
    if kind=='missing':
        p.unlink()  # This test's own synthetic fixture.
    elif kind=='length':
        p.write_bytes(b'x')
    else:
        p.write_bytes(b'!'*spec['files_bytes']['model.safetensors'])
    with pytest.raises(ValueError):
        setup.verify_model(d,spec)


@pytest.mark.parametrize('rel',['../escape','/absolute','C:/absolute','sub\\escape',''])
def test_bad_manifest_paths_are_rejected(tmp_path,rel):
    with pytest.raises(ValueError):
        setup.model_file(tmp_path,rel)


def test_symlink_model_file_is_rejected(tmp_path):
    d,spec=synthetic_model(tmp_path)
    target=tmp_path/'outside';target.write_bytes(b'x')
    (d/'linked').symlink_to(target)
    with pytest.raises(ValueError):
        setup.model_file(d,'linked')


def test_atomic_json_readback(tmp_path):
    p=tmp_path/'config.json'
    value={'engine':'voxcpm','label':'自然分享女声'}
    setup.atomic_json(p,value)
    assert json.loads(p.read_text(encoding='utf-8'))==value
    assert not list(tmp_path.glob('*.tmp'))


def test_public_optional_manifest_is_complete_and_pinned():
    root=Path(__file__).resolve().parents[1]
    m=setup.read_manifest(root)
    assert m['sdk']['version']=='2.0.3'
    assert m['torch_version']=='2.8.0+cu128'
    assert m['torchaudio_version']=='2.8.0+cu128'
    assert set(m['model']['files_sha256'])==set(m['model']['files_bytes'])
    assert m['model']['revision']=='32279effe8c19989596f05d353d1447f51d9e915'
    assert all(len(x)==64 for x in m['model']['files_sha256'].values())


def test_config_update_preserves_original_settings_and_key(tmp_path):
    root=tmp_path/'project';(root/'config').mkdir(parents=True)
    cfg={'host':'127.0.0.1','port':8765,'nas_roots':['./materials'],
         'tts_model':'./original-qwen','tts_python':'./original-qwen-python',
         'ai_api_key':'synthetic-fixture-value','ai_base_url':'https://api.deepseek.com',
         'ai_model':'deepseek-chat','data_root':'./data','output_root':'./output'}
    path=root/'config'/'workbench.config.json'
    path.write_bytes(b'\xef\xbb\xbf'+json.dumps(cfg).encode())
    result=setup.commit_config(root,root/'models'/'vox',root/'vox-python',root/'data'/'voice-cache')
    actual=json.loads(path.read_text(encoding='utf-8-sig'))
    assert result['qwen_configuration_preserved']
    assert all(actual[k]==v for k,v in cfg.items())
    assert actual['voxcpm_model']==str(root/'models'/'vox')


def test_config_update_rejects_linked_config(tmp_path):
    root=tmp_path/'project';(root/'config').mkdir(parents=True)
    external=tmp_path/'outside.json';external.write_text('{}')
    (root/'config'/'workbench.config.json').symlink_to(external)
    with pytest.raises(ValueError):
        setup.commit_config(root,root/'models'/'vox',root/'vox-python',root/'cache')
    assert external.read_text()=='{}'
