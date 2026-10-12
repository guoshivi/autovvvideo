"""外部工具和设备发现。"""
from __future__ import annotations
import functools, glob, os, shutil, subprocess, re
HERMES_FFMPEG_GLOB=os.path.expanduser("~/.hermes/tools/ffmpeg-*-darwin-*/ffmpeg")
FONT_CANDIDATES=[os.environ.get("VP_FONT",""),"/System/Library/Fonts/Hiragino Sans GB.ttc","/System/Library/Fonts/PingFang.ttc","/System/Library/Fonts/STHeiti Medium.ttc","/Library/Fonts/Arial Unicode.ttf"]
def _has_drawtext(path):
    try:
        r=subprocess.run([path,"-hide_banner","-filters"],capture_output=True,text=True,timeout=20)
        return r.returncode==0 and "drawtext" in r.stdout
    except (OSError,subprocess.SubprocessError): return False
@functools.lru_cache(maxsize=4)
def find_ffmpeg(require_drawtext=True):
    c=[]
    if os.environ.get("VP_FFMPEG"): c.append(os.environ["VP_FFMPEG"])
    c += sorted(glob.glob(HERMES_FFMPEG_GLOB), key=lambda x: tuple(int(n) for n in re.findall(r"\d+", x)), reverse=True)
    if shutil.which("ffmpeg"): c.append(shutil.which("ffmpeg"))
    seen=set()
    for p in c:
        p=os.path.abspath(p)
        if p in seen: continue
        seen.add(p)
        if os.path.isfile(p) and os.access(p,os.X_OK) and (not require_drawtext or _has_drawtext(p)): return p
    raise RuntimeError("找不到可用 ffmpeg；可设置 VP_FFMPEG，或安装带 drawtext 的 ffmpeg")
@functools.lru_cache(maxsize=4)
def find_ffprobe():
    ff=find_ffmpeg(False); sibling=os.path.join(os.path.dirname(ff),"ffprobe")
    if os.path.isfile(sibling): return sibling
    p=shutil.which("ffprobe")
    if p: return p
    raise RuntimeError("找不到与 ffmpeg 匹配的 ffprobe")
@functools.lru_cache(maxsize=1)
def find_font():
    for p in FONT_CANDIDATES:
        if p and os.path.isfile(p): return p
    raise RuntimeError("找不到中文字体；请设置 VP_FONT")
def pick_device():
    try:
        import torch
        if torch.cuda.is_available(): return "cuda"
        if torch.backends.mps.is_available(): return "mps"
    except ImportError: pass
    return "cpu"
def child_env(ffmpeg=None):
    env=dict(os.environ)
    if ffmpeg:
        dirs=[os.path.dirname(os.path.abspath(ffmpeg)), os.path.dirname(find_ffprobe())]
        env["PATH"]=os.pathsep.join(dict.fromkeys(dirs))+os.pathsep+env.get("PATH","")
    return env
