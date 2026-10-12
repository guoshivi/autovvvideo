import json, threading, uuid, time, copy, os, hashlib
from pathlib import Path
from .fs_authority import commit_scope, assert_mutation_allowed
class JobQueue:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.root=self.path.parent/'jobs';self.root.mkdir(exist_ok=True);self.lock=threading.RLock();self.jobs=self._load()
        for j in self.jobs.values():
            if j['status']=='running':j.update(status='interrupted',stage='restart',error={'stage':'restart','check':'interrupted','message':'服务重启，需人工复核；未自动重做'})
        self._save()
    def _load(self):
        if not self.path.exists():return {}
        try:
            d=json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(d,dict):raise ValueError('invalid jobs schema')
            return d
        except (OSError,ValueError) as e:raise ValueError(f'任务记录损坏: {e}') from e
    def _save(self,candidate=None,before_replace=None):
        value=copy.deepcopy(self.jobs if candidate is None else candidate)
        t=self.path.with_name(self.path.name+'.tmp-'+uuid.uuid4().hex)
        old_raw=None;published=False;input_before={}
        try:
            if self.path.exists(): old_raw=self.path.read_bytes()
            for jid in value:
                ip=self.root/jid/'input.json'
                if ip.exists(): input_before[str(ip)]=ip.read_bytes()
            raw=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf8')
            with t.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            with commit_scope('queue'):
                if before_replace is not None and before_replace(value) is False:
                    raise ValueError('queue final commit condition rejected')
                self._validate_input_files(value)
                os.replace(t,self.path);published=True
                if before_replace is not None and before_replace(value) is False:
                    raise IOError('queue replace occurred but final condition failed')
                self._validate_input_files(value)
            try: observed=json.loads(self.path.read_text(encoding='utf8'))
            except Exception as e:raise CommitError('queue replace succeeded but readback failed',after_replace=True) from e
            if observed!=value:raise CommitError('queue replace succeeded but readback mismatched',after_replace=True)
            self.jobs=value
        except CommitError:
            self._restore_input_files(input_before)
            raise
        except BaseException:
            if published:
                try:
                    if old_raw is None:self.path.unlink(missing_ok=True)
                    else:
                        rb=self.path.with_name(self.path.name+'.reconcile-'+uuid.uuid4().hex);rb.write_bytes(old_raw);os.replace(rb,self.path)
                    self.jobs=self._load()
                    self._restore_input_files(input_before)
                except Exception:pass
                raise CommitError('queue replace occurred but final condition failed',after_replace=True)
            try:t.unlink()
            except OSError:pass
            self._restore_input_files(input_before)
            raise

    def _validate_input_files(self, jobs):
        for jid,j in jobs.items():
            p=self.root/jid/'input.json'
            if not p.is_file(): raise ValueError('queue input snapshot missing')
            raw=p.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=j.get('input_sha256'): raise ValueError('queue input snapshot SHA changed')
            if json.loads(raw.decode('utf8'))!=j.get('input'): raise ValueError('queue input snapshot content changed')

    @staticmethod
    def _restore_input_files(rows):
        for name,raw in rows.items():
            try: Path(name).write_bytes(raw)
            except OSError: pass
    def enqueue(self,pid,input_snapshot,count=1,condition=None):
        assert_mutation_allowed()
        if type(count) is not int or not 1<=count<=500:raise ValueError('批量版数需为 1–500 的整数')
        created=[]
        with self.lock:
            out=[]
            candidate=copy.deepcopy(self.jobs)
            try:
                for variant in range(count):
                    i=uuid.uuid4().hex; snap=copy.deepcopy(input_snapshot);snap['variant']=variant;snap['batch_count']=count
                    d=self.root/i;d.mkdir(exist_ok=False);created.append((d,(d.stat().st_dev,d.stat().st_ino)));raw=json.dumps(snap,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf8');(d/'input.json').write_bytes(raw)
                    j={'id':i,'product_id':pid,'input_sha256':hashlib.sha256(raw).hexdigest(),'input':snap,'variant':variant,'status':'queued','stage':'queued','progress':0,'created_at':time.time(),'error':None,'revision':0}
                    candidate[i]=j;out.append(copy.deepcopy(j))
                self._save(candidate,condition)
                return out[0] if count==1 else out
            except BaseException:
                for d,identity in created:
                    try:
                        if d.exists() and (d.stat().st_dev,d.stat().st_ino)==identity:
                            import shutil;shutil.rmtree(d)
                    except OSError:pass
                raise
    def get(self,i):
        with self.lock:return copy.deepcopy(self.jobs[i])
    def all(self,pid=None):
        with self.lock:return [copy.deepcopy(x) for x in self.jobs.values() if pid is None or x['product_id']==pid]
    def update(self,i,condition=None,**kw):
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs[i]
            forbidden={'input','input_sha256','product_id','variant','id','revision'}
            if forbidden.intersection(kw): raise ValueError('immutable job fields')
            allowed={'status','stage','progress','indexed','total','reused','index_id','error','cancel_requested','output','artifact','report','output_folder'}
            unknown=set(kw)-allowed
            if unknown: raise ValueError(f'unknown mutable job fields: {sorted(unknown)}')
            if kw.get('status')=='done' and condition is None:raise ValueError('done requires trusted commit condition')
            if j['status'] in ('done','failed','cancelled','interrupted') and kw.get('status',j['status'])!=j['status']:raise ValueError('terminal job state')
            candidate=copy.deepcopy(self.jobs);candidate[i].update(copy.deepcopy(kw));candidate[i]['revision']+=1
            self._save(candidate,condition if kw.get('status')=='done' else None);return copy.deepcopy(candidate[i])
    def cancel(self,i):
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs.get(i)
            if not j or j['status'] in ('done','failed','cancelled','interrupted'):return False
            j['cancel_requested']=True
            if j['status']=='queued':j.update(status='cancelled',stage='cancelled')
            candidate=copy.deepcopy(self.jobs);candidate[i]['cancel_requested']=True
            if candidate[i]['status']=='queued':candidate[i].update(status='cancelled',stage='cancelled')
            self._save(candidate);return True
    def next(self):
        assert_mutation_allowed()
        with self.lock:
            for j in self.jobs.values():
                if j['status']=='queued':
                    candidate=copy.deepcopy(self.jobs);candidate[j['id']].update(status='running',stage='starting');self._save(candidate);return copy.deepcopy(candidate[j['id']])

class CommitError(IOError):
    def __init__(self,message,after_replace=False):super().__init__(message);self.after_replace=after_replace
