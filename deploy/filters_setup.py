"""Install/probe the optional OpenCV filters environment without touching AI envs."""
from pathlib import Path
import subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
VENV=ROOT/'.runtime'/'filters'
PY=VENV/('Scripts/python.exe' if sys.platform.startswith('win') else 'bin/python')
if not PY.exists(): subprocess.check_call([sys.executable,'-m','venv',str(VENV)])
subprocess.check_call([str(PY),'-m','pip','install','--disable-pip-version-check','--only-binary=:all:','-r',str(ROOT/'requirements-filters.txt')])
subprocess.check_call([str(PY),'-c','import cv2,numpy; print(cv2.__version__, numpy.__version__)'])
print(PY)
