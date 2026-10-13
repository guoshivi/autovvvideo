"""Fix2 CPU regressions: real handlers and native-branch ownership doubles.

No server, model, network or Windows kernel is started by these tests.
"""
import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types
from email.message import Message
from unittest.mock import patch

import pytest
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.server import WorkbenchHandler
from workbench.service import WorkbenchService
from workbench.store import Store


SPEC = importlib.util.spec_from_file_location(
    'vox_fix2_setup', Path(__file__).resolve().parents[1] / 'deploy/vox_setup.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
SAFE_FAILURE = '试听生成失败，请稍后重试'
BUSY = '已有试听正在生成，请等它完成后再试'
ENGINE_ERROR = '配音引擎只能选择 Qwen（原配音）或 Vox（新配音·试用）'
SECRET = 'TEST_ONLY credential sk_FAKE internal host 192.0.2.10'


@pytest.fixture
def svc(tmp_path):
    material = tmp_path / 'materials'
    material.mkdir()
    py = tmp_path / 'synthetic-python'
    py.write_text('synthetic executable, never invoked')
    model = tmp_path / 'synthetic-model'
    model.mkdir()
    cfg = RuntimeConfig(data_root=str(tmp_path / 'data'), nas_roots=[str(material)],
                        tts_python=str(py), tts_model=str(model),
                        voxcpm_python=str(py), voxcpm_model=str(model),
                        voxcpm_cache=str(tmp_path / 'cache'),
                        model_dir='', gpu_lock_path='')
    return WorkbenchService(cfg, Store(tmp_path / 'data/records.json'),
                            JobQueue(tmp_path / 'data/jobs.json'), start=False)


def invoke(svc, method, path, body=None, headers=None):
    h = object.__new__(WorkbenchHandler)
    h.path = path
    h.server = types.SimpleNamespace(service=svc, server_port=svc.cfg.port)
    h.headers = Message()
    h.headers['Host'] = f'{svc.cfg.host}:{svc.cfg.port}'
    raw = b'' if body is None else json.dumps(body).encode()
    if method == 'POST':
        h.headers['Origin'] = f'http://{svc.cfg.host}:{svc.cfg.port}'
        h.headers['Content-Type'] = 'application/json'
        h.headers['Content-Length'] = str(len(raw))
    for k, v in (headers or {}).items():
        h.headers[k] = v
    h.rfile = io.BytesIO(raw)
    h.wfile = io.BytesIO()
    h.request_version = 'HTTP/1.1'
    h.requestline = f'{method} {path} HTTP/1.1'
    h.command = method
    h.client_address = ('127.0.0.1', 1)
    h.close_connection = False
    getattr(h, 'do_' + method)()
    head, content = h.wfile.getvalue().split(b'\r\n\r\n', 1)
    return int(head.split(b' ', 2)[1]), head.decode() + '\r\n', content


@pytest.mark.parametrize('kind', ['runtime', 'receipt'])
@pytest.mark.parametrize('detail', [SECRET, BUSY, ENGINE_ERROR])
def test_preview_untrusted_errors_are_fixed_even_for_allowlisted_text(svc, kind, detail):
    entered = []

    def fail(command, **kwargs):
        entered.append(command)
        folder = Path(command[-1])
        (folder / 'stage-test.stderr.log').write_text(detail)
        if kind == 'runtime':
            raise RuntimeError(detail)
        (folder / 'voice-result.json').write_text(json.dumps({'success': False, 'error': detail}))

    with patch('workbench.service.run_owned', side_effect=fail):
        status, _, raw = invoke(svc, 'POST', '/api/voice-preview',
                                {'engine': 'vox', 'voice': 'calm_male', 'text': '合成错误测试'})
    assert entered and status == 400
    assert json.loads(raw) == {'error': SAFE_FAILURE, 'code': 'PREVIEW_FAILED'}
    assert not svc._preview_generation_lock.locked()
    assert not list(svc.preview_root.iterdir())
    private_logs = list((Path(svc.cfg.data_root) / 'voice-preview-logs').rglob('*.log'))
    assert len(private_logs) == 1 and private_logs[0].read_text() == detail
    assert str(private_logs[0]) not in raw.decode()


@pytest.mark.parametrize('body,expected', [
    ({'engine': 'other'}, ENGINE_ERROR),
    ({'engine': 'vox', 'voice': 'other'}, 'Vox 声音预设不可用，请重新选择'),
    ({'engine': 'qwen', 'voice': 'other'}, 'Qwen 说话人无效'),
    ({'speed': True}, '语速需在 0.85–1.5 之间'),
    ({'text': []}, '预览文字无效'),
    ({'python': 'untrusted'}, '预览请求只支持引擎、声音、文字和语速'),
])
def test_preview_controlled_validation_is_useful(svc, body, expected):
    with patch('workbench.service.run_owned') as run:
        status, _, raw = invoke(svc, 'POST', '/api/voice-preview', body)
    run.assert_not_called()
    assert status == 400 and json.loads(raw)['error'] == expected


def test_preview_missing_install_and_busy_are_useful(svc):
    svc.cfg.voxcpm_python = ''
    status, _, raw = invoke(svc, 'POST', '/api/voice-preview', {'engine': 'vox'})
    assert status == 400
    assert json.loads(raw)['error'] == 'Vox 试用环境未安装，请先运行可选的 install-vox.ps1'
    svc._preview_generation_lock.acquire()
    try:
        status, _, raw = invoke(svc, 'POST', '/api/voice-preview', {})
        assert status == 400 and json.loads(raw)['error'] == BUSY
    finally:
        svc._preview_generation_lock.release()


def test_nonpreview_error_contract_stays_unchanged(svc):
    h = object.__new__(WorkbenchHandler)
    h.path = '/api/products'
    responses = []
    h._json = lambda obj, status: responses.append((obj, status))
    h._error(RuntimeError(SECRET))
    assert responses == [({'error': SECRET, 'type': 'RuntimeError'}, 400)]


@pytest.mark.parametrize('rh,status,start,end', [
    (None, 200, 0, 99),
    ('bytes=-10', 206, 90, 99),
    ('bytes=-1000', 206, 0, 99),
    ('bytes=10-29', 206, 10, 29),
    ('bytes=10-', 206, 10, 99),
    ('bytes=-0', 416, None, None),
    ('bytes=999999-', 416, None, None),
    ('bytes=30-10', 416, None, None),
    ('bytes=garbage', 400, None, None),
])
def test_preview_ranges_are_bound_to_exact_snapshot(svc, tmp_path, rh, status, start, end):
    raw = bytes(range(100))
    audio = tmp_path / 'synthetic-preview.wav'
    audio.write_bytes(raw)
    pid = 'a' * 32
    digest = hashlib.sha256(raw).hexdigest()
    svc._previews[pid] = {'id': pid, 'path': str(audio), 'bytes': len(raw), 'sha256': digest}
    got, head, body = invoke(svc, 'GET', f'/api/voice-preview/{pid}?sha256={digest}',
                             headers={} if rh is None else {'Range': rh})
    assert got == status
    if start is not None:
        assert body == raw[start:end + 1]
        assert f'Content-Length: {len(body)}\r\n' in head
        assert hashlib.sha256(body).hexdigest() == hashlib.sha256(raw[start:end + 1]).hexdigest()
        if status == 206:
            assert f'Content-Range: bytes {start}-{end}/100\r\n' in head
    elif status == 416:
        assert body == b'' and 'Content-Range: bytes */100\r\n' in head


class NativeFunction:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


@pytest.mark.parametrize('mode', ['open_osfhandle', 'fdopen'])
def test_native_handle_transfer_failure_releases_exact_owner(tmp_path, mode):
    path = tmp_path / 'self-created-config.json'
    path.write_text('{}')
    handle = 0x123456789AB
    events = []
    fds = []
    real_close = os.close
    kernel = types.SimpleNamespace(
        CreateFileW=NativeFunction(lambda *args: events.append(('create', args)) or handle),
        CloseHandle=NativeFunction(lambda h: events.append(('handle_close', h)) or 1))

    def transfer(h, flags):
        events.append(('transfer', h))
        if mode == 'open_osfhandle':
            raise OSError('synthetic transfer failure')
        fd = os.open(path, os.O_RDWR)
        fds.append(fd)
        return fd

    def fdopen(fd, *args, **kwargs):
        events.append(('fdopen', fd))
        raise OSError('synthetic fdopen failure')

    def close(fd):
        events.append(('fd_close', fd))
        real_close(fd)

    fakeos = types.SimpleNamespace(name='nt', O_RDWR=os.O_RDWR, fdopen=fdopen, close=close)
    crt = types.ModuleType('msvcrt')
    crt.open_osfhandle = transfer
    guard = setup._ConfigGuard(path)
    try:
        with patch.object(setup, 'os', fakeos), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.dict(sys.modules, {'msvcrt': crt}):
            with pytest.raises(OSError, match='synthetic'):
                guard.__enter__()
        assert events[0][1][2] == 0  # Preserve native share-mode=0.
        assert guard._file is None
        assert kernel.CloseHandle.argtypes == [ctypes.c_void_p]
        assert kernel.CloseHandle.restype is ctypes.c_int
        if mode == 'open_osfhandle':
            assert events == [('create', events[0][1]), ('transfer', handle), ('handle_close', handle)]
        else:
            assert len(fds) == 1
            assert events == [('create', events[0][1]), ('transfer', handle), ('fdopen', fds[0]), ('fd_close', fds[0])]
            with pytest.raises(OSError) as exc:
                os.fstat(fds[0])
            assert exc.value.errno == errno.EBADF
    finally:
        # Cleanup is not counted as a production release in the assertions.
        for fd in fds:
            try:
                os.fstat(fd)
            except OSError:
                continue
            real_close(fd)
