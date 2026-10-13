#!/usr/bin/env python3
"""Index-diagnosis for autovvvideo — run this ON the machine where indexing fails.

It mirrors exactly what the workbench does while indexing:
  1. ffprobe the video,
  2. cut a 480p window with the same ffmpeg command (GPU first, CPU fallback),
  3. ask the same decoder stack (torchcodec) to open/decode that window.

Everything it finds is written to  deploy\\diagnose_out\\  — send that folder back.

Usage (from the repo root, on the failing machine):

  .runtime\\embedding\\Scripts\\python.exe deploy\\diagnose_index.py "D:\\path\\to\\the\\indexed\\folder"

Optional:  --all-windows   test every 10s window instead of just the first two.
"""
import argparse, ctypes, ctypes.wintypes as wt, json, os, shutil, subprocess, sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.wmv', '.ts', '.mts'}
OUT = ROOT / 'deploy' / 'diagnose_out'


def read_config():
    for rel in ('config/workbench.config.json', 'workbench.config.json'):
        p = ROOT / rel
        if p.is_file():
            return resolve_paths(json.loads(p.read_text(encoding='utf-8-sig')))
    raise SystemExit('config not found: expected config/workbench.config.json (run install.ps1 first)')


def resolve_paths(cfg):
    """Accept both installed configs (absolute paths) and example configs (relative)."""
    for key in ('tools_dir', 'ffmpeg', 'ffprobe', 'fdk_encoder', 'model_dir', 'tts_model',
                'tts_python', 'embedding_python', 'gpu_lock_path', 'data_root', 'output_root'):
        v = cfg.get(key)
        if isinstance(v, str) and v and not Path(v).is_absolute():
            cfg[key] = str((ROOT / v).resolve())
    return cfg


def prep_runtime(cfg):
    """Mirror boot_workbench.py: make the tools dir (shared FFmpeg DLLs) visible."""
    tools = str(cfg.get('tools_dir') or '')
    if tools and Path(tools).is_dir():
        os.environ['PATH'] = tools + os.pathsep + os.environ.get('PATH', '')
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(tools)
            except OSError:
                pass
    return tools


def run(cmd, timeout=120):
    try:
        r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
        return r.returncode, r.stdout or '', r.stderr or ''
    except Exception as e:
        return 99, '', f'{type(e).__name__}: {e}'


def probe(cfg, p):
    rc, out, err = run([cfg['ffprobe'], '-v', 'error', '-show_entries',
                        'format=duration,format_name:stream=codec_name,width,height,r_frame_rate,avg_frame_rate',
                        '-of', 'json', str(p)])
    if rc:
        return {'ok': False, 'error': err[-300:]}
    try:
        return {'ok': True, 'data': json.loads(out)}
    except Exception:
        return {'ok': False, 'error': 'bad ffprobe json'}


def cut_window(cfg, p, start, seconds, dest):
    gpu = [cfg['ffmpeg'], '-nostdin', '-n', '-v', 'error', '-hwaccel', 'cuda',
           '-hwaccel_output_format', 'cuda', '-ss', str(start), '-t', str(seconds), '-i', str(p),
           '-map', '0:v:0', '-an', '-vf', 'scale_cuda=-2:480', '-c:v', 'h264_nvenc',
           '-preset', 'p1', '-cq', '28', str(dest)]
    cpu = [cfg['ffmpeg'], '-nostdin', '-n', '-v', 'error', '-ss', str(start), '-t', str(seconds),
           '-i', str(p), '-map', '0:v:0', '-an', '-vf', 'scale=-2:480', '-c:v', 'libx264',
           '-preset', 'ultrafast', '-crf', '28', str(dest)]
    rc, _, err = run(gpu, timeout=300)
    used = 'gpu'
    if rc or not Path(dest).is_file():
        try:
            Path(dest).unlink()
        except OSError:
            pass
        rc, _, err = run(cpu, timeout=300)
        used = 'cpu'
    if rc or not Path(dest).is_file():
        return {'ok': False, 'encoder': used, 'error': err[-300:]}
    return {'ok': True, 'encoder': used, 'bytes': Path(dest).stat().st_size}


def decode_test(clip):
    """Exactly what indexing asks of the decoder stack."""
    try:
        from torchcodec.decoders import VideoDecoder
        dec = VideoDecoder(str(clip))
        try:
            frame = dec[0]
        except TypeError:
            frame = dec.get_frame_at(0)
        try:
            n = len(dec)
        except Exception:
            n = None
        return {'ok': True, 'frames': n, 'frame_type': type(frame).__name__}
    except Exception as e:
        return {'ok': False, 'error': ''.join(traceback.format_exception_only(type(e), e)).strip()[:600]}


def loaded_ffmpeg_dlls():
    names = ('avcodec-62.dll', 'avcodec-61.dll', 'avcodec-60.dll', 'avformat-62.dll',
             'avformat-61.dll', 'avformat-60.dll', 'avutil-60.dll', 'avutil-59.dll',
             'avutil-58.dll', 'swresample-6.dll', 'swscale-9.dll')
    k32 = ctypes.windll.kernel32
    out = {}
    for name in names:
        h = k32.GetModuleHandleW(name)
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(2048)
        fn = k32.K32GetModuleFileNameExW
        fn.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
        fn.restype = wt.DWORD
        if fn(k32.GetCurrentProcess(), h, buf, 2048):
            out[name] = buf.value
    return out


def header_hex(p, n=32):
    try:
        with open(p, 'rb') as f:
            return f.read(n).hex()
    except OSError:
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder', help='the folder you tried to index')
    ap.add_argument('--all-windows', action='store_true')
    ap.add_argument('--max-files', type=int, default=30)
    args = ap.parse_args()

    cfg = read_config()
    tools = prep_runtime(cfg)

    report = {'python': sys.executable, 'root': str(ROOT), 'tools_dir': tools}
    report['which_ffmpeg'] = shutil.which('ffmpeg')
    rc, out, _ = run([cfg['ffmpeg'], '-version'])
    report['ffmpeg_version'] = (out.splitlines() or ['?'])[0][:200] if rc == 0 else 'ffmpeg not runnable'
    rc, out, _ = run(['where.exe', 'ffmpeg'])
    report['where_ffmpeg'] = out.strip().splitlines() if rc == 0 else []
    try:
        import torch
        report['torch'] = torch.__version__
        report['cuda_available'] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            report['gpu'] = torch.cuda.get_device_name(0)
    except Exception as e:
        report['torch_error'] = str(e)[:200]
    try:
        import torchvision
        report['torchvision'] = torchvision.__version__
    except Exception:
        pass
    try:
        import torchcodec
        report['torchcodec'] = getattr(torchcodec, '__version__', 'unknown')
    except Exception as e:
        report['torchcodec_error'] = str(e)[:300]

    folder = Path(args.folder)
    files = sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in VIDEO_EXT)
    report['folder'] = str(folder)
    report['video_count'] = len(files)

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / 'clips'
    tmp.mkdir(exist_ok=True)
    rows = []
    failures = 0
    for p in files[:args.max_files]:
        row = {'file': str(p), 'name': p.name, 'size': p.stat().st_size,
               'ext': p.suffix.lower(), 'head_hex': header_hex(p)}
        pr = probe(cfg, p)
        row['probe'] = pr
        duration = None
        if pr.get('ok'):
            try:
                duration = float(pr['data'].get('format', {}).get('duration') or 0)
            except (TypeError, ValueError):
                duration = None
        windows = [0.0]
        if duration and duration > 11:
            windows += [duration / 2]
        if args.all_windows and duration:
            windows = [w for w in range(0, int(duration), 10)]
        row['windows'] = []
        for wi, start in enumerate(windows):
            sec = 10.0
            if duration:
                sec = min(10.0, max(0.5, duration - start))
            clip = tmp / f"{p.stem[:24]}-w{wi}.mp4"
            try:
                if clip.exists():
                    clip.unlink()
            except OSError:
                pass
            wres = {'start': start, 'seconds': sec}
            c = cut_window(cfg, p, start, sec, clip)
            wres['cut'] = c
            if c.get('ok'):
                d = decode_test(clip)
                wres['decode'] = d
                if not d.get('ok'):
                    failures += 1
                    keep = tmp / f"FAIL-{p.stem[:20]}-w{wi}.mp4"
                    try:
                        shutil.copyfile(clip, keep)
                        wres['saved_clip'] = str(keep)
                    except OSError:
                        pass
                try:
                    clip.unlink()
                except OSError:
                    pass
            row['windows'].append(wres)
        rows.append(row)
        status = 'FAIL' if any(not (w.get('decode') or {}).get('ok', True) for w in row['windows']) else 'ok'
        print(f"[{status}] {p.name}  size={row['size']}")
        for wres in row['windows']:
            if not (wres.get('decode') or {}).get('ok', True):
                print('    decode error:', (wres.get('decode') or {}).get('error', '')[:300])

    report['files'] = rows
    report['decode_failures'] = failures
    report['loaded_ffmpeg_dlls'] = loaded_ffmpeg_dlls()
    (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print()
    print('=' * 60)
    print('environment:')
    for k in ('python', 'torch', 'cuda_available', 'gpu', 'torchvision', 'torchcodec',
              'ffmpeg_version', 'which_ffmpeg', 'where_ffmpeg', 'tools_dir'):
        if k in report:
            print(f'  {k}: {report[k]}')
    if report.get('loaded_ffmpeg_dlls'):
        print('  loaded FFmpeg DLLs:')
        for k, v in report['loaded_ffmpeg_dlls'].items():
            print(f'    {k} -> {v}')
    print('=' * 60)
    print(f'videos tested: {len(rows)}  decode failures: {failures}')
    print(f'REPORT + failing clips saved to: {OUT}')
    print('Send me that whole folder (zip it) — it tells me exactly which file/step breaks.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
