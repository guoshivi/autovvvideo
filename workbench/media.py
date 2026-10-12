from pathlib import Path
import hashlib,re,ast,math,json,subprocess

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def range_response(path,header):
    size=Path(path).stat().st_size
    if not header:return (200,0,size-1,size)
    m=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
    if not m or not any(m.groups()):return (416,0,0,0)
    a,b=m.groups()
    if not a: start=max(0,size-int(b));end=size-1
    else:start=int(a);end=int(b) if b else size-1
    if start<0 or start>=size or end<start:return (416,0,0,0)
    end=min(end,size-1);return (206,start,end,end-start+1)
def pcm_correlation(expected,actual):
    """Legacy six-test byte fixture only; production uses frozen correlate_pcm."""
    if not expected or not actual:return {'ok':False,'reason':'empty'}
    n=min(len(expected),len(actual));same=sum(a==b for a,b in zip(expected[:n],actual[:n]))/n
    return {'ok':same>=.97 and len(expected)==len(actual),'ratio':same}
# Extract the frozen function AST verbatim; avoid importing its Windows-only runtime.
_reference=Path(__file__).resolve().parent.parent/'reference/v1/runtime_support.py'
_tree=ast.parse(_reference.read_text(encoding='utf8'))
_node=next(n for n in _tree.body if isinstance(n,ast.FunctionDef) and n.name=='correlate_pcm')
exec(compile(ast.Module(body=[_node],type_ignores=[]),str(_reference),'exec'),globals())

def require_check(name,value):
    if value is not True:raise ValueError(f'verify/{name}: required true; measured={value!r}')
def validate_report(report,path):
    if not isinstance(report,dict) or report.get('ok') is not True:raise ValueError('verify/report.ok: required true')
    checks=report.get('checks')
    if not isinstance(checks,dict) or not checks:raise ValueError('verify/checks: complete applicable checks required')
    for name,value in checks.items():
        if value is not True and not (isinstance(value,dict) and value.get('applicable') is False and isinstance(value.get('reason'),str) and value['reason']):require_check(name,value)
    p=Path(path)
    if not p.is_file():raise ValueError('verify/output: validated file missing')
    got={'sha256':sha256(p),'bytes':p.stat().st_size}
    if report.get('sha256')!=got['sha256'] or report.get('bytes')!=got['bytes']:raise ValueError('verify/output SHA or bytes changed')
    return got
