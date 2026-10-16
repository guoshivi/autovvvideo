import time
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store

def test_worker_survives_processing_error(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)],min_free_bytes=100*1024*1024)
    q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,Store(tmp_path/'records.json'),q,start=False)
    j=q.enqueue('p',{'kind':'index'})
    svc._run_index=lambda job,cancel: (_ for _ in ()).throw(RuntimeError('boom'))
    svc.start()
    for _ in range(50):
        if q.get(j['id'])['status']=='failed':break
        time.sleep(.01)
    assert svc.worker.is_alive() and svc.worker_error['message']=='boom';svc.close()
