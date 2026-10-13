"""Diagnostic-helper regressions; mocks here do not prove GPU indexing works."""
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'diagnose_index', Path(__file__).resolve().parents[1] / 'deploy' / 'diagnose_index.py')
assert SPEC is not None and SPEC.loader is not None
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)


def test_dll_directory_handle_is_retained(tmp_path):
    handle = object()
    with patch.object(diag.os, 'add_dll_directory', return_value=handle, create=True):
        diag.prep_runtime({'tools_dir': str(tmp_path)})
    assert handle in getattr(diag, '_dll_handles', [])


def test_module_handle_signature_is_pointer_sized():
    class Function:
        def __init__(self, impl):
            self.impl = impl
        def __call__(self, *args):
            return self.impl(*args)

    handle = 0x123456789ABC  # Deliberately exceeds a 32-bit handle.
    seen = []
    def module_name(h, buf, capacity):
        seen.append(h)
        buf.value = r'C:\project\tools\avcodec-62.dll'
        return len(buf.value)
    api = SimpleNamespace(
        GetModuleHandleW=Function(lambda name: handle if name == 'avcodec-62.dll' else None),
        GetModuleFileNameW=Function(module_name))
    with patch.object(diag.os, 'name', 'nt'), \
         patch.object(diag.ctypes, 'WinDLL', return_value=api, create=True):
        result = diag.loaded_ffmpeg_dlls()
    assert ctypes.sizeof(api.GetModuleHandleW.restype) == ctypes.sizeof(ctypes.c_void_p)
    assert seen == [handle]
    assert result['avcodec-62.dll'] == r'C:\project\tools\avcodec-62.dll'


def test_cut_failure_cannot_be_reported_as_success():
    assert not diag.window_ok({'cut': {'ok': False}})
    assert not diag.window_ok({'cut': {'ok': True}, 'decode': {'ok': False}})
    assert diag.window_ok({'cut': {'ok': True}, 'decode': {'ok': True}})
