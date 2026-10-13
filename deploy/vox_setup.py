#!/usr/bin/env python3
"""Target-Windows-only optional Vox setup. Never modifies Qwen's environment.

Model weights are fetched only by --download on the target. No production audio
is simulated. Runtime checks do not substitute for model loading and preview.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def reject_links(path):
    p = Path(path).absolute()
    for current in (p, *p.parents):
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('linked setup path is not allowed')
    return p


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(root=ROOT):
    m = json.loads((root / 'deploy' / 'vox_dependencies.json').read_text(encoding='utf-8-sig'))
    if m.get('status') != 'VERIFIED_BY_PARENT':
        raise ValueError('Vox download manifest is unverified')
    model = m.get('model') or {}
    if model.get('repo_id') != 'openbmb/VoxCPM2':
        raise ValueError('unexpected Vox model repository')
    revision = model.get('revision', '')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('invalid fixed model revision')
    if not model.get('files_sha256'):
        raise ValueError('model checksum manifest is empty')
    return m


def model_file(model_dir, rel):
    p = Path(rel)
    if p.is_absolute() or '..' in p.parts or '\\' in rel or ':' in rel or not rel:
        raise ValueError('invalid model manifest path')
    base = reject_links(model_dir)
    target = reject_links(base / p)
    target.relative_to(base)
    return target


def verify_model(model_dir, spec):
    for rel, expected in spec['files_sha256'].items():
        p = model_file(model_dir, rel)
        if not p.is_file():
            raise ValueError('missing model file: ' + rel)
        if p.stat().st_size != spec['files_bytes'][rel]:
            raise ValueError('model byte length mismatch: ' + rel)
        if digest(p) != expected:
            raise ValueError('model SHA-256 mismatch: ' + rel)
    config = json.loads(model_file(model_dir, 'config.json').read_text(encoding='utf-8-sig'))
    if config.get('architecture', '').lower() != 'voxcpm2':
        raise ValueError('model architecture is not VoxCPM2')
    return {'repo_id': spec['repo_id'], 'revision': spec['revision'],
            'verified_files': len(spec['files_sha256']), 'files_sha256': spec['files_sha256']}


class _ConfigGuard:
    """Hold the config's write/delete exclusion for one complete commit.

    Windows uses a native handle with share mode 0, so another process cannot
    write, replace, or delete the target while the callback and readback run.
    The handle is also the writer: closing it after the in-place fsync avoids
    the replace-versus-open sharing violation.  On Unix the lock file is an
    advisory CPU-test guard; the byte recheck in ``atomic_json`` still turns a
    late non-cooperating change into a controlled refusal rather than an
    overwrite.
    """
    def __init__(self, path):
        self.path = Path(path)
        self._file = None
        self._lock_file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reject_links(self.path.parent)
        if os.name == 'nt':
            import ctypes
            import msvcrt
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32,
                                             ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_void_p]
            kernel32.CreateFileW.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.CreateFileW(str(self.path), 0xC0000000, 0, None,
                                         4, 0x80, None)  # OPEN_ALWAYS, NORMAL
            invalid = ctypes.c_void_p(-1).value
            if handle in (None, invalid):
                raise OSError(ctypes.get_last_error(), 'CreateFileW(config)')
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, 'O_BINARY', 0))
            except BaseException:
                kernel32.CloseHandle(handle)
                raise
            # The CRT now owns the HANDLE; close only its FD on wrapping failure.
            try:
                self._file = os.fdopen(fd, 'r+b', buffering=0)
            except BaseException:
                os.close(fd)
                raise
        else:
            import fcntl
            lock_path = self.path.with_name(self.path.name + '.lock')
            reject_links(lock_path)
            self._lock_file = lock_path.open('a+b')
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        return self

    def read_bytes(self):
        if self._file is not None:
            self._file.seek(0)
            return self._file.read()
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def write_bytes(self, data):
        original = self.read_bytes()
        try:
            if self._file is not None:
                self._file.seek(0)
                self._file.truncate(0)
                self._file.write(data)
                self._file.flush()
                os.fsync(self._file.fileno())
            else:
                fd, temp = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
                try:
                    with os.fdopen(fd, 'wb') as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp, self.path)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
        except Exception:
            # The Windows handle may have truncated the target before a write
            # error. Restore the in-memory backup while the exclusive handle
            # is still held, then re-raise the original failure.
            if self._file is not None and original is not None:
                try:
                    self._file.seek(0); self._file.truncate(0); self._file.write(original)
                    self._file.flush(); os.fsync(self._file.fileno())
                except Exception:
                    pass
            raise

    def __exit__(self, *exc):
        if self._file is not None:
            self._file.close()
            self._file = None
        if self._lock_file is not None:
            import fcntl
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None
        return False


def atomic_json(path, obj):
    path = reject_links(path)
    with _ConfigGuard(path) as guard:
        # Capture the bytes after taking the guard.  A callable gets to read
        # and merge the current object inside this same protected interval.
        initial = guard.read_bytes()
        value = obj(guard.read_bytes) if callable(obj) else obj
        if not isinstance(value, dict):
            raise ValueError('atomic JSON payload must be an object')
        # A cooperative or CPU-simulated late writer is never overwritten.
        if guard.read_bytes() != initial:
            raise ValueError('configuration changed during protected commit; refusing overwrite')
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        guard.write_bytes(encoded)
        raw = guard.read_bytes()
        try:
            actual = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise ValueError('atomic JSON readback failed') from exc
        if actual != value:
            raise ValueError('atomic JSON readback failed')
        return actual


def download_model(model_dir, spec):
    from huggingface_hub import snapshot_download
    model_dir = reject_links(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    # Inspect all manifest destinations before the library can write there.
    for rel in spec['files_sha256']:
        model_file(model_dir, rel)
    snapshot_download(repo_id=spec['repo_id'], revision=spec['revision'],
                      local_dir=str(model_dir), allow_patterns=list(spec['files_sha256']))
    checked = verify_model(model_dir, spec)
    atomic_json(model_dir / 'REVISION', checked)
    return checked


def check_runtime(spec):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    from voxcpm import VoxCPM
    if importlib.metadata.version('voxcpm') != spec['sdk']['version']:
        raise ValueError('Vox SDK version does not match the verified manifest')
    if torch.__version__ != spec['torch_version'] or torchaudio.__version__ != spec['torchaudio_version']:
        raise ValueError('torch / torchaudio must use the matched manifest versions')
    if not torch.cuda.is_available():
        raise ValueError('CUDA is unavailable in the optional Vox environment')
    if not torch.cuda.is_bf16_supported():
        raise ValueError('Vox runtime BF16 check failed')
    # Actual CPU WAV roundtrip and audio resampling; no CUDA/model allocation.
    with tempfile.TemporaryDirectory(prefix='vox-runtime-', dir=ROOT / 'data') as td:
        p = Path(td) / 'probe.wav'
        x = np.sin(np.arange(4800, dtype=np.float64) * 0.05) * 0.1
        sf.write(str(p), x, 48000, subtype='PCM_16')
        y, rate = sf.read(str(p))
        if rate != 48000 or len(y) != len(x):
            raise ValueError('soundfile WAV roundtrip failed')
        z = torchaudio.functional.resample(torch.from_numpy(y), 48000, 24000)
        if z.numel() != 2400:
            raise ValueError('torchaudio real resampling failed')
    return {'sdk': importlib.metadata.version('voxcpm'), 'torch': torch.__version__,
            'torchaudio': torchaudio.__version__, 'cuda_available': True,
            'scope': 'imports + CPU WAV/resample + CUDA availability; model synthesis remains pending'}


def commit_config(root, model_dir, python, cache_dir):
    cfg_path = reject_links(root / 'config' / 'workbench.config.json')
    from workbench.config import RuntimeConfig

    def merge_current(read_bytes):
        raw = read_bytes()
        if raw is None:
            raise ValueError('configuration file is missing')
        cfg = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(cfg, dict):
            raise ValueError('configuration root must be an object')
        after = dict(cfg)
        after.update(voxcpm_python=str(python), voxcpm_model=str(model_dir), voxcpm_cache=str(cache_dir))
        RuntimeConfig.from_dict(after)
        return after

    # Reading, validating, merging, writing, and readback now happen inside
    # atomic_json's protected interval.  A late user selection is merged from
    # the current bytes or rejected; it cannot be overwritten by a stale
    # pre-lock snapshot.
    actual = atomic_json(cfg_path, merge_current)
    return {'qwen_configuration_preserved': True, 'optional_vox_configured': True}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--configure', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    if not any((args.download, args.configure, args.check)):
        ap.error('choose --download, --check, or --configure')
    if os.name != 'nt':
        ap.error('run optional Vox setup on the target Windows machine only')
    root = reject_links(ROOT)
    spec = read_manifest(root)
    model_dir = root / 'models' / 'vox'
    python = root / '.runtime' / 'vox' / 'Scripts' / 'python.exe'
    cfg = json.loads((root / 'config' / 'workbench.config.json').read_text(encoding='utf-8-sig'))
    data = Path(cfg['data_root'])
    if not data.is_absolute():
        data = root / data
    cache = reject_links(data / 'voice-cache' / 'vox')
    results = {}
    if args.download:
        results['download'] = download_model(model_dir, spec['model'])
    if args.check or args.configure:
        results['model'] = verify_model(model_dir, spec['model'])
        results['runtime'] = check_runtime(spec)
    if args.configure:
        results['config'] = commit_config(root, model_dir, python, cache)
    print(json.dumps({'ok': True, 'checks': results,
                      'next': 'restart workbench, select Vox, generate a real voice preview, then one real film'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
