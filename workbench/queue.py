import json, threading, uuid, time, copy, os, hashlib
from pathlib import Path
from .selection import diversity_rank, recent_openings, prefix_plan
from .fs_authority import commit_scope, assert_mutation_allowed, replace_with_retry
class JobQueue:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.root=self.path.parent/'jobs';self.root.mkdir(exist_ok=True);self.lock=threading.RLock();self.jobs=self._load()
        for j in self.jobs.values():
            # A job that was running when the service died cannot be trusted to be
            # half-done, so it needs human review.  A *paused* job is deliberate:
            # its worker is gone but nothing was in flight, so it stays paused and
            # the queue's 继续 button hands it back to the worker.
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
                # Re-checked immediately before every actual replace attempt,
                # including retries after a Windows sharing violation: a final
                # condition that drifted to False must not be published behind.
                replace_with_retry(t,self.path,guard=(None if before_replace is None else (lambda:(before_replace(value) is not False))))
                published=True
                if before_replace is not None and before_replace(value) is False:
                    raise IOError('queue replace occurred but final condition failed')
                self._validate_input_files(value)
            try: observed=json.loads(self.path.read_text(encoding='utf8'))
            except Exception as e:raise CommitError('queue replace succeeded but readback failed',after_replace=True) from e
            if observed!=value:raise CommitError('queue replace succeeded but readback mismatched',after_replace=True)
            self.jobs=value
        except CommitError as e:
            self._restore_input_files(input_before)
            if getattr(e,'after_replace',False):
                # The published file is the only truth now.  Never leave the
                # in-memory queue one revision behind disk: the UI would show a
                # finished/exported job as running until the next restart.
                try:self.jobs=self._load()
                except Exception:pass
            raise
        except BaseException:
            if published:
                try:
                    if old_raw is None:self.path.unlink(missing_ok=True)
                    else:
                        rb=self.path.with_name(self.path.name+'.reconcile-'+uuid.uuid4().hex);rb.write_bytes(old_raw);replace_with_retry(rb,self.path)
                except Exception:pass
                # Refresh from disk even when the rollback restore failed.
                try:self.jobs=self._load()
                except Exception:pass
                self._restore_input_files(input_before)
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
            # A monotonic per-product rank is separate from the legacy
            # ``variant`` field.  Multi-script mode intentionally enqueues
            # count=1/variant=0; this rank still gives each batch a different
            # deterministic opening allocation and survives service restarts.
            prior=[]
            for old in self.jobs.values():
                if old.get('product_id')!=pid: continue
                inp=old.get('input') or {}
                if inp.get('kind')=='render':
                    try: prior.append(int(inp.get('selection_rank', old.get('selection_rank', 0))))
                    except (TypeError,ValueError): pass
            rank_base=(max(prior)+1) if prior else 0
            prior_context=[]
            for old in self.jobs.values():
                if old.get('product_id')==pid:
                    ctx=(old.get('input') or {}).get('selection_context')
                    if isinstance(ctx,dict):prior_context.append((ctx.get('allocation_seq'),ctx.get('mapping_sha256')))
            history_sha=hashlib.sha256(json.dumps(prior_context[-64:],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            try:
                for variant in range(count):
                    i=uuid.uuid4().hex; snap=copy.deepcopy(input_snapshot);snap['variant']=variant;snap['batch_count']=count
                    if snap.get('kind')=='render':
                        seq=rank_base+variant;snap['selection_rank']=seq
                        prior_render=[old for old in candidate.values() if old.get('product_id')==pid and (old.get('input') or {}).get('kind')=='render']
                        snap['mapping']=diversity_rank(snap.get('mapping') or [],seq,recent_openings(prior_render))
                        snap['mapping_sha256']=hashlib.sha256(json.dumps(snap['mapping'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
                        mapping_sha=str(snap.get('mapping_sha256') or '')
                        seed=hashlib.sha256(f'six-diversity-v1|{pid}|{seq}|{mapping_sha}|{history_sha}'.encode()).hexdigest()
                        reserved=prefix_plan(snap.get('mapping') or [],snap.get('lines') or [],snap.get('settings') or {})
                        snap['allocation_seq']=seq
                        snap['selection_context']={'schema':'six-diversity-v1','allocation_seq':seq,'batch_id':hashlib.sha256(f'{pid}|{rank_base}|{count}|{mapping_sha}'.encode()).hexdigest()[:16],'prefix_seconds':15,'mapping_sha256':mapping_sha,'history_sha256':history_sha,'seed':seed,'reserved_prefix':reserved,'purpose':'cross-batch opening reservation'}
                    d=self.root/i;d.mkdir(exist_ok=False);created.append((d,(d.stat().st_dev,d.stat().st_ino)));raw=json.dumps(snap,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf8');(d/'input.json').write_bytes(raw)
                    j={'id':i,'product_id':pid,'input_sha256':hashlib.sha256(raw).hexdigest(),'input':snap,'variant':variant,'selection_rank':snap.get('selection_rank',variant),'status':'queued','stage':'queued','progress':0,'created_at':time.time(),'error':None,'revision':0}
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
            allowed={'status','stage','progress','indexed','total','reused','skipped','skipped_files','index_id','error','cancel_requested','pause_requested','output','artifact','report','output_folder','cleanup'}
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
            candidate=copy.deepcopy(self.jobs);c=candidate[i]
            c['cancel_requested']=True
            # a job that never started (or is parked paused) is cancelled
            # outright; a live one stops at its next pause/cancel checkpoint and
            # the worker then reports the terminal state.
            if c['status'] in ('queued','paused'):c.update(status='cancelled',stage='cancelled')
            self._save(candidate);return True
    def cancel_all_queued(self,pid=None):
        assert_mutation_allowed()
        with self.lock:
            candidate=copy.deepcopy(self.jobs);n=0
            for j in candidate.values():
                if j.get('status')=='queued' and (pid is None or j.get('product_id')==pid):
                    j.update(status='cancelled',stage='cancelled',cancel_requested=True);n+=1
            if n:self._save(candidate)
            return n
    def finalize_cancel_running(self,i):
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs.get(i)
            if not j or j.get('status')!='running' or not j.get('cancel_requested'):return False
            candidate=copy.deepcopy(self.jobs);candidate[i].update(status='cancelled',stage='cancelled',error={'stage':'cancel','check':'worker_stopped','message':'出片线程已停止，任务已直接取消'})
            self._save(candidate);return True
    def pause(self,i):
        """Ask a job to suspend at its next checkpoint.

        `pause_requested` is the request.  For a job that has not started yet
        there is no worker in flight, so `status='paused'` takes effect
        immediately and `next()` will not pick it up.  A *running* job keeps
        `running` until its worker acknowledges at a file/step boundary
        (`pause_ack`) -- claiming 'paused' while work is still in flight was the
        dishonest part.
        """
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs.get(i)
            if not j or j['status'] in ('done','failed','cancelled','interrupted'):return False
            candidate=copy.deepcopy(self.jobs);c=candidate[i]
            c['pause_requested']=True
            if c['status']=='queued':c.update(status='paused',stage='paused')
            self._save(candidate);return True
    def pause_ack(self,i):
        """Worker-side confirmation that it is parked at a boundary.

        Only then does a running job become `paused`; the GPU lock/model can be
        released at the same point.  Returns False when the job already moved on
        (cancelled while the request was in flight).
        """
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs.get(i)
            if not j or j['status']!='running' or not j.get('pause_requested'):return False
            candidate=copy.deepcopy(self.jobs);candidate[i].update(status='paused',stage='paused')
            self._save(candidate);return True
    def resume(self,i,live=False):
        """Clear a pause.

        live=True -- a worker is still inside this job and resumes in place, so
        the status goes back to running and the worker restores its own stage.
        live=False -- the worker is gone (the app restarted while paused), so the
        job is handed back to the queue and picked up from the top."""
        assert_mutation_allowed()
        with self.lock:
            j=self.jobs.get(i)
            if not j or j['status']!='paused':return False
            candidate=copy.deepcopy(self.jobs);c=candidate[i]
            c['pause_requested']=False
            c.update(status='running' if live else 'queued',stage='resuming' if live else 'queued')
            self._save(candidate);return True
    def next(self):
        assert_mutation_allowed()
        with self.lock:
            for j in self.jobs.values():
                if j['status']=='queued':
                    candidate=copy.deepcopy(self.jobs);candidate[j['id']].update(status='running',stage='starting');self._save(candidate);return copy.deepcopy(candidate[j['id']])
    def remove_terminal(self,ids,condition=None):
        assert_mutation_allowed()
        ids=list(ids)
        with self.lock:
            for jid in ids:
                if jid not in self.jobs:raise ValueError(f'unknown job: {jid}')
                if self.jobs[jid].get('status') not in ('done','failed','cancelled','interrupted'):
                    raise ValueError(f'job is not terminal: {jid}')
            candidate=copy.deepcopy(self.jobs);removed={jid:candidate[jid]['status'] for jid in ids}
            for jid in ids:del candidate[jid]
            def guard(value):
                for jid,status in removed.items():
                    cur=self.jobs.get(jid)
                    if not cur or cur.get('status')!=status:return False
                return condition(value) if condition is not None else True
            self._save(candidate,guard)
            return removed

class CommitError(IOError):
    def __init__(self,message,after_replace=False):super().__init__(message);self.after_replace=after_replace
