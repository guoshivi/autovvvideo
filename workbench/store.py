import json, os, threading, uuid, copy, time, tempfile
from pathlib import Path
from .fs_authority import commit_scope, assert_mutation_allowed, CommitEffectError
class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock(); self.data=self._load()
    def _load(self):
        if not self.path.exists(): return {'products':{},'indexes':{}}
        try:
            d=json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(d,dict) or not isinstance(d.get('products'),dict) or not isinstance(d.get('indexes'),dict): raise ValueError('invalid schema')
            return d
        except (OSError,ValueError) as e: raise ValueError(f'持久记录损坏: {self.path}: {e}') from e
    def _save(self,candidate=None,before_replace=None):
        value=copy.deepcopy(self.data if candidate is None else candidate)
        tmp=self.path.with_name(self.path.name+'.tmp-'+uuid.uuid4().hex)
        old_raw=None; published=False
        try:
            if self.path.exists(): old_raw=self.path.read_bytes()
            raw=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
            with tmp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            with commit_scope('store'):
                if before_replace is not None:
                    result=before_replace(value)
                    if result is False: raise ValueError('store final commit condition rejected')
                os.replace(tmp,self.path)
                published=True
                # The callback is deliberately repeated after the actual
                # syscall.  A source or selector can change in an injected
                # syscall window; restore the previous durable record then.
                if before_replace is not None:
                    result=before_replace(value)
                    if result is False: raise CommitEffectError('store replace occurred but final condition failed')
            try: observed=json.loads(self.path.read_text(encoding='utf8'))
            except Exception as e: raise CommitEffectError('store replace succeeded but readback failed') from e
            if observed!=value: raise CommitEffectError('store replace succeeded but readback mismatched')
            self.data=value
        except (CommitEffectError, CommitError) as e:
            # Best-effort recovery is explicit: the syscall did happen, so
            # callers record reconciliation even when the old bytes restore.
            try:
                if old_raw is None: self.path.unlink(missing_ok=True)
                else:
                    rb=self.path.with_name(self.path.name+'.reconcile-'+uuid.uuid4().hex)
                    rb.write_bytes(old_raw); os.replace(rb,self.path)
                self.data=self._load()
            except Exception: pass
            raise CommitError(str(e),after_replace=True) from e
        except BaseException:
            if published:
                try:
                    if old_raw is None: self.path.unlink(missing_ok=True)
                    else:
                        rb=self.path.with_name(self.path.name+'.reconcile-'+uuid.uuid4().hex);rb.write_bytes(old_raw);os.replace(rb,self.path)
                    self.data=self._load()
                except Exception: pass
                raise CommitError('store replace occurred but final condition failed',after_replace=True)
            try: tmp.unlink()
            except OSError: pass
            raise

    def _source_matches(self,rec,p):
        from .indexer import collect_source_fingerprints,expected_files_unchanged
        actual=collect_source_fingerprints(rec['source'])
        expected=[e.get('fingerprint',{}) for e in rec.get('files',[])]
        return expected_files_unchanged(expected,actual) and p.get('source',{}).get('path')==rec.get('source')
    def create_product(self,name):
        with self.lock:
            name=str(name).strip()
            if not name or len(name)>100: raise ValueError('商品名称需为 1..100 字')
            i=uuid.uuid4().hex; p={'id':i,'name':name,'archived':False,'created_at':time.time(),'settings':{},'copy':{'text':'','variants':[]},'source':None,'output':None,'selection_revision':0,'index_id':None}
            self.data['products'][i]=p; self._save(); return copy.deepcopy(p)
    def list_products(self,include_archived=False):
        with self.lock:return [copy.deepcopy(p) for p in self.data['products'].values() if include_archived or not p.get('archived')]
    def get_product(self,pid):
        with self.lock:
            if pid not in self.data['products']:raise KeyError(pid)
            return copy.deepcopy(self.data['products'][pid])
    def update_product(self,pid,**kw):
        assert_mutation_allowed()
        with self.lock:
            p=self.data['products'][pid]
            allowed={'name','archived','settings','copy','source','output','selection_revision','index_id'}
            if set(kw)-allowed: raise ValueError('forbidden product field')
            candidate=copy.deepcopy(self.data);candidate['products'][pid].update(copy.deepcopy(kw));self._save(candidate);return copy.deepcopy(candidate['products'][pid])
    def archive_product(self,pid):return self.update_product(pid,archived=True)
    def set_copy(self,pid,text,variants=None):
        from .copybook import split_script
        split_script(text)
        variants=list(variants or [])
        if len(variants)>20:raise ValueError('过多稿件版本')
        for v in variants:split_script(v)
        return self.update_product(pid,copy={'text':str(text),'variants':variants})
    def set_settings(self,pid,settings):
        from .settings import ProductionSettings
        normalized=ProductionSettings.from_dict(settings).snapshot()
        return self.update_product(pid,settings=normalized)
    def select(self,pid,role,selection):
        assert_mutation_allowed()
        if role not in ('source','output'):raise ValueError('invalid role')
        with self.lock:
            p=self.data['products'][pid]; other=p['output' if role=='source' else 'source']
            from .browser import overlaps
            if other and overlaps(selection['path'],other['path']):raise ValueError('素材与输出目录不得重叠')
            candidate=copy.deepcopy(self.data);cp=candidate['products'][pid];cp[role]=copy.deepcopy(selection);cp['selection_revision']+=1
            if role=='source' and (cp['source'].get('path') or '')!=((p.get('source') or {}).get('path') or ''):cp['index_id']=None
            self._save(candidate);return copy.deepcopy(cp)
    def put_index(self,pid,rec,expected_revision=None):
        assert_mutation_allowed()
        with self.lock:
            p=copy.deepcopy(self.data['products'][pid])
            if not p['source'] or p['source']['path']!=rec['source'] or rec['product_id']!=pid:raise ValueError('索引来源与商品不符')
            old=self.data['indexes'].get(rec['id'])
            if old is not None and old != rec: raise ValueError('已出版索引不可变异')
            if not self._source_matches(rec,p):raise ValueError('索引提交时素材集合或指纹已变化')
            candidate=copy.deepcopy(self.data);candidate['indexes'][rec['id']]=copy.deepcopy(rec);candidate['products'][pid]['index_id']=rec['id']
            def final(value):
                current=self.data['products'][pid]
                if current.get('source')!=p.get('source'):
                    raise ValueError('索引提交条件已变化')
                if not self._source_matches(rec,current):raise ValueError('索引提交时素材集合或指纹已变化')
            self._save(candidate,final)
            return copy.deepcopy(rec)
    def get_index(self,i):
        with self.lock:return copy.deepcopy(self.data['indexes'].get(i))

class CommitError(IOError):
    def __init__(self,message,after_replace=False):super().__init__(message);self.after_replace=after_replace
