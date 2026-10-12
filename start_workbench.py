#!/usr/bin/env python3
import argparse,sys,signal,os,time
from pathlib import Path
from workbench.config import RuntimeConfig
from workbench.store import Store
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.server import make_server
class Singleton:
    def __init__(self,path):self.path=Path(path);self.f=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.f=self.path.open('a+b')
        size=self.path.stat().st_size
        if size==0:
            self.f.write(b'\0');self.f.flush()
        elif size!=1: raise RuntimeError('singleton lock must be exactly one byte')
        self.f.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('已有工作台实例持有 data root 锁')
        else:
            import fcntl
            try:fcntl.flock(self.f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise RuntimeError('已有工作台实例持有 data root 锁')
    def close(self):
        if self.f:
            if os.name!='nt':
                import fcntl;fcntl.flock(self.f,fcntl.LOCK_UN)
            self.f.close();self.f=None
def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--config',default='workbench.config.json');ap.add_argument('--test',action='store_true');a=ap.parse_args(argv)
    lock=store=queue=service=httpd=None
    try:
        cfg=RuntimeConfig(host='127.0.0.1',port=0,data_root='./workbench-data',nas_roots=['.']) if a.test else RuntimeConfig.load(a.config)
        if not a.test: cfg.production_validate()
        Path(cfg.data_root).mkdir(parents=True,exist_ok=True);lock=Singleton(Path(cfg.data_root)/'singleton.lock');lock.acquire()
        store=Store(Path(cfg.data_root)/'records.json');queue=JobQueue(Path(cfg.data_root)/'jobs.json');service=WorkbenchService(cfg,store,queue,start=False);httpd=make_server(cfg,store,queue,service);service.start()
        print(f'Workbench listening on http://{cfg.host}:{httpd.server_port}',flush=True)
        try:httpd.serve_forever()
        except KeyboardInterrupt:pass
        finally:
            if httpd:
                httpd.server_close()
            if service: service.close()
            if lock: lock.close()
        return 0
    except Exception as e:
        if httpd:
            try: httpd.server_close()
            except Exception: pass
        if service:
            try: service.close()
            except Exception: pass
        if lock:
            try: lock.close()
            except Exception: pass
        print(f'启动失败: {e}',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
