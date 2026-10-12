from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from pathlib import Path
import json, mimetypes, os, hashlib, time, tempfile
from email.message import Message
from contextlib import ExitStack
from .media import range_response,sha256
from .browser import BrowserError
class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version='Workbench/2.0'
    def log_message(self,*args):return
    def _json(self,obj,status=200):
        b=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def _body(self):
        vals=self.headers.get_all('Content-Length', [])
        if len(vals)!=1: raise ValueError('Content-Length must occur exactly once')
        raw=vals[0].strip()
        if not raw.isascii() or not raw.isdigit(): raise ValueError('invalid Content-Length')
        n=int(raw)
        if n<0 or n>2_000_000: raise ValueError('Content-Length out of bounds')
        if self.headers.get('Transfer-Encoding'): raise ValueError('Transfer-Encoding rejected')
        if self.headers.get_content_type()!='application/json': raise ValueError('JSON Content-Type required')
        if n == 0:
            self._request_deadline=time.monotonic()+5.0
            return {}
        chunks=[]; got=0; deadline=time.monotonic()+5.0; self._request_deadline=deadline
        # Read exactly the declared length; EOF before that is a malformed
        # request even when the prefix happens to be valid JSON.
        conn=getattr(self,'connection',None)
        if conn is not None: conn.settimeout(0.25)
        try:
            while got < n:
                remaining=deadline-time.monotonic()
                if remaining <= 0: raise ValueError('body total deadline exceeded')
                if conn is not None: conn.settimeout(min(.25,remaining))
                reader=getattr(self.rfile,'read1',None)
                b=reader(min(64*1024,n-got)) if callable(reader) else self.rfile.read(1)
                if not b: raise ValueError(f'body truncated: expected {n}, got {got}')
                got += len(b); chunks.append(b)
                if got > n: raise ValueError('body exceeds declared length')
                if time.monotonic() >= deadline: raise ValueError('body total deadline exceeded')
        except TimeoutError as e:
            if time.monotonic() >= deadline: raise ValueError('body total deadline exceeded') from e
            raise ValueError('body read timeout') from e
        if time.monotonic() >= deadline: raise ValueError('body total deadline exceeded')
        try: obj=json.loads(b''.join(chunks))
        except Exception as e: raise ValueError(f'请求 JSON 无效: {e}')
        if time.monotonic() >= deadline: raise ValueError('body total deadline exceeded')
        return obj
    def _raw_body(self,limit):
        vals=self.headers.get_all('Content-Length', [])
        if len(vals)!=1: raise ValueError('Content-Length must occur exactly once')
        raw=vals[0].strip()
        if not raw.isascii() or not raw.isdigit(): raise ValueError('invalid Content-Length')
        n=int(raw)
        if n<=0: raise ValueError('空文件')
        if n>limit: raise ValueError('图片太大（上限 12MB）')
        if self.headers.get('Transfer-Encoding'): raise ValueError('Transfer-Encoding rejected')
        chunks=[];got=0;deadline=time.monotonic()+60.0;self._request_deadline=deadline
        conn=getattr(self,'connection',None)
        if conn is not None: conn.settimeout(0.5)
        reader=getattr(self.rfile,'read1',None)
        try:
            while got<n:
                remaining=deadline-time.monotonic()
                if remaining<=0: raise ValueError('body total deadline exceeded')
                if conn is not None: conn.settimeout(min(.5,remaining))
                b=reader(min(256*1024,n-got)) if callable(reader) else self.rfile.read(min(65536,n-got))
                if not b: raise ValueError(f'body truncated: expected {n}, got {got}')
                got+=len(b);chunks.append(b)
        except TimeoutError as e:
            raise ValueError('body read timeout') from e
        return b''.join(chunks)
    def _guard(self, write=False):
        cfg=self.server.service.cfg
        host=(self.headers.get('Host') or '').strip()
        port=getattr(self.server,'server_port',cfg.port)
        allowed={f'{cfg.host}:{port}',f'{cfg.host}:{cfg.port}',cfg.host,'127.0.0.1','localhost','[::1]'}
        if host not in allowed: raise PermissionError('Host 不可信')
        if write:
            origin=self.headers.get('Origin')
            if origin is None or origin=='null': raise PermissionError('Origin 必须同源')
            if origin not in {f'http://{cfg.host}:{port}',f'http://{cfg.host}:{cfg.port}',f'http://{cfg.host}', 'http://127.0.0.1', 'http://localhost'}: raise PermissionError('跨站 Origin 被拒绝')
    def _error(self,e):
        if getattr(self,'_response_started',False):
            try:self.close_connection=True
            except Exception:pass
            return
        self.close_connection=True
        status=404 if isinstance(e,KeyError) else 400
        if isinstance(e,PermissionError):status=403
        if isinstance(e,FileNotFoundError):status=404
        self._json({'error':str(e),'type':type(e).__name__},status)
    def do_GET(self):
        try:
            self._guard(False)
            u=urlparse(self.path);q=parse_qs(u.query);svc=self.server.service
            if u.path=='/api/products':return self._json([svc.public_product(p) for p in svc.store.list_products(bool(q.get('archived')))])
            if u.path=='/api/jobs':return self._json([svc.public_job(j) for j in svc.queue.all(q.get('product_id',[None])[0])])
            if u.path.startswith('/api/products/') and u.path.endswith('/index'):
                return self._json(svc.index_state(u.path.split('/')[3]))
            if u.path=='/api/browse':
                if 'path' in q or any(x.split('=',1)[0]=='path' for x in u.query.split('&')):return self._json(svc.browser.browse_path(q.get('path',[''])[0],q.get('pick',['audio'])[0]))
                return self._json(svc.browser.browse(int(q.get('root_id',['-1'])[0]),q.get('token',[''])[0]))
            if u.path=='/api/roots':
                roots=[]
                for i,r in enumerate(svc.cfg.nas_roots):
                    rs=str(r).rstrip('/\\');tail=rs.replace('/','\\').split('\\')[-1] or rs
                    roots.append({'id':i,'name':tail,'path':str(r)})
                return self._json(roots)
            if u.path=='/api/ai-settings':return self._json(svc.ai_settings())
            if u.path.startswith('/api/retrieval/'):
                pid=u.path.split('/')[3];return self._json(svc.retrieve(pid,q.get('text',[''])[0]))
            if u.path.startswith('/api/reports/'):
                jid=u.path.split('/')[3];j=svc.queue.get(jid)
                if not q.get('product_id') or j['product_id']!=q['product_id'][0]:raise PermissionError('商品范围不匹配')
                if j['status']!='done' or not j.get('report'):raise PermissionError('任务未完成或报告不可用')
                return self._json(j['report'])
            if u.path.startswith('/api/thumbs/'):
                jid=u.path.rsplit('/',1)[-1];j=svc.queue.get(jid)
                if not q.get('product_id') or j['product_id']!=q['product_id'][0]:raise PermissionError('商品范围不匹配')
                if j['status']!='done' or not j.get('artifact'):raise KeyError(jid)
                p=svc.thumb_path(jid)
                if p is None:raise FileNotFoundError('暂无缩略图')
                b=Path(p).read_bytes();self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
            if u.path.startswith('/api/results/'):
                jid=u.path.rsplit('/',1)[-1];j=svc.queue.get(jid)
                if not q.get('product_id') or j['product_id']!=q['product_id'][0]:raise PermissionError('商品范围不匹配')
                if j['status']!='done' or not j.get('output') or not j.get('artifact'):raise PermissionError('任务未完成，禁止下载')
                p=Path(j['output']).absolute()
                if not p.is_file():raise PermissionError('成片已变化，下载已封锁')
                # Hash and stream through one read handle to reduce check→open
                # races; a later mutation is still a parent Windows/NAS gate.
                # Bind hashing and streaming to a bounded stable snapshot. A
                # later same-inode mutation cannot alter the bytes sent.
                with ExitStack() as stack:
                    snap=stack.enter_context(tempfile.SpooledTemporaryFile(max_size=8*1024*1024, mode='w+b'))
                    handle=stack.enter_context(p.open('rb'));h=hashlib.sha256();size=0
                    while True:
                        chunk=handle.read(1024*1024)
                        if not chunk:break
                        h.update(chunk)
                        wrote=snap.write(chunk)
                        if wrote != len(chunk): raise OSError('snapshot short write')
                        size+=len(chunk)
                    if h.hexdigest()!=j['artifact']['sha256'] or size!=j['artifact']['bytes']:
                        raise PermissionError('成片已变化，下载已封锁')
                    snap.flush(); snap.seek(0)
                    total=size;rh=self.headers.get('Range');status=200;start=0;end=total-1;n=total
                    if rh:
                        import re
                        m=re.fullmatch(r'bytes=(\d*)-(\d*)',rh.strip())
                        if not m: raise ValueError('invalid Range')
                        a,b=m.groups(); start=int(a) if a else max(0,total-int(b or 0)); end=int(b) if b else total-1
                        if start<0 or start> end or end>=total:
                            self._response_started=True;self.send_response(416);self.send_header('Content-Range',f'bytes */{total}');self.end_headers();return
                        status=206;n=end-start+1
                    self._response_started=True
                    self.send_response(status);self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(n));self.send_header('Content-Disposition',f'inline; filename="job-{jid}.mp4"')
                    if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
                    self.end_headers()
                    snap.seek(start);remaining=n
                    while remaining:
                        b=snap.read(min(1024*1024,remaining))
                        if not b:break
                        self.wfile.write(b);remaining-=len(b)
                return
            if u.path in ('/','/index.html','/app.js','/style.css'):
                root=Path(__file__).parent.parent/'static';name='index.html' if u.path=='/' else u.path[1:];p=(root/name).resolve();p.relative_to(root.resolve());b=p.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(p))[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
            self.send_error(404)
        except Exception as e:self._error(e)
    def do_POST(self):
        try:
            self._guard(True)
            u=urlparse(self.path)
            svc=self.server.service
            if u.path.startswith('/api/products/') and u.path.endswith('/cover-file'):
                parts=u.path.strip('/').split('/');pid=parts[2] if len(parts)>2 else ''
                return self._json(svc.save_cover_file(pid,self._raw_body(12*1024*1024),self.headers.get_content_type()))
            body=self._body()
            if time.monotonic() >= getattr(self,'_request_deadline',0): raise ValueError('body total deadline exceeded')
            if u.path=='/api/products':return self._json(svc.create_product(body.get('name','')),201)
            if u.path.startswith('/api/products/'):
                parts=u.path.strip('/').split('/');pid=parts[2] if len(parts)>2 else ''
                if parts[-1]=='copy':return self._json(svc.public_product(svc.store.set_copy(pid,body.get('text',''),body.get('variants',[]))))
                if parts[-1]=='settings':return self._json(svc.public_product(svc.store.set_settings(pid,body.get('settings',{}))))
                if parts[-1]=='bgm':
                    if 'path' in body:return self._json(svc.select_bgm_path(pid,body['path']))
                    return self._json(svc.select_bgm(pid,int(body['root_id']),body.get('token',''),body['name']))
                if parts[-1] in ('source','output'):
                    if 'path' in body:return self._json(svc.select_path(pid,parts[-1],body['path']))
                    return self._json(svc.select(pid,parts[-1],int(body['root_id']),body.get('token','')))
                if parts[-1]=='index':return self._json(svc.request_index(pid),202)
                if parts[-1]=='jobs':return self._json(svc.enqueue(pid,int(body.get('count',1)),body.get('text'),body.get('settings')),202)
                if parts[-1]=='cancel':return self._json(svc.cancel(pid,body['job_id']))
                if parts[-1]=='ai-copy':return self._json(svc.ai_copy(pid,body))
                if parts[-1]=='open-folder':return self._json(svc.open_folder(pid,body.get('job_id')))
            if u.path=='/api/retrieval':return self._json(svc.retrieve(body['product_id'],body.get('text','')))
            if u.path=='/api/ai-settings':return self._json(svc.set_ai_settings(body))
            self.send_error(404)
        except Exception as e:self._error(e)
def make_server(cfg,store,queue,service=None):
    s=ThreadingHTTPServer((cfg.host,cfg.port),WorkbenchHandler);s.store=store;s.queue=queue;s.service=service;return s
