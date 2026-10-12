#!/usr/bin/env python3
"""Read-only deployment health checks.

Normal invocation never allocates CUDA memory and never prints a secret.
``--heavy-gpu`` opts into a small CUDA/BF16 operation on the first GPU.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def item(name, status, detail): return {'name': name, 'status': status, 'detail': detail}
def run(cmd, timeout=30, env=None):
    try:
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,env=env)
        return p.returncode,p.stdout,p.stderr
    except Exception as exc: return 99,'',str(exc)
def configured_path(cfg,key):
    raw=Path(str(cfg.get(key) or '')); return raw if raw.is_absolute() else ROOT/raw
def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()
def manifest_check(model,spec):
    expected=spec.get('files_sha256') or {}; sizes=spec.get('files_bytes') or {}
    if not expected:return False,'manifest has no files'
    for rel,digest in expected.items():
        p=model/rel
        if not p.is_file():return False,f'missing {rel}'
        if sizes.get(rel) is not None and p.stat().st_size != sizes[rel]:return False,f'byte length mismatch {rel}'
        if sha256(p)!=digest:return False,f'SHA-256 mismatch {rel}'
    marker=model/'REVISION'
    if not marker.is_file() or spec.get('revision','') not in marker.read_text(encoding='utf-8-sig'):
        return False,'REVISION evidence missing or unexpected'
    return True,f'{len(expected)} manifest files and revision verified'
def runtime_check(py,label):
    if not py.is_file():return item(label,'WARN','isolated Python is not installed')
    code="import importlib; [importlib.import_module(x) for x in ('torch','numpy')]; import torch; print(torch.__version__)"
    rc,_,_=run([str(py),'-c',code],90)
    return item(label,'PASS' if rc==0 else 'FAIL','torch/numpy import succeeded' if rc==0 else 'isolated runtime import failed')
def fdk_probe(ffmpeg,ffprobe,fdk,tools):
    if not (ffmpeg.is_file() and ffprobe.is_file() and fdk.is_file()):return item('fdkaac.actual','WARN','FFmpeg, ffprobe and fdkaac are not all installed')
    env={**os.environ,'PATH':str(tools)+os.pathsep+os.environ.get('PATH','')}
    with tempfile.TemporaryDirectory(prefix='autovvvideo-doctor-') as td:
        d=Path(td); wav=d/'probe.wav'; encoded=d/'probe.m4a'; decoded=d/'decoded.wav'
        rc,_,_=run([str(ffmpeg),'-v','error','-f','lavfi','-i','sine=frequency=880:duration=1','-ac','1','-ar','24000','-c:a','pcm_s16le',str(wav)],60,env)
        if rc or not wav.is_file():return item('fdkaac.actual','FAIL','CPU test WAV synthesis failed')
        rc,_,_=run([str(fdk),'-b','136000','-w','12000','-a','1','-G','2','-o',str(encoded),str(wav)],60,env)
        if rc or not encoded.is_file():return item('fdkaac.actual','FAIL','fdkaac CPU encode failed')
        rc,out,_=run([str(ffprobe),'-v','error','-show_entries','stream=codec_name,bit_rate:packet=stream_index,pts_time,size','-of','json',str(encoded)],30)
        if rc:return item('fdkaac.actual','FAIL','ffprobe could not read fdkaac output')
        try:
            doc=json.loads(out); streams=doc.get('streams') or []; packets=doc.get('packets') or []
            rates=[int(x['bit_rate']) for x in streams if str(x.get('bit_rate','')).isdigit()]
            for p in packets:
                if p.get('size') and p.get('pts_time'): rates.append(int(float(p['size'])*8/max(float(p['pts_time']),.001)))
            measured=max(rates,default=0)
        except Exception:return item('fdkaac.actual','FAIL','ffprobe bitrate result was malformed')
        if measured<128000:return item('fdkaac.actual','FAIL','measured AAC bitrate is below 128000 bps')
        rc,_,_=run([str(ffmpeg),'-v','error','-i',str(encoded),'-f','wav','-y',str(decoded)],60,env)
        if rc or not decoded.is_file():return item('fdkaac.actual','FAIL','decoded AAC exit code was nonzero')
        return item('fdkaac.actual','PASS','CPU WAV -> fdkaac -> ffprobe bitrate >=128000 -> decode passed')
def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--check-config-only',action='store_true');ap.add_argument('--heavy-gpu',action='store_true',help='opt in to a real CUDA/BF16 allocation'); args=ap.parse_args(argv)
    rows=[]; cfg_path=ROOT/'config'/'workbench.config.json'
    try:
        cfg=json.loads(cfg_path.read_text(encoding='utf-8-sig'))
        if not isinstance(cfg,dict):raise ValueError()
        rows.append(item('config','PASS','UTF-8/BOM JSON loaded'))
    except Exception:return emit([item('config','FAIL','missing or invalid config')])
    host_ok=cfg.get('host')=='127.0.0.1';rows.append(item('config.host','PASS' if host_ok else 'FAIL','local-only host' if host_ok else 'host must remain 127.0.0.1'))
    try:port_ok=type(cfg.get('port')) is int and 1<=cfg['port']<=65535
    except Exception:port_ok=False
    rows.append(item('config.port','PASS' if port_ok else 'FAIL','valid TCP port' if port_ok else 'invalid TCP port'))
    for key in ('data_root','nas_roots','output_root','tools_dir','ffmpeg','ffprobe','fdk_encoder','embedding_python','tts_python','model_dir','tts_model','font_path'):
        ok=bool(cfg.get(key));rows.append(item('config.'+key,'PASS' if ok else 'FAIL','configured' if ok else 'missing'))
    for key in ('data_root','output_root','tools_dir','model_dir','tts_model'):
        p=configured_path(cfg,key);rows.append(item('path.'+key,'PASS' if p.exists() else 'WARN','exists' if p.exists() else 'not installed'))
    if args.check_config_only:
        rows.append(item('install.complete','WARN','configuration-only check; dependencies, models and media probes are pending'));return emit(rows)
    tools=configured_path(cfg,'tools_dir');ffmpeg=configured_path(cfg,'ffmpeg');ffprobe=configured_path(cfg,'ffprobe');fdk=configured_path(cfg,'fdk_encoder');font=configured_path(cfg,'font_path')
    for label,path in (('ffmpeg',ffmpeg),('ffprobe',ffprobe),('fdkaac',fdk),('font',font)):
        rows.append(item(label,'PASS' if path.is_file() else 'FAIL','file present' if path.is_file() else 'missing'))
    if ffmpeg.is_file():
        rc,out,_=run([str(ffmpeg),'-hide_banner','-filters']);ok=rc==0 and 'drawtext' in out;rows.append(item('ffmpeg.drawtext','PASS' if ok else 'FAIL','drawtext filter available' if ok else 'drawtext filter missing'))
    dlls=list(tools.glob('*.dll')) if tools.is_dir() else [];rows.append(item('ffmpeg.shared-dlls','PASS' if dlls else 'FAIL','shared build DLLs present' if dlls else 'no FFmpeg DLLs in tools directory'))
    rows.append(fdk_probe(ffmpeg,ffprobe,fdk,tools)); emb=configured_path(cfg,'embedding_python');tts=configured_path(cfg,'tts_python');rows.append(runtime_check(emb,'embedding.runtime'));rows.append(runtime_check(tts,'tts.runtime'))
    try:
        manifest=json.loads((ROOT/'deploy'/'dependencies.json').read_text(encoding='utf-8-sig'));models=manifest['assets']['models']
        for key,cfg_key in (('embedding','model_dir'),('tts','tts_model')):
            ok,detail=manifest_check(configured_path(cfg,cfg_key),models[key]);rows.append(item('model.'+key,'PASS' if ok else 'WARN',detail))
    except Exception:rows.append(item('model.manifest','WARN','manifest could not be checked'))
    rc,out,_=run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits']);rows.append(item('nvidia','PASS' if rc==0 and out.strip() else 'WARN','driver query succeeded' if rc==0 and out.strip() else 'NVIDIA driver/GPU not available'))
    if args.heavy_gpu:
        code="import torch; assert torch.cuda.is_available(); assert torch.cuda.is_bf16_supported(); x=torch.ones((32,32),device='cuda',dtype=torch.bfloat16); y=x@x; torch.cuda.synchronize(); print(torch.cuda.get_device_name(0),float(y[0,0]))"
        rc,_,_=run([str(emb),'-c',code],120);rows.append(item('cuda-bf16-heavy','PASS' if rc==0 else 'FAIL','real CUDA/BF16 allocation and matmul passed' if rc==0 else 'real CUDA/BF16 probe failed'))
    else:rows.append(item('cuda-bf16-heavy','WARN','opt-in only; no GPU was allocated'))
    for key in ('data_root','output_root','tools_dir'):
        p=configured_path(cfg,key)
        try:p.mkdir(parents=True,exist_ok=True);ok=os.access(p,os.R_OK|os.W_OK)
        except Exception:ok=False
        rows.append(item('permissions.'+key,'PASS' if ok else 'FAIL','read/write check'))
    return emit(rows)
def emit(rows):
    status='FAIL' if any(x['status']=='FAIL' for x in rows) else ('WARN' if any(x['status']=='WARN' for x in rows) else 'PASS');print(json.dumps({'status':status,'checks':rows},ensure_ascii=False,separators=(',',':')));return 2 if status=='FAIL' else (1 if status=='WARN' else 0)
if __name__=='__main__':raise SystemExit(main())
