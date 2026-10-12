"""安全字幕：textfile + expansion=none，保留所有文字。"""
from __future__ import annotations
from pathlib import Path
def wrap_caption(text,max_chars=12):
    if max_chars<=0: raise ValueError('max_chars 必须为正')
    text=str(text)
    out=[]
    for raw in text.splitlines() or [""]:
        if len(raw)<=max_chars: out.append(raw); continue
        out.extend(raw[i:i+max_chars] for i in range(0,len(raw),max_chars))
    return "\n".join(out)
def write_caption_file(dir_path,idx,text):
    p=Path(dir_path)/f"cap_{idx:02d}.txt"; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(wrap_caption(text),encoding="utf-8"); return str(p)
def _escape(v):
    # Escape the option parser, then the filtergraph parser (no shell).
    first = ''.join('\\'+c if c in "\\':" else c for c in str(v))
    return ''.join('\\'+c if c in "\\'[],;" else c for c in first)
# Vertical pitch between wrapped lines, calibrated on the production ffmpeg +
# msyh.ttc: a multi-line drawtext advances one line by round(a*size+b) pixels
# (measured 109/134/167/197 at sizes 32/42/54/66).  Per-line drawtext uses the
# same advance so the block geometry matches the old single-drawtext layout.
LINE_PITCH_SLOPE = 2.606
LINE_PITCH_INTERCEPT = 25.35
def drawtext_filter(caption_path,font_path,size=54,y_from_bottom=380,border=3):
    """Per-line drawtext so EVERY wrapped line is centered on its own.

    A single multi-line drawtext centers the text BLOCK by its widest line and
    left-aligns the shorter inner lines, so the second row drifts off-center.
    Each line is drawn separately with x=(w-text_w)/2; lines stack downward
    from the same anchor (first line top stays at h-y_from_bottom)."""
    border=int(border); size=int(size)
    stroke=f":borderw={border}:bordercolor=black@0.7" if border>0 else ""
    p=Path(caption_path)
    lines=[l for l in p.read_text(encoding='utf-8').splitlines() if l] or ['']
    pitch=round(size*LINE_PITCH_SLOPE+LINE_PITCH_INTERCEPT)
    out=[]
    for i,line in enumerate(lines):
        lp=p.parent/(p.stem+f'_l{i}.txt'); lp.write_text(line,encoding='utf-8')
        y=f"h-{int(y_from_bottom)}" if i==0 else f"h-{int(y_from_bottom)}+{i*pitch}"
        out.append(f"drawtext=fontfile={_escape(font_path)}:textfile={_escape(str(lp))}:expansion=none:fontcolor=white:fontsize={size}{stroke}:x=(w-text_w)/2:y={y}")
    return ','.join(out)
