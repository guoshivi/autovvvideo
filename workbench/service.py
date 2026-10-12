"""Single persistent work queue and product-scoped application operations."""
import threading,time,json,hashlib,urllib.request,subprocess,os,copy
from pathlib import Path
from contextlib import contextmanager
from .browser import FolderBrowser,AUDIO_EXT,VIDEO_EXT,overlaps,reject_links
from .settings import ProductionSettings
from .copybook import split_script
from .indexer import Indexer,ProductionEmbedder,collect_source_fingerprints,expected_files_unchanged
from .renderer import render_job,export_validated
from .selection import spread_candidates
from .aicopy import run as ai_run,AIError
from .ai_endpoint import normalize_endpoint
from .media import sha256
from .fs_authority import lexical_under, AuthorityError, capture_lease
from .process import run_owned,Cancelled
from .queue import CommitError as QueueCommitError

class ServiceError(ValueError):pass
def _render_dest_name(product,job_input,job_id):
    """Human-friendly export name: 产品_摘要_月日-时分秒_v版本（文件夹与文件同名）。"""
    import re as _re,time as _time
    def sane(s,maxlen):
        s=_re.sub(r'[\\/:*?"<>|\r\n\t]','',str(s or '')).strip().strip('.')
        return s[:maxlen]
    name=sane(product.get('name') or '视频',24) or '视频'
    line=(job_input.get('lines') or [''])[0]
    summary=sane(line,12)
    t=_time.strftime('%m%d-%H%M%S')
    v=int(job_input.get('variant') or 0)
    return '_'.join([name]+([summary] if summary else [])+[f'{t}_v{v}'])
@contextmanager
def gpu_guard(cfg):
    p=Path(cfg.gpu_lock_path)
    if not p.is_file() or p.stat().st_size!=1:raise ServiceError('GPU lock must preexist as exactly one byte')
    f=p.open('r+b')
    try:
        def _try_lock():
            if os.name=='nt':
                import msvcrt
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def _unlock():
            if os.name=='nt':
                import msvcrt
                f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_UN)
        # Non-blocking lock: a click's retrieve run (candidate picking) or a
        # concurrent instance (prod+test) can hold it briefly — the raw
        # Errno 13 surfaced as an unreadable job failure (observed: e2e job
        # died while the user's prod click held the lock).  Wait up to ~60s,
        # then fail with a human message.
        for _attempt in range(20):
            try:_try_lock();break
            except OSError:
                if _attempt==19:raise ServiceError('显卡正忙：另一条任务正在使用（挑镜头/出片/建索引），已等待约 60 秒，请稍后再试')
                time.sleep(3)
        try:yield
        finally:_unlock()
    finally:f.close()
def gpu_preflight(cfg):
    # GPU release (embedder close / empty_cache) can lag a couple of seconds
    # behind a just-finished task; retry briefly before declaring the GPU busy
    # so a click right after an index run doesn't fail spuriously.
    last=None
    for attempt in range(4):
        try:return _gpu_preflight_once(cfg)
        except ServiceError as e:
            last=e
            if attempt<3:time.sleep(3)
    raise last or ServiceError('显卡预检失败，请重试')
def _gpu_preflight_once(cfg):
    for port in cfg.comfy_ports:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f'http://127.0.0.1:{port}/queue',timeout=3) as r:q=json.load(r)
        except Exception as e:
            if port==cfg.comfy_ports[0]:raise ServiceError(f'GPU preflight queue {port} unavailable: {e}') from e
            continue
        if not isinstance(q,dict) or not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):raise ServiceError('GPU preflight malformed queue')
        if q['queue_running'] or q['queue_pending']:raise ServiceError('显卡正忙：ComfyUI 正在出图，等它跑完再点「开始出片」')
    r=subprocess.run(['nvidia-smi','--query-gpu=index,memory.used,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=30)
    if r.returncode or not r.stdout.strip():raise ServiceError('GPU preflight nvidia-smi unavailable')
    for line in r.stdout.strip().splitlines():
        _,mem,util=[int(x.strip()) for x in line.split(',')]
        if mem>1500 or util>5:raise ServiceError(f'显卡正忙，稍等几秒再试（别的程序正在用 GPU：显存 {mem}MB、占用 {util}%，可能是游戏、ComfyUI 或刚跑完的任务）')
class WorkbenchService:
    def __init__(self,cfg,store,queue,embedder_factory=None,start=True):
        self.cfg=cfg;self.store=store;self.queue=queue;self.browser=FolderBrowser(cfg.nas_roots);self.embedder_factory=embedder_factory or (lambda:ProductionEmbedder(cfg.model_dir));self.stop_flag=threading.Event();self.worker=None;self.gpu_mutex=threading.RLock()
        if start:self.start()
    def start(self):
        if self.worker:return
        self.worker=threading.Thread(target=self._loop,name='workbench-worker',daemon=False);self.worker.start()
    def close(self):
        self.stop_flag.set()
        if self.worker:self.worker.join(timeout=10)
    def create_product(self,name):
        return self.public_product(self.store.create_product(name))
    def public_product(self,p):
        p=copy.deepcopy(p)
        for role in ('source','output'):
            if p.get(role):
                p[role].pop('_lease',None)
        if p.get('settings'):p['settings'].pop('bgm_path',None)
        return p
    def public_job(self,j):
        out={k:copy.deepcopy(v) for k,v in j.items() if k not in ('input','output','report','input_sha256')}
        x=j.get('input') or {}
        if x.get('kind')=='render':
            lines=x.get('lines') or []
            first=str(lines[0]) if lines else ''
            out['script_summary']=(first[:18]+'…') if len(first)>18 else first
            out['lines_count']=len(lines)
            out['batch_count']=x.get('batch_count') or 1
        if j.get('status')=='done':
            rep=j.get('report') or {}
            tl=rep.get('timeline') or []
            if tl:
                try:out['duration']=float(tl[-1].get('end') or 0)
                except (TypeError,ValueError):pass
            art=j.get('artifact') or {}
            if isinstance(art,dict) and 'bytes' in art:out['bytes']=art.get('bytes')
            try:out['finished_at']=os.path.getmtime(j['output'])
            except Exception:out['finished_at']=j.get('created_at')
        return out
    def select(self,pid,role,root_id,token):
        p=self.store.get_product(pid)
        if p['archived']:raise ServiceError('archived product')
        path=self.browser.at(root_id,token)
        selection={'root_id':root_id,'token':token,'path':str(path),'_lease':capture_lease(path)}
        return self.public_product(self.store.select(pid,role,selection))
    def select_path(self,pid,role,path):
        p=self.store.get_product(pid)
        if p['archived']:raise ServiceError('archived product')
        q=self.browser.at_path(path)
        selection={'path':str(q),'_lease':capture_lease(q)}
        return self.public_product(self.store.select(pid,role,selection))

    @staticmethod
    def _mapping_digest(mapping):
        return hashlib.sha256(json.dumps(mapping,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
    def _source_fingerprints(self, source):
        return collect_source_fingerprints(source['path'],self.browser)
    def _source_meta_matches(self, source, expected):
        """Cheap stat walk (path/bytes/mtime_ns).  True when every expected file
        still carries identical metadata, in which case a full-library re-hash
        can be skipped under the same trust assumption as Indexer.is_obsolete."""
        if not expected:return False
        try: files=self.browser.files(Path(source['path'])) if self.browser else FolderBrowser([Path(source['path'])]).files(Path(source['path']))
        except Exception: return False
        meta={}
        for p in files:
            pp=Path(p).absolute()
            try:
                reject_links(pp);st=pp.stat()
            except OSError: return False
            meta[str(pp)]=(st.st_size,st.st_mtime_ns)
        for e in expected:
            m=meta.get(e.get('path'))
            if m is None or e.get('bytes') is None or e.get('mtime_ns') is None or m!=(e['bytes'],e['mtime_ns']): return False
        return True
    def _snapshot_fresh(self, snap, *, require_mapping=True):
        psrc=snap.get('source') or {}
        expected=snap.get('source_fingerprints')
        # Fast path: if every recorded (path,bytes,mtime_ns) still matches, skip
        # the full-library re-hash (was ~105s for 261 files, executed while the
        # queue/store locks were held) and trust the recorded fingerprints.
        actual=None
        if expected is not None and self._source_meta_matches(psrc,expected):
            pass
        else:
            try: actual=self._source_fingerprints(psrc)
            except Exception: return False
        # Contract v2 (task snapshot, 2026-10-11): every expected file must match
        # exactly; directory additions are tolerated and need a separate re-index.
        if expected is not None and actual is not None and not expected_files_unchanged(expected,actual):return False
        if require_mapping:
            mapping=snap.get('mapping')
            if mapping is not None and snap.get('mapping_sha256')!=self._mapping_digest(mapping):return False
            allowed={x.get('sha256') for x in (actual if actual is not None else expected or [])}
            for row in (mapping or []):
                for c in row:
                    if c.get('source_sha256') not in allowed:return False
        return True
    def select_bgm(self,pid,root_id,token,name):
        p=self.store.get_product(pid)
        if not p['source']:raise ServiceError('select source first')
        folder=self.browser.at(root_id,token)
        try: lexical_under(folder, Path(p['source']['path'])); folder.relative_to(Path(p['source']['path']))
        except ValueError: raise ServiceError('BGM must be inside selected source')
        from .browser import component
        f=folder/component(name);reject_links(f)
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXT:raise ServiceError('BGM must be an authorized audio file')
        return {'root_id':root_id,'token':token,'name':name,'path':str(f),'sha256':sha256(f)}
    def select_bgm_path(self,pid,path):
        p=self.store.get_product(pid)
        if p['archived']:raise ServiceError('archived product')
        raw=str(path or '').strip()
        if not raw:raise ServiceError('请选择 BGM 音频文件')
        f=Path(raw)
        if not f.is_absolute():raise ServiceError('需要完整路径')
        if '..' in f.parts:raise ServiceError('拒绝路径穿越')
        reject_links(f)
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXT:raise ServiceError('请选择音频文件（mp3/wav/m4a/aac/flac）')
        return {'name':f.name,'path':str(f),'sha256':sha256(f)}
    def _indexer(self,embedder):return Indexer(Path(self.cfg.data_root)/'indexes',embedder,self.browser,self.cfg.ffmpeg or 'ffmpeg',self.cfg.ffprobe or 'ffprobe')
    def request_index(self,pid):
        p=self.store.get_product(pid)
        if not p['source']:raise ServiceError('select source before index')
        fps=None
        if p.get('index_id'):
            prev=self.store.get_index(p['index_id'])
            if prev and prev.get('source')==p['source']['path']:
                prev_fps=[e['fingerprint'] for e in prev['files']]
                if self._source_meta_matches(p['source'],prev_fps):fps=prev_fps
        if fps is None:fps=self._source_fingerprints(p['source'])
        snap={'kind':'index','source':copy.deepcopy(p['source']),'source_fingerprints':fps,'selection_revision':p['selection_revision'],'model_identity':self.cfg.model_dir,'settings':{'sample_seconds':10}}
        def guard(candidate):
            cur=self.store.get_product(pid)
            if (cur.get('source') or {}).get('path')!=(snap['source'] or {}).get('path') or not self._snapshot_fresh(snap,require_mapping=False):
                raise ServiceError('index enqueue generation changed')
        with self.store.lock,self.queue.lock:
            return self.public_job(self.queue.enqueue(pid,snap,1,guard))
    def index_state(self,pid):
        p=self.store.get_product(pid);rec=self.store.get_index(p.get('index_id')) if p.get('index_id') else None
        obsolete=bool(rec and (rec['source']!=(p.get('source') or {}).get('path') or self._indexer(None).is_obsolete(rec)))
        return {'ready':bool(rec and not obsolete),'obsolete':obsolete,'index':{'id':rec['id'],'count':rec['count'],'created_at':rec['created_at'],'synthetic':rec['synthetic']} if rec else None,'tasks':[self.public_job(j) for j in self.queue.all(pid) if j['input'].get('kind')=='index']}
    def _ready_index(self,pid):
        p=self.store.get_product(pid);rec=self.store.get_index(p.get('index_id')) if p.get('index_id') else None
        if not rec or rec['product_id']!=pid or rec['source']!=(p.get('source') or {}).get('path') or self._indexer(None).is_obsolete(rec):raise ServiceError('索引不存在或素材已变化，请重新建索引')
        # Re-read under the store lock immediately before acceptance.
        with self.store.lock:
            p=self.store.get_product(pid); rec=self.store.get_index(p.get('index_id')) if p.get('index_id') else None
            if not rec or rec['product_id']!=pid or rec['source']!=(p.get('source') or {}).get('path') or self._indexer(None).is_obsolete(rec):raise ServiceError('索引不存在或素材已变化，请重新建索引')
            return p,rec
    def _retrieve_snapshot(self,p,rec,text):
        lines=split_script(text)
        if not lines:raise ServiceError('脚本为空')
        with self.gpu_mutex:
            if self.embedder_factory.__name__=='<lambda>' and self.cfg.model_dir:gpu_preflight(self.cfg)
            with gpu_guard(self.cfg) if self.cfg.model_dir else _null():
                if self.cfg.model_dir:gpu_preflight(self.cfg)
                emb=self.embedder_factory()
                try:rows=[self._indexer(emb).search(rec,line,k=min(8,sum(len(x['blocks']) for x in rec['files']))) for line in lines]
                finally:
                    if hasattr(emb,'close'):emb.close()
        rows=spread_candidates(rows)
        allowed={e['fingerprint']['sha256'] for e in rec['files']}
        out=[{'sentence_id':i,'line':line,'candidates':r} for i,(line,r) in enumerate(zip(lines,rows))]
        for row in out:
            for c in row['candidates']:
                if c.get('index_id')!=rec['id'] or c.get('source_sha256') not in allowed:
                    raise ServiceError('retrieval mapping generation mismatch')
        return out
    def retrieve(self,pid,text):
        p,rec=self._ready_index(pid)
        return self._retrieve_snapshot(p,rec,text)
    def enqueue(self,pid,count=1,text=None,settings=None):
        p,rec=self._ready_index(pid)
        if not p['output']:raise ServiceError('select output folder')
        if p['archived']:raise ServiceError('archived product')
        text=p['copy']['text'] if text is None else text;lines=split_script(text)
        if not lines:raise ServiceError('script empty')
        setting=ProductionSettings.from_dict(settings if settings is not None else p['settings']).snapshot()
        if setting['bgm']:
            bgm=Path(setting['bgm_path']);reject_links(bgm)
            if not bgm.is_file() or bgm.suffix.lower() not in AUDIO_EXT:raise ServiceError('BGM 文件不可用，请重新选择')
            bgm_sha=sha256(bgm)
        else:bgm_sha=None
        for key,label in (('intro','片头'),('outro','片尾')):
            if setting.get(key):setting[key]=self._bind_media(setting[key],label,need_kind=True)
        if setting.get('cover') and setting['cover'].get('mode')=='file':
            setting['cover']=self._bind_media(setting['cover'],'封面')
        preview=self._retrieve_snapshot(p,rec,text);mapping=[r['candidates'] for r in preview]
        for i,c in enumerate(mapping):
            if not c:raise ServiceError(f'no indexed candidate for sentence {i}')
        expected_fps=[e['fingerprint'] for e in rec['files']]
        src_fps=expected_fps if self._source_meta_matches(p['source'],expected_fps) else self._source_fingerprints(p['source'])
        snap={'kind':'render','product_id':pid,'index_id':rec['id'],'index_source':rec['source'],'source':copy.deepcopy(p['source']),'source_fingerprints':src_fps,'output':copy.deepcopy(p['output']),'selection_revision':p['selection_revision'],'lines':lines,'settings':setting,'mapping':mapping,'mapping_sha256':self._mapping_digest(mapping),'bgm_sha256':bgm_sha}
        def guard(candidate):
            cur=self.store.get_product(pid)
            if (cur.get('source') or {}).get('path')!=(snap['source'] or {}).get('path') or (cur.get('output') or {}).get('path')!=(snap['output'] or {}).get('path') or cur.get('index_id')!=snap['index_id'] or not self._snapshot_fresh(snap):
                raise ServiceError('enqueue generation changed')
        with self.store.lock,self.queue.lock:
            jobs=self.queue.enqueue(pid,snap,count,guard)
        return [self.public_job(j) for j in jobs] if isinstance(jobs,list) else [self.public_job(jobs)]
    def cancel(self,pid,jid):
        j=self.queue.get(jid)
        if j['product_id']!=pid:raise KeyError(jid)
        self.queue.cancel(jid);return self.public_job(self.queue.get(jid))
    _IMG_EXT={'.jpg','.jpeg','.png','.webp','.bmp'}
    _COVER_TYPES={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}
    def _bind_media(self,entry,label,need_kind=False):
        p=Path(str(entry.get('path') or ''));reject_links(p)
        if not p.is_file():raise ServiceError(f'{label}文件不存在，请重新选择')
        if need_kind:
            kind=entry.get('kind');suffix=p.suffix.lower()
            if kind=='image' and suffix not in self._IMG_EXT:raise ServiceError(f'{label}请选择图片（jpg / png / webp）')
            if kind=='video' and suffix not in VIDEO_EXT:raise ServiceError(f'{label}请选择视频文件')
        digest=sha256(p)
        if entry.get('sha256') and entry['sha256']!=digest:raise ServiceError(f'{label}文件已变化，请重新选择')
        out=dict(entry);out['path']=str(p);out['sha256']=digest
        return out
    def _ai_overrides(self):
        try:
            d=json.loads((Path(self.cfg.data_root)/'ai-config.json').read_text(encoding='utf-8'))
        except Exception:
            return {}
        return d if isinstance(d,dict) else {}
    def ai_settings(self):
        ov=self._ai_overrides()
        return {'base_url':ov.get('base_url') or self.cfg.ai_base_url,
                'model':ov.get('model') or self.cfg.ai_model,
                'has_key':bool(ov.get('api_key',self.cfg.ai_api_key))}
    def set_ai_settings(self,body):
        body=body or {}
        base=str(body.get('base_url') or '').strip()
        p=Path(self.cfg.data_root)/'ai-config.json'
        ov=self._ai_overrides()
        if base:ov['base_url']=base
        key=body.get('api_key')
        if isinstance(key,str) and key.strip():ov['api_key']=key.strip()
        if body.get('clear_key'):ov['api_key']=''
        model=str(body.get('model') or '').strip()
        if model:ov['model']=model
        normalize_endpoint(ov.get('base_url') or self.cfg.ai_base_url,bool(ov.get('api_key',self.cfg.ai_api_key)))
        p.parent.mkdir(parents=True,exist_ok=True)
        tmp=p.with_name(p.name+'.tmp');tmp.write_text(json.dumps(ov,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,p)
        return self.ai_settings()
    def ai_copy(self,pid,body):
        p=self.store.get_product(pid)
        if p['archived']:raise ServiceError('archived product')
        body=body or {}
        mode=str(body.get('mode') or 'generate')
        ov=self._ai_overrides()
        base=ov.get('base_url') or self.cfg.ai_base_url
        key=ov.get('api_key',self.cfg.ai_api_key)
        model=ov.get('model') or self.cfg.ai_model
        try:
            return {'text':ai_run(base,key,model,mode,str(body.get('text') or ''),body.get('brief') or {})}
        except AIError as e:
            raise ServiceError(str(e)) from e
    def save_cover_file(self,pid,raw,content_type):
        p=self.store.get_product(pid)
        if p['archived']:raise ServiceError('archived product')
        ct=str(content_type or '').lower()
        if ct not in self._COVER_TYPES:raise ServiceError('封面图请用 JPG / PNG / WebP 格式')
        if not raw or len(raw)>12*1024*1024:raise ServiceError('封面图需小于 12MB')
        head=raw[:16]
        ok=((ct=='image/jpeg' and head[:3]==b'\xff\xd8\xff') or (ct=='image/png' and head[:8]==b'\x89PNG\r\n\x1a\n') or (ct=='image/webp' and head[:4]==b'RIFF' and raw[8:12]==b'WEBP'))
        if not ok:raise ServiceError('封面图内容与格式不符，请重新选择')
        digest=hashlib.sha256(raw).hexdigest()
        folder=Path(self.cfg.data_root)/'covers';folder.mkdir(parents=True,exist_ok=True)
        dest=folder/(digest[:16]+self._COVER_TYPES[ct])
        if not dest.exists():
            tmp=folder/(digest[:16]+'.tmp');tmp.write_bytes(raw);os.replace(tmp,dest)
        return {'name':dest.name,'path':str(dest),'sha256':digest,'bytes':len(raw)}
    def thumb_path(self,jid):
        d=self.queue.root/jid;t=d/'thumb.jpg'
        try:
            if t.is_file() and t.stat().st_size>0:return t
        except OSError:pass
        job=self.queue.get(jid)
        if job.get('status')!='done' or not job.get('artifact'):return None
        for cand in (d/'cover.jpg',d/'final.mp4',Path(job.get('output') or '')):
            try:
                src=cand
                if not str(src) or not src.is_file():continue
                is_img=src.suffix.lower() in ('.jpg','.jpeg','.png','.webp')
                # NB: '-ss 0' before an image input makes this ffmpeg decode 0
                # frames (empty output, exit 0) — only seek for video sources.
                cmd=[self.cfg.ffmpeg or 'ffmpeg','-nostdin','-v','error']+([] if is_img else ['-ss','1'])+['-i',str(src),'-frames:v','1','-vf','scale=240:-2','-q:v','5','-y',str(t)]
                r=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
            except Exception:
                continue
            try:
                if r.returncode==0 and t.is_file() and t.stat().st_size>0:return t
            except OSError:continue
        return None
    def open_folder(self,pid,jid):
        j=self.queue.get(jid)
        if j['product_id']!=pid:raise KeyError(jid)
        if j.get('status')!='done' or not j.get('output'):raise ServiceError('任务还没完成')
        folder=str(Path(j['output']).parent)
        opened=False
        if os.name=='nt':
            try:
                subprocess.Popen(['explorer',folder]);opened=True
            except Exception:opened=False
        return {'opened':opened,'path':folder}
    def _loop(self):
        while not self.stop_flag.is_set():
            job=self.queue.next()
            if not job:self.stop_flag.wait(.2);continue
            jid=job['id'];cancel=lambda:self.stop_flag.is_set() or self.queue.get(jid).get('cancel_requested',False)
            try:
                if sha256(self.queue.root/jid/'input.json')!=job['input_sha256']:raise ServiceError('input.json SHA changed')
                if job['input']['kind']=='index':self._run_index(job,cancel)
                else:self._run_render(job,cancel)
            except (InterruptedError,Cancelled) as e:self.queue.update(jid,status='cancelled' if not self.stop_flag.is_set() else 'interrupted',stage='cancelled',error={'stage':'cancel','check':'requested','message':str(e)})
            except Exception as e:
                if getattr(e,'after_replace',False):
                    try:
                        receipt=getattr(e,'receipt',None) or {}
                        (self.queue.root/'reconciliation.json').write_text(json.dumps({'job_id':jid,'error':str(e),'after_effect':True,'receipt':receipt},ensure_ascii=False),encoding='utf8')
                    except OSError:pass
                    continue
                details=getattr(e,'details',None) or {'stage':job.get('stage','worker'),'check':type(e).__name__,'message':str(e)}
                self.queue.update(jid,status='failed',stage='failed',error=details)
    def _run_index(self,j,cancel):
        x=j['input'];p=self.store.get_product(j['product_id'])
        if (p.get('source') or {}).get('path')!=(x['source'] or {}).get('path') or not self._snapshot_fresh(x,require_mapping=False):raise ServiceError('index/source generation changed')
        with self.gpu_mutex:
            if self.cfg.model_dir:gpu_preflight(self.cfg)
            with gpu_guard(self.cfg) if self.cfg.model_dir else _null():
                if self.cfg.model_dir:gpu_preflight(self.cfg)
                emb=self.embedder_factory()
                try:
                    idx=self._indexer(emb)
                    baseline=None
                    cur=self.store.get_product(j['product_id'])
                    if cur.get('index_id'):
                        prev_rec=self.store.get_index(cur['index_id'])
                        if prev_rec and prev_rec.get('source')==x['source']['path']:baseline=prev_rec
                    def progress(n,total,reused=0):self.queue.update(j['id'],stage='embedding',progress=round(n/total*95),indexed=n,total=total,reused=reused)
                    rec=idx.build(j['product_id'],x['source']['path'],x['settings'],x['model_identity'],progress,cancel,baseline=baseline)
                finally:
                    if hasattr(emb,'close'):emb.close()
        if cancel():raise Cancelled('index/cancel')
        p2=self.store.get_product(j['product_id'])
        if (p2.get('source') or {}).get('path')!=(x['source'] or {}).get('path') or not self._snapshot_fresh({'source':x['source'],'source_fingerprints':[e['fingerprint'] for e in rec['files']]},require_mapping=False) or self._indexer(None).is_obsolete(rec): raise ServiceError('index/source changed during build')
        with self.store.lock,self.queue.lock:
            self.store.put_index(j['product_id'],rec,x['selection_revision'])
            def commit(candidate):
                current=self.store.get_product(j['product_id'])
                queued=candidate[j['id']]
                return ((current.get('source') or {}).get('path')==(x['source'] or {}).get('path') and current.get('index_id')==rec['id'] and self.store.get_index(rec['id'])==rec and queued.get('input_sha256')==j.get('input_sha256') and not self._indexer(None).is_obsolete(rec) and self._snapshot_fresh({'source':x['source'],'source_fingerprints':[e['fingerprint'] for e in rec['files']]},require_mapping=False))
            try:
                self.queue.update(j['id'],status='done',stage='indexed',progress=100,index_id=rec['id'],condition=commit)
            except Exception as e:
                raise QueueCommitError('index stored but queue terminal commit requires reconciliation',after_replace=True) from e
    def _run_render(self,j,cancel):
        x=j['input'];p=self.store.get_product(j['product_id'])
        rec=self.store.get_index(x['index_id'])
        if not rec or rec['source']!=x['index_source'] or self._indexer(None).is_obsolete(rec):raise ServiceError('render/index obsolete')
        if (p.get('source') or {}).get('path')!=(x['source'] or {}).get('path') or not self._snapshot_fresh(x):raise ServiceError('render/source snapshot changed')
        if (p.get('output') or {}).get('path')!=(x.get('output') or {}).get('path'):raise ServiceError('render/output generation changed')
        jid=j['id'];d=self.queue.root/jid
        if x['settings']['voice']:
            speed=float(x['settings'].get('speed') or 1.0)
            req=d/'voice-request.json';req.write_text(json.dumps({'lines':x['lines'],'speaker':x['settings']['speaker'],'tts_model':self.cfg.tts_model,'speed':speed,'ffmpeg':self.cfg.ffmpeg},ensure_ascii=False),encoding='utf8')
            with self.gpu_mutex:
                gpu_preflight(self.cfg)
                with gpu_guard(self.cfg):
                    gpu_preflight(self.cfg);self.queue.update(jid,stage='voice',progress=5)
                    run_owned([self.cfg.tts_python,'-m','workbench.tts_worker',str(req),str(d)],cwd=self.cfg.project_src,timeout=self.cfg.max_stage_seconds,cancel=cancel,log_dir=d)
            receipt=json.loads((d/'voice-result.json').read_text(encoding='utf8'))
            if receipt.get('success') is not True or len(receipt.get('voices',[]))!=len(x['lines']):raise ServiceError('voice receipt incomplete')
            if abs(speed-1.0)>1e-9 and receipt.get('speed')!=speed:raise ServiceError('voice receipt speed mismatch')
            x['voices']=receipt['voices']
        else:x['voices']=[]
        x['variant']=j['variant'];x['input_sha256']=j['input_sha256'];x['bgm_sha256']=x.get('bgm_sha256')
        self.queue.update(jid,stage='render',progress=20)
        def progress(n,total):self.queue.update(jid,stage='render',progress=20+round(50*n/total))
        final,report=render_job(x,self.cfg,d,cancel,progress)
        if cancel():raise Cancelled('render/cancel before export')
        self.queue.update(jid,stage='export',progress=85)
        lexical_under(x['output']['path'], x['output']['path'])
        cover_local=(d/'cover.jpg') if report.get('cover') else None
        dest,got=export_validated(final,x['output']['path'],jid,report,x['output'].get('_lease'),cover_local=cover_local,dest_name=_render_dest_name(p,x,jid))
        # Publication has happened. A later cancellation is too late, and the
        # ledger must preserve the committed fact after independent readback.
        # Final authority window: re-read the product and immutable job before
        # allowing a terminal done commit. A late selection/revision switch
        # cannot turn a published artifact into a false success.
        p_final=self.store.get_product(j['product_id'])
        if (p_final.get('source') or {}).get('path')!=(x.get('source') or {}).get('path') or (p_final.get('output') or {}).get('path')!=(x.get('output') or {}).get('path') or not self._snapshot_fresh(x):
            raise ServiceError('render/final generation changed after publication')
        if self.queue.get(jid).get('input_sha256')!=j.get('input_sha256'):
            raise ServiceError('render/input snapshot changed before commit')
        def commit(cand):
            p_now=self.store.get_product(j['product_id']);q_now=cand[jid]
            try: lease_ok=capture_lease(x['output']['path'])==x['output'].get('_lease')
            except Exception: lease_ok=False
            return (lease_ok and (p_now.get('source') or {}).get('path')==(x.get('source') or {}).get('path') and (p_now.get('output') or {}).get('path')==(x.get('output') or {}).get('path') and q_now.get('input_sha256')==j.get('input_sha256') and self._snapshot_fresh(x))
        with self.store.lock,self.queue.lock:
            try:
                self.queue.update(jid,status='done',stage='done',progress=100,output=str(dest),artifact=got,report=report,output_folder=(x['output'].get('token') or x['output']['path']),condition=commit)
            except Exception as e:
                raise QueueCommitError('publication completed but queue terminal commit requires reconciliation',after_replace=True) from e
class _null:
    def __enter__(self):return None
    def __exit__(self,*a):return False
