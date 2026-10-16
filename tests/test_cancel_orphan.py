from types import SimpleNamespace
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store

def test_dead_worker_finalizes_running_cancel(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)])
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False);j=q.enqueue('p',{'kind':'index'});q.next();svc.worker=SimpleNamespace(is_alive=lambda:False)
    out=svc.cancel('p',j['id']);assert out['status']=='cancelled' and out['error']['check']=='worker_stopped'

def test_live_worker_keeps_running_on_cancel(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)])
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False);j=q.enqueue('p',{'kind':'index'});q.next();svc.worker=SimpleNamespace(is_alive=lambda:True);svc._running_job_id=j['id']
    out=svc.cancel('p',j['id']);assert out['status']=='running' and out['cancel_requested'] is True

def test_finalize_cancel_running_ignores_non_running_without_save(tmp_path,monkeypatch):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)])
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False);j=q.enqueue('p',{'kind':'index'});q.cancel(j['id']);calls=[];original=q._save
    monkeypatch.setattr(q,'_save',lambda *a,**k:(calls.append(1),original(*a,**k))[1])
    assert q.finalize_cancel_running(j['id']) is False and calls==[]
