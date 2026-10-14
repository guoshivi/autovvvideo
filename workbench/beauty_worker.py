"""Controlled worker entry point for optional local beauty processing."""
import json, sys
from pathlib import Path
try:
    from .beauty import process_video
except ImportError:  # direct file entry used by the isolated filters env
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from workbench.beauty import process_video


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if len(argv)!=1: raise SystemExit('usage: beauty_worker request.json')
    req=json.loads(Path(argv[0]).read_text(encoding='utf8'))
    allowed={'source','output','report','strength','fps','width','height','expected_frames'}
    if set(req)-allowed: raise ValueError('beauty request contains unknown fields')
    result=process_video(req['source'],req['output'],req.get('strength',.25),fps=req.get('fps',30),width=req.get('width',1080),height=req.get('height',1920),expected_frames=req.get('expected_frames'))
    Path(req['report']).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')


if __name__=='__main__': main()
