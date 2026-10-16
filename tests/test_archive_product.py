import pytest
from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService,ServiceError
from workbench.store import Store

def make(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)])
    st=Store(tmp_path/'records.json');q=JobQueue(tmp_path/'jobs.json');return WorkbenchService(cfg,st,q,start=False),st,q

def test_archive_rejects_active_and_hides_after_done(tmp_path):
    svc,st,q=make(tmp_path);p=st.create_product('A');j=q.enqueue(p['id'],{'kind':'index'})
    with pytest.raises(ServiceError):svc.archive_product(p['id'])
    q.cancel(j['id']);svc.archive_product(p['id'])
    assert st.list_products()==[] and st.list_products(True)[0]['archived'] is True
