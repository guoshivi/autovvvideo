"""Fatal (non-Exception) crash in processing must mark the in-flight job failed (brief §4b).

Regression: the inner try's ``finally`` cleared ``_running_job_id`` before the outer
BaseException guard could read it, so a BaseException escape left the job ``running``
forever (zombie). Fixed by an inner ``except BaseException`` that marks the in-flight
job failed (best effort) and re-raises for the outer worker_error record.
"""
import time
from types import SimpleNamespace
import pytest
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store


class Boom(BaseException):
    pass


@pytest.fixture
def services():
    made = []
    def factory(cfg, st, q):
        svc = WorkbenchService(cfg, st, q, start=False); made.append(svc); return svc
    yield factory
    for svc in made:
        try: svc.close()
        except Exception: pass


def wait_for(fn, timeout=6.0):
    end = time.time()+timeout
    while time.time() < end:
        if fn(): return True
        time.sleep(0.01)
    return False


def test_fatal_exception_marks_inflight_job_failed(tmp_path, monkeypatch, services):
    cfg = RuntimeConfig(data_root=str(tmp_path/'data'), nas_roots=[str(tmp_path)], min_free_bytes=100*1024*1024)
    st = Store(tmp_path/'records.json'); q = JobQueue(tmp_path/'jobs.json'); svc = services(cfg, st, q)
    monkeypatch.setattr('workbench.service.shutil.disk_usage', lambda p: SimpleNamespace(free=10**12, total=10**12, used=0))
    first = q.enqueue('p', {'kind': 'index'})
    second = q.enqueue('p', {'kind': 'index'})
    def run(job, cancel):
        if job['id'] == first['id']: raise Boom('hard stop')
        q.update(job['id'], status='done', stage='done', progress=100, condition=lambda v: True)
    monkeypatch.setattr(svc, '_run_index', run)
    svc.start()
    assert wait_for(lambda: q.get(second['id'])['status'] == 'done'), 'worker did not continue with the next job'
    time.sleep(0.3)
    assert svc.worker.is_alive() and svc.worker_error['type'] == 'Boom'
    crashed = q.get(first['id'])
    assert crashed['status'] == 'failed' and crashed['stage'] == 'failed'
    assert crashed['error']['stage'] == 'worker' and crashed['error']['check'] == 'Boom'
    assert svc._running_job_id is None
