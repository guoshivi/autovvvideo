from pathlib import Path
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store

def test_bulk_cancel_is_one_save_and_scoped(tmp_path,monkeypatch):
    q=JobQueue(tmp_path/'jobs.json'); rows=[q.enqueue('p',{'kind':'index'}) for _ in range(3)]; q.enqueue('other',{'kind':'index'}); q.next()
    calls=[]; original=q._save
    monkeypatch.setattr(q,'_save',lambda *a,**k:(calls.append(1),original(*a,**k))[1])
    assert q.cancel_all_queued('p')==2 and len(calls)==1
    assert [j['status'] for j in q.all('p')]==['running','cancelled','cancelled']
    assert q.cancel_all_queued('p')==0 and len(calls)==1

def test_remove_terminal_rejects_active(tmp_path):
    q=JobQueue(tmp_path/'jobs.json');j=q.enqueue('p',{'kind':'index'})
    try:q.remove_terminal([j['id']])
    except ValueError:pass
    else:raise AssertionError('active job must be rejected')
