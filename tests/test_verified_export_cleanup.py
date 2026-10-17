import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from workbench.cleanup import cleanup_job_dir
from workbench.media import sha256


def _caller_rig(tmp_path, monkeypatch, mode='silent', zero_inode=False):
    from workbench import service as service_mod
    from workbench.config import RuntimeConfig
    from workbench.queue import JobQueue
    from workbench.service import WorkbenchService
    from workbench.store import Store

    material = tmp_path / 'materials'; material.mkdir()
    output = tmp_path / 'output'; output.mkdir()
    cfg = RuntimeConfig(data_root=str(tmp_path / 'data'), nas_roots=[str(tmp_path)],
                        gpu_lock_path='', tts_python='', tts_model='', model_dir='')
    store = Store(tmp_path / 'records.json')
    queue = JobQueue(tmp_path / 'jobs.json')
    svc = WorkbenchService(cfg, store, queue, start=False)
    product = svc.create_product('P')
    svc.select_path(product['id'], 'source', str(material))
    svc.select_path(product['id'], 'output', str(output))
    rec = {'id': 'synthetic-index', 'product_id': product['id'], 'source': str(material), 'files': []}
    svc.store.put_index(product['id'], rec)
    p = svc.store.get_product(product['id'])
    snapshot = {'kind': 'render', 'source': p['source'], 'output': p['output'],
                'index_id': rec['id'], 'index_source': str(material), 'lines': ['x'],
                'settings': {'voice': False}, 'mapping': []}
    svc.queue.enqueue(product['id'], snapshot)
    job = svc.queue.next()
    monkeypatch.setattr(svc, '_indexer', lambda _: SimpleNamespace(is_obsolete=lambda _: False))
    monkeypatch.setattr(svc, '_snapshot_fresh', lambda _: True)

    def fake_render(x, config, d, cancel, progress):
        if mode == 'silent':
            final = d / 'final.mp4'
        else:
            attempt = d / 'final-codec' / 'codec-0000'; attempt.mkdir(parents=True)
            for name, raw in {'master.wav': b'wav', 'audio.m4a': b'audio',
                              'final-decoded.f32': b'decoded'}.items():
                (attempt / name).write_bytes(raw)
            final = attempt / 'final.mp4'
        final.write_bytes(b'synthetic final')
        report = {'ok': True, 'checks': {'video': True}, 'sha256': sha256(final),
                  'bytes': final.stat().st_size}
        return final, report

    def fake_export(src, root, report, title, authorization=None):
        dest = Path(root) / f'published-{mode}.mp4'
        dest.write_bytes(Path(src).read_bytes())
        return dest, {'sha256': sha256(dest), 'bytes': dest.stat().st_size}

    monkeypatch.setattr(service_mod, 'render_job', fake_render)
    monkeypatch.setattr(service_mod, 'export_numbered', fake_export)
    if zero_inode:
        original_tree = service_mod.iter_tree

        def zero_inode_tree(root):
            for path, st in original_tree(root):
                yield path, SimpleNamespace(st_mode=st.st_mode, st_dev=0, st_ino=0)

        monkeypatch.setattr(service_mod, 'iter_tree', zero_inode_tree)
    return svc, job, queue.root / job['id']


@pytest.mark.parametrize('mode,expected_files', [('silent', 1), ('codec', 4)])
def test_run_render_caller_removes_verified_silent_and_codec_media(tmp_path, monkeypatch, mode, expected_files):
    svc, job, jobdir = _caller_rig(tmp_path, monkeypatch, mode)
    svc._run_render(job, lambda: False)
    saved = svc.queue.get(job['id'])
    assert saved['status'] == 'done' and Path(saved['output']).exists()
    assert saved['cleanup']['files'] == expected_files
    assert not (jobdir / 'final.mp4').exists()
    if mode == 'codec':
        assert not list((jobdir / 'final-codec').rglob('master.wav'))
        assert not list((jobdir / 'final-codec').rglob('audio.m4a'))
        assert not list((jobdir / 'final-codec').rglob('final.mp4'))
        assert not list((jobdir / 'final-codec').rglob('final-decoded.f32'))


def test_run_render_cleanup_receipt_save_failure_keeps_actual_counts(tmp_path, monkeypatch):
    svc, job, jobdir = _caller_rig(tmp_path, monkeypatch)
    original = svc.queue._save
    injected = []

    def save(candidate=None, before_replace=None):
        if candidate and candidate[job['id']].get('cleanup', {}).get('files', 0) > 0 and not injected:
            injected.append(True)
            raise OSError('synthetic cleanup receipt save failure')
        return original(candidate, before_replace)

    monkeypatch.setattr(svc.queue, '_save', save)
    svc._run_render(job, lambda: False)
    saved = svc.queue.get(job['id'])
    assert injected and saved['status'] == 'done'
    assert saved['cleanup']['files'] == 1
    assert saved['cleanup']['bytes'] == len(b'synthetic final')
    assert not (jobdir / 'final.mp4').exists()


def test_run_render_zero_inode_direntry_model_refuses_hardlink_alias(tmp_path, monkeypatch):
    svc, job, jobdir = _caller_rig(tmp_path, monkeypatch, zero_inode=True)
    original = svc._cleanup_verified_export

    def alias_before_cleanup(jid):
        out = Path(svc.queue.get(jid)['output'])
        out.unlink()
        out.hardlink_to(jobdir / 'final.mp4')
        return original(jid)

    monkeypatch.setattr(svc, '_cleanup_verified_export', alias_before_cleanup)
    svc._run_render(job, lambda: False)
    saved = svc.queue.get(job['id'])
    assert saved['status'] == 'done' and saved['cleanup']['files'] == 0
    assert (jobdir / 'final.mp4').exists() and Path(saved['output']).exists()


def test_cleanup_verified_export_removes_only_known_final_media(tmp_path):
    (tmp_path / 'final.mp4').write_bytes(b'silent')
    codec = tmp_path / 'final-codec' / 'codec-0000'
    codec.mkdir(parents=True)
    for name, raw in {
        'master.wav': b'wav',
        'audio.m4a': b'audio',
        'final.mp4': b'codec',
        'final-decoded.f32': b'decoded',
        'report.json': b'keep report',
    }.items():
        (codec / name).write_bytes(raw)
    (tmp_path / 'cover.jpg').write_bytes(b'cover')
    (tmp_path / 'voice_000.wav').write_bytes(b'voice')

    legacy = cleanup_job_dir(tmp_path, 'font.ttf')
    assert (tmp_path / 'final.mp4').exists()
    assert (codec / 'master.wav').exists()
    assert legacy['files'] == 0

    verified = cleanup_job_dir(tmp_path, 'font.ttf', verified_export=True)
    assert verified['files'] == 5
    assert not (tmp_path / 'final.mp4').exists()
    for name in ('master.wav', 'audio.m4a', 'final.mp4', 'final-decoded.f32'):
        assert not (codec / name).exists()
    assert (codec / 'report.json').exists()
    assert (tmp_path / 'cover.jpg').exists()
    assert (tmp_path / 'voice_000.wav').exists()


def test_cleanup_verified_export_does_not_remove_unknown_codec_files(tmp_path):
    codec = tmp_path / 'final-codec' / 'codec-0064'
    codec.mkdir(parents=True)
    (codec / 'mystery.bin').write_bytes(b'keep')
    (codec / 'final-decoded.f32.tmp').write_bytes(b'decoded')
    out = cleanup_job_dir(tmp_path, 'font.ttf', verified_export=True)
    assert out['files'] == 1
    assert (codec / 'mystery.bin').exists()
    assert not (codec / 'final-decoded.f32.tmp').exists()


def test_service_cleanup_requires_persisted_done_row_and_exact_output(tmp_path, monkeypatch):
    from workbench.config import RuntimeConfig
    from workbench.queue import JobQueue
    from workbench.service import WorkbenchService
    from workbench.store import Store

    cfg = RuntimeConfig(data_root=str(tmp_path / 'data'), nas_roots=[str(tmp_path)])
    store = Store(tmp_path / 'records.json')
    product = store.create_product('P')
    queue = JobQueue(tmp_path / 'jobs.json')
    svc = WorkbenchService(cfg, store, queue, start=False)
    job = queue.enqueue(product['id'], {'kind': 'render', 'lines': ['x']})
    jobdir = queue.root / job['id']
    final = jobdir / 'final.mp4'
    final.write_bytes(b'local')
    exported = tmp_path / 'exports.mp4'
    exported.write_bytes(b'exported')
    artifact = {'sha256': sha256(exported), 'bytes': exported.stat().st_size}
    queue.update(job['id'], status='done', stage='done', progress=100,
                 output=str(exported), artifact=artifact,
                 condition=lambda _: True)
    persisted = json.loads(queue.path.read_text(encoding='utf8'))[job['id']]
    assert persisted['status'] == 'done'
    # The service's authoritative cleanup hook is exercised through the same
    # persisted row boundary used after _run_render.
    result = svc._cleanup_verified_export(job['id'])
    assert result['files'] == 1
    assert not final.exists()

    # A same-size but corrupted export is never authorization to delete.
    final.write_bytes(b'local-again')
    exported.write_bytes(b'changed!')
    skipped = svc._cleanup_verified_export(job['id'])
    assert skipped['files'] == 0
    assert final.exists()

    # An authoritative disk readback failure is a hard stop for deletion.
    exported.write_bytes(b'exported')
    original_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, **kwargs: (_ for _ in ()).throw(OSError('readback unavailable'))
                        if self == queue.path else original_read(self, **kwargs))
    skipped = svc._cleanup_verified_export(job['id'])
    assert skipped['files'] == 0 and final.exists()


def test_service_cleanup_protects_in_tree_and_hardlink_outputs(tmp_path):
    from workbench.config import RuntimeConfig
    from workbench.queue import JobQueue
    from workbench.service import WorkbenchService
    from workbench.store import Store

    cfg = RuntimeConfig(data_root=str(tmp_path / 'data'), nas_roots=[str(tmp_path)])
    store = Store(tmp_path / 'records.json')
    product = store.create_product('P')
    queue = JobQueue(tmp_path / 'jobs.json')
    svc = WorkbenchService(cfg, store, queue, start=False)
    job = queue.enqueue(product['id'], {'kind': 'render', 'lines': ['x']})
    jobdir = queue.root / job['id']
    local = jobdir / 'final.mp4'
    local.write_bytes(b'published bytes')
    exported = tmp_path / 'published.mp4'
    exported.hardlink_to(local)
    artifact = {'sha256': sha256(exported), 'bytes': exported.stat().st_size}
    queue.update(job['id'], status='done', stage='done', progress=100,
                 output=str(exported), artifact=artifact, condition=lambda _: True)
    skipped = svc._cleanup_verified_export(job['id'])
    assert skipped['files'] == 0 and local.exists() and exported.exists()
