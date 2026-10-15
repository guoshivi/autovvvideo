"""Numbered export contracts; bytes/render/index fixtures are explicitly synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import errno
import json
import os
import threading

import pytest

from workbench import renderer, service
from workbench.config import RuntimeConfig
from workbench.fs_authority import AuthorityError, capture_lease, identity_chain
from workbench.media import sha256
from workbench.queue import JobQueue, CommitError
from workbench.store import Store

NOW = datetime(2026, 10, 15, 23, 59)


@pytest.fixture
def files(tmp_path):
    src = tmp_path / '最终.mp4'
    src.write_bytes(b'EXPLICIT-SYNTHETIC-MP4' * 100)
    root = tmp_path / '输出'
    root.mkdir()
    report = {'ok': True, 'checks': {'video': True}, 'sha256': sha256(src), 'bytes': src.stat().st_size}
    return src, root, report


def export(files, title='金门一条根', **kwargs):
    src, root, report = files
    return renderer.export_numbered(src, root, report, title, now=kwargs.pop('now', NOW), **kwargs)


def leaves(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())


def test_sequential_only_video_and_internal_artifacts_untouched(files):
    src, root, report = files
    cover = src.with_name('cover.jpg')
    cover.write_bytes(b'synthetic cover')
    report['cover'] = {'sha256': sha256(cover), 'bytes': cover.stat().st_size}
    internal = src.with_name('report.json')
    internal.write_text(json.dumps(report), encoding='utf8')
    before = {p: p.read_bytes() for p in (src, cover, internal)}
    old = root / 'legacy-job'
    old.mkdir()
    (old / 'old.mp4').write_bytes(b'old video')
    (old / 'report.json').write_bytes(b'old report')
    for n in (1, 2):
        dest, got = export(files)
        assert dest == root / '金门一条根' / '20261015' / f'金门一条根 {n}.mp4'
        assert dest.read_bytes() == src.read_bytes()
        assert got == {'sha256': report['sha256'], 'bytes': report['bytes']}
    assert leaves(root) == sorted(['legacy-job/old.mp4', 'legacy-job/report.json',
                                  '金门一条根/20261015/金门一条根 1.mp4', '金门一条根/20261015/金门一条根 2.mp4'])
    assert (old / 'old.mp4').read_bytes() == b'old video'
    assert (old / 'report.json').read_bytes() == b'old report'
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_gaps_case_insensitive_and_escaped_title(files):
    _, root, _ = files
    title = 'X[商品]'
    d = root / title / '20261015'
    d.mkdir(parents=True)
    for name in (f'{title} 1.mp4', 'x[商品] 3.MP4', f'{title} nope.mp4', 'X商 90.mp4'):
        (d / name).write_bytes(b'old')
    dest, _ = export(files, title)
    assert dest.name == f'{title} 4.mp4'
    assert (d / 'x[商品] 3.MP4').read_bytes() == b'old'


def test_date_changes_start_new_sequence(files):
    a, _ = export(files, 'X')
    b, _ = export(files, 'X', now=datetime(2026, 10, 16))
    assert a.parent.name == '20261015' and b.parent.name == '20261016'
    assert a.name == b.name == 'X 1.mp4'


def test_collision_retries_without_overwriting(files, monkeypatch):
    _, root, _ = files
    d = root / 'X' / '20261015'
    d.mkdir(parents=True)
    (d / 'X 1.mp4').write_bytes(b'old')
    monkeypatch.setattr(renderer, '_next_number', lambda *_: 1)
    dest, _ = export(files, 'X')
    assert dest.name == 'X 2.mp4'
    assert (d / 'X 1.mp4').read_bytes() == b'old'
    assert not list(d.glob('*.staging'))


def test_eight_concurrent_publishers_all_start_at_one(files, monkeypatch):
    src, root, report = files
    barrier = threading.Barrier(8)
    def same_number(*_):
        barrier.wait(timeout=20)
        return 1
    monkeypatch.setattr(renderer, '_next_number', same_number)
    def run(i):
        unique = src.with_name(f'source-{i}.mp4')
        unique.write_bytes(bytes([i]) * 500)
        r = dict(report, sha256=sha256(unique), bytes=unique.stat().st_size)
        dest, _ = export((unique, root, r), '并发商品')
        return dest, unique.read_bytes()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(8)))
    assert {p.name for p, _ in results} == {f'并发商品 {n}.mp4' for n in range(1, 9)}
    assert all(p.read_bytes() == raw for p, raw in results)
    assert len(leaves(root)) == 8 and not list(root.rglob('*.staging'))


@pytest.mark.parametrize('raw,expected', [
    ('  金/门\\一:条*根?"<>|\x00\x01\x1f\x7f\x85\r\n\t. ', '金门一条根'),
    ('CON', '_CON'), ('prn.txt', '_prn.txt'), ('LPT9', '_LPT9'), ('COM¹.txt', '_COM¹.txt'),
    ('CON .txt', '_CON .txt'), ('..\\../', '视频'), ('  ', '视频'),
    ('金' * 90, '金' * 80), ('A' * 78 + '. 后缀', 'A' * 78),
])
def test_safe_segment(raw, expected):
    assert renderer._safe_segment(raw) == expected
    assert len(renderer._safe_segment(raw)) <= 80


def test_long_product_uses_one_shared_eighty_character_limit(files):
    title = '金门一条根' * 18
    short = service._short_title({'name': title}, {'lines': ['不该用的文案']})
    assert short == title[:80]
    dest, _ = export(files, short)
    assert dest.parent.parent.name == title[:80]
    assert dest.name == title[:80] + ' 1.mp4'


@pytest.mark.parametrize('name,lines,expected', [
    ('金门一条根', ['文案不同也不换目录'], '金门一条根'),
    ('金/门:一条根', ['文案'], '金门一条根'),
    ('', ['开头文案，后面不用'], '开头文案'),
    (None, [], '视频'), ('   ', ['回退。文案'], '回退'),
    ('///', ['商品名不空不能偷换成文案'], '视频'),
    ('', ['很长的文案' * 20 + '，后文'], ('很长的文案' * 20)[:80]),
])
def test_short_title_product_first(name, lines, expected):
    assert service._short_title({'name': name}, {'lines': lines}) == expected


@pytest.mark.parametrize('bad', [{'ok': False}, {'checks': {}}, {'checks': {'video': False}},
                                 {'sha256': 'bad'}, {'bytes': 1}])
def test_bad_report_creates_nothing(files, bad):
    src, root, report = files
    with pytest.raises(ValueError):
        export((src, root, dict(report, **bad)))
    assert list(root.iterdir()) == []


def test_wrong_lease_and_selection_path_create_nothing(files, tmp_path):
    _, root, _ = files
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(AuthorityError):
        export(files, authorization={'path': str(root), 'chain': identity_chain(other)})
    with pytest.raises(ValueError, match='selection path'):
        export(files, authorization=capture_lease(other))
    assert list(root.iterdir()) == []


def test_missing_root_and_source_overlap(files, tmp_path):
    src, root, report = files
    missing = tmp_path / 'missing'
    with pytest.raises(ValueError, match='root missing'):
        export((src, missing, report))
    assert not missing.exists()
    inside = root / src.name
    inside.write_bytes(src.read_bytes())
    with pytest.raises(ValueError, match='overlap'):
        export((inside, root, report))
    assert leaves(root) == [src.name]


@pytest.mark.parametrize('level', ['product', 'date'])
def test_preexisting_symlink_does_not_write_outside(files, tmp_path, level):
    _, root, _ = files
    outside = tmp_path / 'outside'
    outside.mkdir()
    if level == 'product':
        (root / 'X').symlink_to(outside, target_is_directory=True)
    else:
        (root / 'X').mkdir()
        (root / 'X' / '20261015').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        export(files, 'X')
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('level', ['product', 'date'])
def test_copy_ancestor_swap_rejected(files, tmp_path, monkeypatch, level):
    _, root, _ = files
    outside = tmp_path / 'outside'
    outside.mkdir()
    copy = renderer.shutil.copyfileobj
    def swap(f, g, *args):
        copy(f, g, *args)
        ancestor = root / 'X' if level == 'product' else root / 'X' / '20261015'
        ancestor.rename(tmp_path / 'moved')
        ancestor.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(renderer.shutil, 'copyfileobj', swap)
    with pytest.raises(AuthorityError) as exc:
        export(files, 'X')
    assert not getattr(exc.value, 'after_replace', False)
    assert list(outside.iterdir()) == []
    assert not list((tmp_path / 'moved').rglob('*.mp4'))
    if os.name != 'nt':
        assert not list((tmp_path / 'moved').rglob('*.staging'))


def test_copy_plain_directory_swap_rejected(files, tmp_path, monkeypatch):
    _, root, _ = files
    copy = renderer.shutil.copyfileobj
    def swap(f, g, *args):
        copy(f, g, *args)
        d = root / 'X' / '20261015'
        d.rename(tmp_path / 'moved')
        d.mkdir()
    monkeypatch.setattr(renderer.shutil, 'copyfileobj', swap)
    with pytest.raises(AuthorityError):
        export(files, 'X')
    assert not list(root.rglob('*.mp4'))
    assert not list((tmp_path / 'moved').rglob('*.mp4'))


@pytest.mark.parametrize('failure', ['copy', 'rename', 'staging_hash', 'staging_read'])
def test_prepublication_failures_clean_owned_stage(files, monkeypatch, failure):
    _, root, _ = files
    def fail(*args, **kwargs):
        raise PermissionError('injected failure')
    if failure == 'copy':
        def partial_copy(f, g, *args):
            g.write(b'partial synthetic copy')
            fail()
        monkeypatch.setattr(renderer.shutil, 'copyfileobj', partial_copy)
    elif failure == 'rename':
        monkeypatch.setattr(renderer, '_nv_rename_file', fail)
    else:
        original = renderer.sha256
        def hash_stage(p):
            if Path(p).suffix == '.staging':
                return 'wrong' if failure == 'staging_hash' else fail()
            return original(p)
        monkeypatch.setattr(renderer, 'sha256', hash_stage)
    with pytest.raises((OSError, ValueError)) as exc:
        export(files, 'X')
    assert exc.value.after_replace is False
    assert exc.value.receipt['stage'].endswith('.staging')
    assert leaves(root) == []


def test_failed_exclusive_open_never_deletes_foreign_stage(files, monkeypatch):
    _, root, _ = files
    original = Path.open
    seen = []
    def collision(p, mode='r', *args, **kwargs):
        if mode == 'xb' and p.suffix == '.staging':
            with original(p, 'wb') as f:
                f.write(b'foreign staging')
            seen.append(p)
        return original(p, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', collision)
    with pytest.raises(FileExistsError):
        export(files, 'X')
    assert len(seen) == 1 and seen[0].read_bytes() == b'foreign staging'
    assert leaves(root) == [seen[0].relative_to(root).as_posix()]


def test_cleanup_never_deletes_replacement_stage(files, tmp_path, monkeypatch):
    _, root, _ = files
    def replace_stage(stage, dest, *args):
        stage.rename(tmp_path / 'owned-stage-moved')
        stage.write_bytes(b'foreign staging')
        raise PermissionError('injected rename failure')
    monkeypatch.setattr(renderer, '_nv_rename_file', replace_stage)
    with pytest.raises(PermissionError):
        export(files, 'X')
    stages = list(root.rglob('*.staging'))
    assert len(stages) == 1 and stages[0].read_bytes() == b'foreign staging'


@pytest.mark.parametrize('failure', ['corrupt', 'read', 'authority', 'raise_after_rename'])
def test_postpublication_failures_keep_video_and_receipt(files, tmp_path, monkeypatch, failure):
    src, root, report = files
    native = renderer._nv_rename_file
    observed = []
    def publish(stage, dest, *args):
        native(stage, dest, *args)
        observed.append(dest)
        if failure == 'corrupt':
            with dest.open('ab') as f:
                f.write(b'!')
        elif failure == 'authority':
            dest.parent.rename(tmp_path / 'moved')
            dest.parent.mkdir()
        elif failure == 'raise_after_rename':
            raise PermissionError('injected error after syscall')
    monkeypatch.setattr(renderer, '_nv_rename_file', publish)
    if failure == 'read':
        original = renderer.sha256
        def fail_dest(p):
            if observed and Path(p) == observed[0]:
                raise PermissionError('injected final read failure')
            return original(p)
        monkeypatch.setattr(renderer, 'sha256', fail_dest)
    with pytest.raises(OSError) as exc:
        export(files, 'X')
    assert exc.value.after_replace is True
    assert exc.value.receipt['destination'] == str(observed[0])
    assert exc.value.receipt['sha256'] == report['sha256']
    actual = tmp_path / 'moved' / observed[0].name if failure == 'authority' else observed[0]
    assert actual.is_file()
    assert actual.read_bytes().startswith(src.read_bytes())
    assert not list(root.rglob('*.staging'))


def test_collision_rechecks_full_chain_before_retry(files, tmp_path, monkeypatch):
    _, root, _ = files
    calls = []
    def collide(stage, dest, *args):
        calls.append(dest)
        dest.parent.rename(tmp_path / 'moved')
        dest.parent.mkdir()
        raise FileExistsError(errno.EEXIST, 'injected collision')
    monkeypatch.setattr(renderer, '_nv_rename_file', collide)
    with pytest.raises(AuthorityError):
        export(files, 'X')
    assert len(calls) == 1 and not list(root.rglob('*.mp4'))


def test_native_file_no_replace_preserves_both_inputs(tmp_path):
    stage = tmp_path / 'stage'
    dest = tmp_path / 'dest.mp4'
    stage.write_bytes(b'new')
    dest.write_bytes(b'old')
    with pytest.raises(OSError) as exc:
        renderer._nv_rename_file(stage, dest, tmp_path, identity_chain(tmp_path))
    assert exc.value.errno in (17, 80, 183) or getattr(exc.value, 'winerror', None) in (80, 183)
    assert stage.read_bytes() == b'new' and dest.read_bytes() == b'old'


def test_mkdir_rechecks_parent_before_creating_next_level(files, tmp_path, monkeypatch):
    _, root, _ = files
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = Path.mkdir
    def swapped_mkdir(p, *args, **kwargs):
        result = original(p, *args, **kwargs)
        if p == root / 'X':
            p.rename(tmp_path / 'moved')
            p.symlink_to(outside, target_is_directory=True)
        return result
    monkeypatch.setattr(Path, 'mkdir', swapped_mkdir)
    with pytest.raises(AuthorityError):
        export(files, 'X')
    assert list(outside.iterdir()) == []


def test_final_hash_rechecks_full_chain_and_keeps_published_video(files, tmp_path, monkeypatch):
    _, root, _ = files
    original = renderer.sha256
    def swap_during_hash(p):
        digest = original(p)
        if Path(p).name == 'X 1.mp4':
            Path(p).parent.rename(tmp_path / 'moved')
            Path(p).parent.mkdir()
            (Path(p).parent / Path(p).name).write_bytes(files[0].read_bytes())
        return digest
    monkeypatch.setattr(renderer, 'sha256', swap_during_hash)
    with pytest.raises(OSError) as exc:
        export(files, 'X')
    assert exc.value.after_replace is True
    assert (tmp_path / 'moved' / 'X 1.mp4').read_bytes() == files[0].read_bytes()
    assert (root / 'X' / '20261015' / 'X 1.mp4').read_bytes() == files[0].read_bytes()
    assert isinstance(exc.value.__cause__, AuthorityError)


def test_collision_retry_limit_cleans_only_own_stage(files, monkeypatch):
    _, root, _ = files
    attempts = []
    def collision(stage, dest, leased_dir, chain):
        assert leased_dir == dest.parent
        assert chain == identity_chain(dest.parent)
        attempts.append(dest.name)
        raise FileExistsError(errno.EEXIST, 'injected repeated collisions')
    monkeypatch.setattr(renderer, '_nv_rename_file', collision)
    with pytest.raises(FileExistsError) as exc:
        export(files, 'X')
    assert len(attempts) == 200 and exc.value.after_replace is False
    assert leaves(root) == []


@pytest.fixture
def wired_service(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    material = tmp_path / 'materials'
    material.mkdir()
    output = tmp_path / 'output'
    output.mkdir()
    cfg = RuntimeConfig(data_root=str(data), nas_roots=[str(tmp_path)], gpu_lock_path='',
                        tts_python='', tts_model='', model_dir='')
    svc = service.WorkbenchService(cfg, Store(data / 'records.json'), JobQueue(data / 'queue.json'), start=False)
    p = svc.create_product('商品名超过二十四个字也应该完整保留而不是改成文案第一句')
    pid = p['id']
    svc.select_path(pid, 'source', str(material))
    svc.select_path(pid, 'output', str(output))
    rec = {'id': 'synthetic-index', 'product_id': pid, 'source': str(material), 'files': []}
    svc.store.put_index(pid, rec)
    p = svc.store.get_product(pid)
    snapshot = {'kind': 'render', 'source': p['source'], 'output': p['output'],
                'index_id': rec['id'], 'index_source': str(material), 'lines': ['不是商品名的文案'],
                'settings': {'voice': False}, 'mapping': []}
    svc.queue.enqueue(pid, snapshot)
    j = svc.queue.next()
    monkeypatch.setattr(svc, '_indexer', lambda _: SimpleNamespace(is_obsolete=lambda _: False))
    freshness = []
    def fresh(x):
        freshness.append(x)
        return True
    monkeypatch.setattr(svc, '_snapshot_fresh', fresh)
    def synthetic_render(x, cfg, d, cancel, progress):
        assert x['settings']['voice'] is False
        f = d / 'final.mp4'
        f.write_bytes(b'explicit synthetic renderer fixture')
        cover = d / 'cover.jpg'
        cover.write_bytes(b'synthetic internal cover')
        r = {'ok': True, 'checks': {'video': True}, 'sha256': sha256(f), 'bytes': f.stat().st_size,
             'cover': {'sha256': sha256(cover), 'bytes': cover.stat().st_size}}
        (d / 'report.json').write_text(json.dumps(r), encoding='utf8')
        return f, r
    monkeypatch.setattr(service, 'render_job', synthetic_render)
    return svc, j, output, freshness


def test_real_service_wiring_persists_numbered_output_and_internal_report(wired_service):
    svc, j, output, freshness = wired_service
    svc._run_render(j, lambda: False)
    saved = svc.queue.get(j['id'])
    disk = json.loads(svc.queue.path.read_text(encoding='utf8'))[j['id']]
    assert saved == disk and saved['status'] == 'done'
    p = svc.store.get_product(j['product_id'])
    dest = Path(saved['output'])
    assert dest.parent.parent == output / p['name']
    assert len(dest.parent.name) == 8 and dest.parent.name.isdigit()
    assert dest.name == p['name'] + ' 1.mp4'
    assert saved['artifact']['sha256'] == sha256(dest)
    assert saved['output_folder'] == str(output)
    assert saved['report']['cover']
    assert (svc.queue.root / j['id'] / 'report.json').is_file()
    assert (svc.queue.root / j['id'] / 'cover.jpg').is_file()
    assert leaves(output) == [dest.relative_to(output).as_posix()]
    assert len(freshness) >= 3  # initial/final generation and actual queue condition


@pytest.mark.parametrize('role', ['source', 'output'])
def test_service_initial_generation_guard_still_blocks(wired_service, tmp_path, role):
    svc, j, output, _ = wired_service
    changed = tmp_path / 'changed'
    changed.mkdir()
    svc.store.select(j['product_id'], role, {'path': str(changed), '_lease': capture_lease(changed)})
    with pytest.raises(service.ServiceError):
        svc._run_render(j, lambda: False)
    assert leaves(output) == [] and svc.queue.get(j['id'])['status'] != 'done'


def test_service_postpublication_generation_guard_keeps_video(wired_service, tmp_path, monkeypatch):
    svc, j, output, _ = wired_service
    original = service.export_numbered
    changed = tmp_path / 'changed'
    changed.mkdir()
    def change_after(*args, **kwargs):
        result = original(*args, **kwargs)
        svc.store.select(j['product_id'], 'output', {'path': str(changed), '_lease': capture_lease(changed)})
        return result
    monkeypatch.setattr(service, 'export_numbered', change_after)
    with pytest.raises(service.ServiceError, match='final generation'):
        svc._run_render(j, lambda: False)
    assert len(list(output.rglob('*.mp4'))) == 1
    assert svc.queue.get(j['id'])['status'] != 'done'


def test_service_real_queue_commit_rejects_changed_generation(wired_service, monkeypatch):
    svc, j, output, _ = wired_service
    original = svc.queue.update
    def change_before_done(i, **kw):
        if kw.get('status') == 'done':
            p = svc.store.get_product(j['product_id'])
            svc.store.update_product(j['product_id'], output=dict(p['output'], path=str(output / 'changed')))
        return original(i, **kw)
    monkeypatch.setattr(svc.queue, 'update', change_before_done)
    with pytest.raises(CommitError) as exc:
        svc._run_render(j, lambda: False)
    assert exc.value.after_replace is True
    assert len(list(output.rglob('*.mp4'))) == 1
    assert svc.queue.get(j['id'])['status'] != 'done'
    assert json.loads(svc.queue.path.read_text(encoding='utf8'))[j['id']]['status'] != 'done'
