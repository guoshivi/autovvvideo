from types import SimpleNamespace
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store
import time

def test_worker_status_exposes_disk_threshold(tmp_path,monkeypatch):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)],min_free_bytes=100*1024*1024)
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False)
    monkeypatch.setattr('workbench.service.shutil.disk_usage',lambda p:SimpleNamespace(free=123,total=1000,used=877))
    s=svc.worker_status();assert s['alive'] is False and s['disk']['free_bytes']==123 and s['disk']['ok'] is False

def test_loop_pauses_on_low_disk_then_resumes(tmp_path,monkeypatch):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)],min_free_bytes=1000)
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False);j=q.enqueue('p',{'kind':'index'})
    current={'free':1};monkeypatch.setattr('workbench.service.shutil.disk_usage',lambda p:SimpleNamespace(free=current['free'],total=10000,used=9999-current['free']))
    original_wait=svc.stop_flag.wait;monkeypatch.setattr(svc.stop_flag,'wait',lambda seconds: original_wait(min(seconds,.01)))
    seen=[]
    def finish(job,cancel):
        seen.append(job['id']);q.update(job['id'],status='done',stage='done',progress=100,condition=lambda value:True)
    svc._run_index=finish;svc.start()
    time.sleep(.05);assert q.get(j['id'])['status']=='queued'
    current['free']=5000
    for _ in range(100):
        if q.get(j['id'])['status']=='done':break
        time.sleep(.01)
    svc.close();assert seen==[j['id']] and q.get(j['id'])['status']=='done'
