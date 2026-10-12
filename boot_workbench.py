#!/usr/bin/env python3
"""Deployment bootstrap for the private embedded Python.

The private embedded Python intentionally excludes arbitrary script directories
from sys.path. Add only this authorized app in-process for this process (and its
children via PYTHONPATH); never edit the private _pth or PATH configuration.
Also mirror the proven V1 runtime prep: make the trial tools dir (FFmpeg shared
DLLs for torchcodec) discoverable before the embedding stack loads.
"""
from pathlib import Path
import json, os, sys, runpy

sys.dont_write_bytecode = True
APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
os.environ['PYTHONPATH'] = str(APP) + os.pathsep + os.environ.get('PYTHONPATH', '')

_cfg_path = None
for _i, _a in enumerate(sys.argv):
    if _a == '--config' and _i + 1 < len(sys.argv):
        _cfg_path = sys.argv[_i + 1]
        break
_cfg = {}
if _cfg_path:
    try:
        _cfg = json.loads(Path(_cfg_path).read_text(encoding='utf-8-sig'))
    except Exception:
        _cfg = {}

_tools = str(_cfg.get('tools_dir') or '')
if _tools and Path(_tools).is_dir():
    os.environ['PATH'] = _tools + os.pathsep + os.environ.get('PATH', '')
    _dll_handles = []
    if hasattr(os, 'add_dll_directory'):
        try:
            _dll_handles.append(os.add_dll_directory(_tools))
        except OSError:
            pass

for _k, _key in (('VP_FFMPEG', 'ffmpeg'), ('VP_FFPROBE', 'ffprobe'), ('VP_FONT', 'font_path')):
    _v = _cfg.get(_key)
    if _v:
        os.environ.setdefault(_k, str(_v))

if __name__ == '__main__':
    runpy.run_path(str(APP / 'start_workbench.py'), run_name='__main__')
