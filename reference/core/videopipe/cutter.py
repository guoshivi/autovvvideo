"""Bounded, source-safe rendering. Text/font use controlled relative paths."""
from __future__ import annotations
import json, math, os, shutil, subprocess, tempfile, uuid
from pathlib import Path
from . import env

def choose_range(duration,want=4.0,pos=.35):
    d=float(duration); want=float(want); pos=float(pos)
    if not all(math.isfinite(x) for x in (d,want,pos)) or d<=0 or want<=0:
        raise ValueError("duration/want must be finite and positive")
    ss=min(max(0.0,d*pos),max(0.0,d-want))
    return ss,min(want,d-ss)

def normalize_filter(w=1080,h=1920):
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"

def probe(path):
    r=subprocess.run([env.find_ffprobe(),"-v","error","-select_streams","v:0","-show_entries","format=duration:stream=duration,width,height,r_frame_rate,codec_name","-of","json",str(path)],capture_output=True,text=True,timeout=30)
    if r.returncode or not r.stdout.strip(): raise RuntimeError(f"ffprobe failed: {path}")
    x=json.loads(r.stdout); streams=x.get('streams',[])
    if not streams: raise RuntimeError(f"无视频流: {path}")
    st=streams[0]; dur=float(st.get('duration') or x.get('format',{}).get('duration') or 0)
    if not math.isfinite(dur) or dur<=0: raise RuntimeError(f"无可播放视频: {path}")
    return {**st,'duration':dur}

def probe_duration(path): return probe(path)['duration']
def _atomic_probe_move(tmp,dst):
    probe(tmp); os.replace(tmp,dst)

def cut_segment(src,dst,ss,t,vf="",fps=30,caption_path=None,font_path=None,caption_size=54,caption_y=380,force=False):
    source=Path(src).expanduser().resolve(); out=Path(dst).expanduser().absolute()
    if source==out.resolve() or (out.exists() and source.samefile(out)): raise ValueError("源与目标路径相同，拒绝覆盖素材")
    if out.exists() and not force: raise FileExistsError(f"输出已存在: {out}")
    if out.is_symlink(): raise ValueError("拒绝符号链输出")
    dur=probe_duration(source); t=float(t)
    if ss is None: ss,t=choose_range(dur,t)
    else:
        ss=float(ss)
        if not math.isfinite(ss) or not math.isfinite(t) or ss<0 or ss>=dur or t<=0: raise ValueError("无有效切割范围")
        t=min(t,dur-ss)
    if fps<=0: raise ValueError("fps 必须为正")
    if bool(caption_path)!=bool(font_path): raise ValueError("字幕和字体必须同时提供")
    out.parent.mkdir(parents=True,exist_ok=True)
    tmp=out.with_name(out.name+f".tmp.{uuid.uuid4().hex}.mp4")
    ff=env.find_ffmpeg(bool(caption_path))
    try:
        with tempfile.TemporaryDirectory(prefix='vp-render-',dir=os.environ.get('TMPDIR',str(out.parent))) as wd:
            filters=[vf or normalize_filter()]
            if caption_path:
                shutil.copyfile(caption_path,Path(wd)/'caption.txt')
                shutil.copyfile(font_path,Path(wd)/'font.ttc')
                from .caption import drawtext_filter
                filters.append(drawtext_filter('caption.txt','font.ttc',caption_size,caption_y))
            cmd=[ff,'-y','-v','error','-ss',f'{ss:.9f}','-t',f'{t:.9f}','-i',str(source),'-vf',','.join(filters),'-an','-c:v','libx264','-preset','veryfast','-crf','20','-r',str(fps),'-pix_fmt','yuv420p',str(tmp)]
            r=subprocess.run(cmd,cwd=wd,env=env.child_env(ff),capture_output=True,text=True,timeout=300)
            if r.returncode: raise RuntimeError(r.stderr[-2000:])
            _atomic_probe_move(tmp,out)
    finally:
        if tmp.exists(): tmp.unlink()
