from workbench.config import RuntimeConfig
from workbench.queue import JobQueue
from workbench.service import WorkbenchService
from workbench.store import Store
import pytest

def test_cleanup_records_dry_run_and_remove(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)])
    st=Store(tmp_path/'records.json');p=st.create_product('P');pid=p['id'];q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,st,q,start=False)
    done=q.enqueue(pid,{'kind':'index'});q.next();q.update(done['id'],status='failed',stage='failed',error={'message':'x'})
    d=q.root/done['id'];(d/'scratch.bin').write_bytes(b'1234')
    q2=q.enqueue(pid,{'kind':'index'});q.cancel(q2['id'])
    dry=svc.cleanup_records(pid,True);assert dry['counts']['failed']==1 and dry['counts']['cancelled']==1 and dry['bytes']>=4
    out=svc.cleanup_records(pid,False);assert set(out['removed'])=={done['id'],q2['id']} and not d.exists() and q.all(pid)==[]

def test_remove_terminal_saves_once(tmp_path,monkeypatch):
    q=JobQueue(tmp_path/'jobs.json');j=q.enqueue('p',{'kind':'index'});q.cancel(j['id']);calls=[];original=q._save
    monkeypatch.setattr(q,'_save',lambda *a,**k:(calls.append(1),original(*a,**k))[1])
    q.remove_terminal([j['id']]);assert calls==[1]

def test_remove_terminal_failure_keeps_directory_and_ledger(tmp_path,monkeypatch):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)]);st=Store(tmp_path/'records.json');p=st.create_product('P');q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,st,q,start=False);j=q.enqueue(p['id'],{'kind':'index'});q.cancel(j['id']);d=q.root/j['id'];(d/'keep').write_bytes(b'x')
    monkeypatch.setattr(q,'remove_terminal',lambda ids:(_ for _ in ()).throw(OSError('save failed')))
    with pytest.raises(OSError):svc.cleanup_records(p['id'],False)
    assert d.exists() and q.get(j['id'])['status']=='cancelled'

def test_other_product_directory_is_untouched(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)]);st=Store(tmp_path/'records.json');p=st.create_product('P');other=st.create_product('Q');q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,st,q,start=False);a=q.enqueue(p['id'],{'kind':'index'});b=q.enqueue(other['id'],{'kind':'index'});q.cancel(a['id']);q.cancel(b['id']);other_dir=q.root/b['id'];(other_dir/'keep').write_bytes(b'keep');svc.cleanup_records(p['id'],False);assert other_dir.exists() and (other_dir/'keep').exists()

def test_remove_job_dir_skips_link_file(tmp_path):
    cfg=RuntimeConfig(data_root=str(tmp_path/'data'),nas_roots=[str(tmp_path)]);st=Store(tmp_path/'records.json');p=st.create_product('P');q=JobQueue(tmp_path/'jobs.json');svc=WorkbenchService(cfg,st,q,start=False);j=q.enqueue(p['id'],{'kind':'index'});q.cancel(j['id']);d=q.root/j['id'];outside=tmp_path/'outside';outside.write_bytes(b'outside');link=d/'link.f32';
    try:link.symlink_to(outside)
    except (OSError,NotImplementedError):return
    (d/'normal.bin').write_bytes(b'normal');svc.cleanup_records(p['id'],False);assert outside.exists() and link.exists() and not (d/'normal.bin').exists()
