import json, os, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
root=Path(__file__).resolve().parent
cfg=root/'config'/'workbench.config.json'
log=root/'logs';log.mkdir(exist_ok=True)
py=root/'.runtime'/'embedding'/'Scripts'/'python.exe'
if not py.exists(): py=sys.executable

def _read_config():
    try:
        return json.loads(cfg.read_text(encoding='utf-8-sig'))
    except Exception:
        return {'host':'127.0.0.1','port':8765}

def _browser():
    pf=os.environ.get('ProgramFiles','')
    pf86=os.environ.get('ProgramFiles(x86)','')
    la=os.environ.get('LOCALAPPDATA','')
    candidates = [
        pf + r'\Microsoft\Edge\Application\msedge.exe',
        pf86 + r'\Microsoft\Edge\Application\msedge.exe',
        la + r'\Microsoft\Edge\Application\msedge.exe',
        pf + r'\Google\Chrome\Application\chrome.exe',
        pf86 + r'\Google\Chrome\Application\chrome.exe',
        la + r'\Google\Chrome\Application\chrome.exe',
    ]
    for name in ('msedge.exe', 'chrome.exe'):
        found=shutil.which(name)
        if found: candidates.append(found)
    return next((x for x in candidates if x and Path(x).is_file()), None)

cfg_data=_read_config()
host=str(cfg_data.get('host') or '127.0.0.1')
port=int(cfg_data.get('port') or 8765)
# A wildcard bind address is not a browsable URL: open the local loopback.
browse_host=host if host not in ('0.0.0.0','::') else '127.0.0.1'
url=f'http://{browse_host}:{port}/'
with (log/'launcher.log').open('a',encoding='utf8') as f:
    proc=subprocess.Popen([str(py),str(root/'boot_workbench.py'),'--config',str(cfg)],cwd=root,
                          stdout=f,stderr=subprocess.STDOUT,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    ready=False
    for _ in range(120):
        if proc.poll() is not None: break
        try:
            with urllib.request.urlopen(url,timeout=0.5) as response:
                ready=response.status == 200
            if ready: break
        except Exception:
            time.sleep(0.25)
    if ready:
        browser=_browser()
        if browser:
            subprocess.Popen([browser, f'--app={url}'], cwd=root,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        else:
            try:
                os.startfile(url)
            except Exception:
                f.write('No Edge/Chrome executable found; service is ready at '+url+'\n')
    else:
        f.write('Workbench did not become ready; inspect launcher.log\n')
