"""Single persistent work queue and product-scoped application operations."""
import threading,time,json,hashlib,urllib.request,subprocess,os,copy,shutil,io,dataclasses
from pathlib import Path
from contextlib import contextmanager
from .browser import FolderBrowser,AUDIO_EXT,VIDEO_EXT,overlaps,reject_links
from .settings import ProductionSettings
from .copybook import split_script
from .copy_tools import clean_punctuation, parse_script_variants
from .timing import estimate_timing
from .indexer import Indexer,ProductionEmbedder,collect_source_fingerprints,expected_files_unchanged
from .renderer import render_job,export_numbered
from .selection import spread_candidates
from .aicopy import run as ai_run,AIError
from .ai_endpoint import normalize_endpoint
from .media import sha256
from .fs_authority import lexical_under, AuthorityError, capture_lease
from .process import run_owned,Cancelled
from .queue import CommitError as QueueCommitError
from .cleanup import cleanup_job_dir,remove_job_dir,iter_tree
from .voice_engines import (VoiceEngineError, VOX_PRESETS, require_vox_config, require_qwen_config,
                            list_clone_voices, resolve_clone_voice, validate_preview_payload, opaque_preview_id)

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
def _short_title(product,job_input):
    import re
    from .renderer import _safe_segment
    name=str(product.get('name') or '').strip()
    if name:return _safe_segment(name)
    line=str((job_input.get('lines') or [''])[0] or '').strip()
    first=re.split(r'[，。！？!?,;；：:\s]',line,maxsplit=1)[0]
    return _safe_segment(first or line)
_SKIP_CATEGORIES=(
    ('index/probe','unreadable','无法解析（文件损坏或不完整）'),
    ('index/window','slice_failed','切片失败（该片段的画面无法读取）'),
)
def public_skipped(rows):
    """Public (API/UI-safe) view of the files a partial index had to skip.

    The indexer's own records carry the full private path and the raw ffmpeg
    diagnostics; neither may cross the product API boundary.  The public form
    keeps only the basename and a short reason category, so the UI can say
    "成功 X / 跳过 Y（原因）" without leaking local paths."""
    out=[]
    for r in rows or []:
        if not isinstance(r,dict):continue
        raw=str(r.get('reason') or '');code,label='unknown','读取失败'
        for prefix,cat,cn in _SKIP_CATEGORIES:
            if raw.startswith(prefix):code,label=cat,cn;break
        out.append({'name':str(r.get('name') or ''),'reason':code,'reason_cn':label})
    return out
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
# A GPU only counts as busy when the existing conservative thresholds are met:
# too much VRAM already in use, or utilization beyond idle.  The thresholds are
# deliberately unchanged; what changed is that an nvidia-smi output that cannot
# be parsed is now REJECTED (fail-closed) instead of being read as "idle".
_GPU_BUSY_MEM_MB=1500      # used VRAM above which a job must wait
_GPU_BUSY_UTIL=5           # utilization percent above which a job must wait
def _parse_smi_rows(text):
    """Parse `nvidia-smi --query-gpu=index,memory.used,utilization.gpu
    --format=csv,noheader,nounits` into [(index,used_mb,util_pct), ...].

    Fail-closed: a non-empty line that is not a well-formed row makes the whole
    output untrusted, so None is returned and the caller must refuse the run.
    Never return an empty list for non-empty garbage -- that used to read as
    'GPU idle' and let a job through."""
    rows=[]
    for line in (text or '').strip().splitlines():
        line=line.strip()
        if not line:continue
        f=[x.strip() for x in line.split(',')]
        if len(f)!=3:return None
        try:rows.append((int(f[0]),int(f[1]),int(f[2])))
        except ValueError:return None
    return rows or None
def _gpu_busy_reason(rows):
    """Return a human-readable reason when any GPU is genuinely busy, else None."""
    for _,mem,util in rows:
        if mem>_GPU_BUSY_MEM_MB or util>_GPU_BUSY_UTIL:
            return (f'显卡正忙，稍等几秒再试（别的程序正在用 GPU：显存 {mem}MB、占用 {util}%，'
                    f'可能是游戏、ComfyUI 或刚跑完的任务）')
    return None
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
    rows=_parse_smi_rows(r.stdout)
    # Fail-closed: unparseable nvidia-smi output must refuse the run, never be
    # read as "the GPU is idle".
    if rows is None:raise ServiceError('GPU preflight nvidia-smi malformed')
    reason=_gpu_busy_reason(rows)
    if reason:raise ServiceError(reason)
class WorkbenchService:
    def __init__(self,cfg,store,queue,embedder_factory=None,start=True):
        self.cfg=cfg;self.store=store;self.queue=queue;self.browser=FolderBrowser(cfg.nas_roots);self.embedder_factory=embedder_factory or (lambda:ProductionEmbedder(cfg.model_dir));self.stop_flag=threading.Event();self.worker=None;self.gpu_mutex=threading.RLock();self._running_job_id=None;self.worker_error=None;self.disk_low=False;self.disk_free_bytes=None
        self._timing_mapping_cache={};self._timing_cache_lock=threading.RLock()
        self.preview_root=Path(cfg.data_root)/'voice-previews';self.preview_root.mkdir(parents=True,exist_ok=True);reject_links(self.preview_root);self._previews={};self._preview_lock=threading.RLock();self._preview_generation_lock=threading.Lock()
        if start:self.start()
    def start(self):
        if self.worker:return
        self.worker=threading.Thread(target=self._loop,name='workbench-worker',daemon=False);self.worker.start()
    def close(self):
        self.stop_flag.set()
        if self.worker:self.worker.join(timeout=10)
    def worker_status(self):
        try:free=shutil.disk_usage(self.queue.root).free
        except OSError:free=self.disk_free_bytes
        threshold=getattr(self.cfg,'min_free_bytes',1610612736)
        if free is not None:self.disk_free_bytes=free
        return {'alive':bool(self.worker and self.worker.is_alive()),'running_job':self._running_job_id,'last_error':copy.deepcopy(self.worker_error),'disk':{'free_bytes':free,'threshold_bytes':threshold,'ok':free is None or free>=threshold}}
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
        return {'ready':bool(rec and not obsolete),'obsolete':obsolete,'index':{'id':rec['id'],'count':rec['count'],'created_at':rec['created_at'],'synthetic':rec['synthetic'],'skipped':public_skipped(rec.get('skipped'))} if rec else None,'tasks':[self.public_job(j) for j in self.queue.all(pid) if j['input'].get('kind')=='index']}
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
    @staticmethod
    def _timing_text_key(pid,text):
        return (str(pid), ' '.join(str(text or '').split()))
    def _free_timing_mapping(self,pid,text):
        key=self._timing_text_key(pid,text)
        with self._timing_cache_lock:
            cached=self._timing_mapping_cache.get(key)
        if cached is not None:return copy.deepcopy(cached)
        for job in reversed(self.queue.all(pid)):
            inp=job.get('input') or {}
            if inp.get('kind')!='render':continue
            if self._timing_text_key(pid,'\n'.join(map(str,inp.get('lines') or [])))==key and inp.get('mapping') is not None:
                return copy.deepcopy(inp['mapping'])
        return None
    def timing(self,pid,text,settings=None,*,retrieve=False):
        """Return a measured-window duration plan without invoking TTS/AI."""
        p,rec=self._ready_index(pid)
        lines=split_script(text if text is not None else p['copy']['text'])
        setting=ProductionSettings.from_dict({**(p.get('settings') or {}),**(settings or {})}).snapshot()
        query=text if text is not None else p['copy']['text']
        mapping=self._free_timing_mapping(pid,query) if lines else []
        retrieval_warning=''
        if retrieve and lines:
            try:
                mapping=[row['candidates'] for row in self._retrieve_snapshot(p,rec,query)]
                with self._timing_cache_lock:self._timing_mapping_cache[self._timing_text_key(pid,query)]=copy.deepcopy(mapping)
            except Exception:
                mapping=None;retrieval_warning='检索不可用，按索引总容量估计。'
        receipts=[]
        for job in self.queue.all(pid):
            inp=job.get('input') or {}
            if job.get('status')!='done' or inp.get('kind')!='render':continue
            if (inp.get('settings') or {}).get('voice_engine','qwen')!=setting.get('voice_engine','qwen'):continue
            if (inp.get('settings') or {}).get('speaker','Serena')!=setting.get('speaker','Serena'):continue
            if (inp.get('settings') or {}).get('vox_voice','natural_female')!=setting.get('vox_voice','natural_female'):continue
            proof=(job.get('report') or {}).get('voice_calibration') or {}
            worker=Path(self.cfg.project_src)/'workbench'/('voxcpm_worker.py' if setting.get('voice_engine')=='vox' else 'tts_worker.py')
            if not worker.is_file() or proof.get('worker_sha256')!=sha256(worker) or proof.get('input_sha256')!=job.get('input_sha256') or proof.get('engine')!=setting.get('voice_engine','qwen'):continue
            if proof.get('voice')!=(setting.get('vox_voice') if proof['engine']=='vox' else setting.get('speaker')):continue
            prior_speed=(inp.get('settings') or {}).get('speed')
            if type(prior_speed) not in (int,float) or prior_speed<=0 or proof.get('speed')!=prior_speed:continue
            for line,row in zip(inp.get('lines') or [],proof.get('voices') or []):
                wav=self.queue.root/job['id']/str(row.get('file') or '')
                if wav.name!=row.get('file') or not wav.is_file() or sha256(wav)!=row.get('sha256'):continue
                from .verify import read_wav
                samples=len(read_wav(wav))
                if type(row.get('samples')) is int and samples==row['samples']:
                    receipts.append({'samples':samples,'characters':len(str(line)),'speed':prior_speed})
        est=estimate_timing(lines,rec,speed=setting.get('speed',1.0),receipts=receipts,mapping=mapping,settings=setting)
        if mapping is None:
            est=dataclasses.replace(est,warning=(retrieval_warning or '未按当前文案匹配，显示索引总容量。'))
        return {'lines':len(lines),'estimate':est.as_dict(),'speed':setting.get('speed',1.0),'voice_engine':setting.get('voice_engine','qwen'),'indexed':bool(rec)}
    def enqueue(self,pid,count=1,text=None,settings=None):
        # Validate the selected optional engine before expensive index lookup
        # so an unavailable Vox install is reported immediately.
        raw_product=self.store.get_product(pid)
        early=ProductionSettings.from_dict(settings if settings is not None else raw_product['settings']).snapshot()
        if early.get('voice') and early.get('voice_engine','qwen')=='vox':
            try: require_vox_config(self.cfg)
            except VoiceEngineError as e: raise ServiceError(str(e)) from e
            vv=early.get('vox_voice','natural_female')
            if vv not in VOX_PRESETS and resolve_clone_voice(self._vox_clone_dir(), vv) is None:
                raise ServiceError(f'没找到克隆音色「{vv}」：请把音频放进 {self._vox_clone_dir()} 后刷新列表')
        p,rec=self._ready_index(pid)
        if not p['output']:raise ServiceError('select output folder')
        if p['archived']:raise ServiceError('archived product')
        text=p['copy']['text'] if text is None else text;lines=split_script(text)
        if not lines:raise ServiceError('script empty')
        setting=early
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

    def _voice_command(self, engine, request, workdir):
        if engine == 'qwen':
            py, _model = require_qwen_config(self.cfg)
            return [str(py), '-m', 'workbench.tts_worker', str(workdir/'voice-request.json'), str(workdir)]
        py, model, cache = require_vox_config(self.cfg)
        request.update({'voxcpm_model':str(model), 'voxcpm_cache':str(cache), 'voxcpm_python':str(py), 'ffmpeg':self.cfg.ffmpeg})
        return [str(py), '-m', 'workbench.voxcpm_worker', str(workdir/'voice-request.json'), str(workdir)]

    def _run_voice_request(self, engine, request, workdir, cancel=None):
        workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
        command=self._voice_command(engine,request,workdir)
        (workdir/'voice-request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf8')
        use_lock=bool(getattr(self.cfg,'gpu_lock_path',''))
        with self.gpu_mutex:
            if getattr(self.cfg,'model_dir',''): gpu_preflight(self.cfg)
            with gpu_guard(self.cfg) if use_lock else _null():
                if getattr(self.cfg,'model_dir',''): gpu_preflight(self.cfg)
                run_owned(command,cwd=self.cfg.project_src,timeout=self.cfg.max_stage_seconds,cancel=cancel,log_dir=workdir)
        receipt_path=workdir/'voice-result.json'
        if not receipt_path.is_file(): raise ServiceError('配音没有返回有效结果')
        try: receipt=json.loads(receipt_path.read_text(encoding='utf8'))
        except Exception as e: raise ServiceError('配音结果损坏，请重试') from e
        if receipt.get('success') is not True: raise ServiceError(str(receipt.get('error') or '配音失败'))
        if receipt.get('engine','qwen') != engine: raise ServiceError('配音引擎收据与所选引擎不一致')
        voices=receipt.get('voices')
        if not isinstance(voices,list) or len(voices)!=len(request.get('lines') or []): raise ServiceError('voice receipt incomplete')
        return receipt

    def _retain_preview_diagnostics(self, workdir):
        """Keep owned-process logs private when a preview generation fails.

        The preview directory is intentionally removed so a failed request
        cannot leave a routable media identity behind.  The stdout/stderr
        files are copied to a private diagnostics directory first; callers of
        the HTTP preview boundary never receive these paths or their content.
        """
        try:
            workdir = Path(workdir)
            if not workdir.is_dir():
                return
            dest = Path(self.cfg.data_root) / 'voice-preview-logs' / workdir.name
            dest.mkdir(parents=True, exist_ok=True)
            reject_links(dest)
            for log in workdir.glob('stage-*.log'):
                if log.is_file():
                    shutil.copy2(log, dest / log.name)
        except Exception:
            # Diagnostics must never mask the original preview failure.
            pass

    def _vox_clone_dir(self):
        return Path(self.cfg.project_src) / 'voices'
    def vox_voices(self):
        d = self._vox_clone_dir()
        return {'dir': str(d),
                'presets': [{'id': k, 'label': v['label']} for k, v in VOX_PRESETS.items()],
                'clones': list_clone_voices(d)}
    def preview_voice(self, body):
        if not self._preview_generation_lock.acquire(blocking=False):
            raise ServiceError('已有试听正在生成，请等它完成后再试')
        try:
            return self._preview_voice(body)
        finally:
            self._preview_generation_lock.release()

    def _preview_voice(self, body):
        try: req=validate_preview_payload(body, self._vox_clone_dir())
        except VoiceEngineError as e: raise ServiceError(str(e)) from e
        engine=req['engine']
        if engine=='vox':
            try: require_vox_config(self.cfg)
            except VoiceEngineError as e: raise ServiceError(str(e)) from e
        else:
            try: require_qwen_config(self.cfg)
            except VoiceEngineError as e: raise ServiceError(str(e)) from e
        pid=opaque_preview_id(); d=self.preview_root/pid;d.mkdir(exist_ok=False)
        try:
            request={'lines':[req['text']],'speed':req['speed']}
            if engine=='qwen': request.update({'speaker':req['voice'],'tts_model':self.cfg.tts_model,'ffmpeg':self.cfg.ffmpeg})
            else: request.update({'voice':req['voice'], 'vox_clone_dir':str(self._vox_clone_dir())})
            receipt=self._run_voice_request(engine,request,d,cancel=self.stop_flag.is_set)
            row=receipt['voices'][0]
            if row.get('file')!='voice_000.wav':raise ServiceError('预览音频身份无效')
            source=d/row['file'];reject_links(source)
            if not source.is_file():raise ServiceError('预览音频校验失败')
            # Take one immutable, worker-bound read.  The BytesIO keeps the
            # original .name so the existing copy hook and its race probe hit
            # the same production boundary while the bytes themselves cannot
            # change underneath the copy.
            source_bytes=source.read_bytes()
            worker_sha=row.get('sha256')
            if hashlib.sha256(source_bytes).hexdigest()!=worker_sha:raise ServiceError('预览音频校验失败')
            audio=d/'preview.wav'
            src_snapshot=io.BytesIO(source_bytes);src_snapshot.name=str(source)
            try:
                with src_snapshot,audio.open('xb') as dst: shutil.copyfileobj(src_snapshot,dst)
            finally:
                src_snapshot.close()
            audio_bytes=audio.read_bytes()
            digest=hashlib.sha256(audio_bytes).hexdigest();size=len(audio_bytes)
            if digest!=worker_sha:raise ServiceError('预览音频校验失败')
            meta={'id':pid,'engine':engine,'voice':req['voice'],'sha256':digest,'bytes':size,'path':str(audio),'created_at':time.time()}
            with self._preview_lock:self._previews[pid]=meta
            return {'id':pid,'engine':engine,'voice':req['voice'],'audio_url':f'/api/voice-preview/{pid}?sha256={digest}','sha256':digest,'bytes':size,'cold_start':bool(receipt.get('anchor_cold_start'))}
        except Exception:
            self._retain_preview_diagnostics(d)
            shutil.rmtree(d,ignore_errors=True);raise

    def preview_audio(self,pid,expected_sha=None):
        if not isinstance(pid,str) or len(pid)!=32 or any(c not in '0123456789abcdef' for c in pid): raise ServiceError('预览身份无效')
        if not isinstance(expected_sha,str) or len(expected_sha)!=64 or any(c not in '0123456789abcdef' for c in expected_sha): raise ServiceError('预览音频需要有效指纹')
        with self._preview_lock:meta=copy.deepcopy(self._previews.get(pid))
        if meta is None: raise KeyError(pid)
        if expected_sha and expected_sha!=meta['sha256']: raise ServiceError('预览音频指纹不匹配')
        p=Path(meta['path']);reject_links(p)
        if not p.is_file() or p.stat().st_size!=meta['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=meta['sha256']: raise ServiceError('预览音频已变化，已拒绝播放')
        return p,meta
    def cancel(self,pid,jid):
        j=self.queue.get(jid)
        if j['product_id']!=pid:raise KeyError(jid)
        self.queue.cancel(jid)
        latest=self.queue.get(jid)
        if latest.get('status')=='running' and not (self.worker and self.worker.is_alive() and self._running_job_id==jid):
            self.queue.finalize_cancel_running(jid);latest=self.queue.get(jid)
        return self.public_job(latest)
    def cancel_queued(self,pid):
        self.store.get_product(pid)
        return {'cancelled':self.queue.cancel_all_queued(pid)}
    def get_prefs(self):
        p=Path(self.cfg.data_root)/'prefs.json';default={'cleanup_after_success':True}
        try:
            d=json.loads(p.read_text(encoding='utf8'))
            return {'cleanup_after_success':d.get('cleanup_after_success',True) is True} if isinstance(d,dict) else default
        except Exception:return default
    def set_prefs(self,body):
        if not isinstance(body,dict) or type(body.get('cleanup_after_success')) is not bool:raise ServiceError('cleanup_after_success 必须是布尔值')
        d={'cleanup_after_success':body['cleanup_after_success']};p=Path(self.cfg.data_root)/'prefs.json';p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8');os.replace(tmp,p);return d
    def cleanup_records(self,pid,dry_run=False):
        if type(dry_run) is not bool:raise ServiceError('dry_run 必须是布尔值')
        self.store.get_product(pid)
        terminal=('done','failed','cancelled','interrupted');jobs=[j for j in self.queue.all(pid) if j.get('status') in terminal]
        counts={s:sum(1 for j in jobs if j.get('status')==s) for s in terminal}
        if dry_run:
            total=0
            for j in jobs:
                d=self.queue.root/j['id']
                try:
                    st=d.lstat()
                    if st.st_mode & 0o170000 == 0o120000 or getattr(st,'st_file_attributes',0)&0x400 or not d.is_dir():continue
                    for p,st2 in iter_tree(d):
                        if st2.st_mode & 0o170000 == 0o100000:total+=st2.st_size
                except OSError:pass
            return {'counts':counts,'bytes':total,'total':len(jobs)}
        removed=self.queue.remove_terminal([j['id'] for j in jobs]) if jobs else {}
        freed=0;errors=[]
        for jid in removed:
            d=self.queue.root/jid
            try:
                st=d.lstat()
                if d.parent != self.queue.root or st.st_mode & 0o170000 == 0o120000 or getattr(st,'st_file_attributes',0)&0x400:continue
            except OSError:continue
            b,e=remove_job_dir(d);freed+=b;errors.extend(e)
        return {'removed':removed,'freed_bytes':freed,'errors':errors}
    def archive_product(self,pid):
        p=self.store.get_product(pid)
        if any(j.get('status') in ('queued','running','paused') for j in self.queue.all(pid)):raise ServiceError('还有未结束的任务，请先取消或等它跑完')
        return self.public_product(self.store.archive_product(pid))
    def _pause_requested(self,jid):
        return bool(self.queue.get(jid).get('pause_requested'))
    def _pause_aware_cancel(self,jid,cancel):
        """Cooperative pause for job kinds that have no dedicated pause plumbing.

        Render stages already poll `cancel` at safe points, so a pause is
        implemented by blocking there: no new stage is started while suspended,
        no work is charged, and a cancel arriving during the pause still aborts
        promptly.  The queue only reports `paused` once this worker actually
        reaches such a boundary -- it is not claimed at request time.  The
        already-running OWNED subprocess (ffmpeg/TTS) cannot be OS-suspended and
        keeps finishing its current step; that limit is surfaced in the UI copy.
        """
        def poll():
            if self._pause_requested(jid):
                self.queue.pause_ack(jid)
                while self._pause_requested(jid):
                    if cancel():raise Cancelled('cancel while paused')
                    time.sleep(0.2)
                self.queue.update(jid,status='running',stage='resuming')
            return cancel()
        return poll
    def pause(self,pid,jid):
        j=self.queue.get(jid)
        if j['product_id']!=pid:raise KeyError(jid)
        if not self.queue.pause(jid):raise ServiceError('任务已结束，无法暂停')
        return self.public_job(self.queue.get(jid))
    def resume(self,pid,jid):
        j=self.queue.get(jid)
        if j['product_id']!=pid:raise KeyError(jid)
        # A worker still inside this job resumes where it stopped (the index
        # pipeline picks up between batches); a paused job whose worker is gone
        # (app restarted) goes back into the queue.
        if not self.queue.resume(jid,live=self._running_job_id==jid):raise ServiceError('任务不在暂停状态')
        return self.public_job(self.queue.get(jid))
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
        if mode=='clean':
            return {'text':clean_punctuation(str(body.get('text') or ''))}
        ov=self._ai_overrides()
        base=ov.get('base_url') or self.cfg.ai_base_url
        key=ov.get('api_key',self.cfg.ai_api_key)
        model=ov.get('model') or self.cfg.ai_model
        brief=dict(body.get('brief') or {})
        if mode=='fit':
            target=brief.get('target_seconds')
            if type(target) is not int or not 3<=target<=600:raise ServiceError('目标时长需为 3–600 秒')
            plan=self.timing(pid,str(body.get('text') or ''),body.get('settings'),retrieve=True)
            est=plan['estimate'];setting=ProductionSettings.from_dict({**(p.get('settings') or {}),**(body.get('settings') or {})}).snapshot()
            if est['available_seconds']<=0:raise ServiceError('当前文案没有可用的索引匹配窗口')
            fixed=sum(float((setting.get(k) or {}).get('seconds') or 0) for k in ('intro','outro'))
            usable=min(float(target)-fixed,est['available_seconds'])
            if usable<=0:raise ServiceError('目标时长没有可用口播空间')
            lines=split_script(str(body.get('text') or ''))
            budget=max(1,int(max(0,usable-.30*len(lines))*est['chars_per_second']))
            brief.update(speed=setting['speed'],chars_per_second=est['chars_per_second'],chars_budget=budget)
        if mode=='variants':
            requested=brief.get('count',3)
            if type(requested) is not int or not 2<=requested<=20:raise ServiceError('裂变数量需为 2–20 的整数')
        try:
            result=ai_run(base,key,model,mode,str(body.get('text') or ''),brief)
            if mode=='variants':
                try: rows=parse_script_variants(result)
                except ValueError as exc: raise ServiceError(str(exc)) from None
                rows=rows[:requested]
                return {'variants':rows,'text':'\n---\n'.join(rows),'requested':requested,'produced':len(rows),'partial':len(rows)<requested,'reason':'AI 返回的有效且不同的稿件不足' if len(rows)<requested else ''}
            return {'text':result}
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
    def _finish_cancelled(self,jid,e):
        """Terminal bookkeeping for a worker-observed cancel/interrupt.

        Some other path may have finalised the job first -- cancel() moves a
        paused job straight to `cancelled` while this worker is still
        reporting (the exit race) -- so the state already recorded is kept and
        this bookkeeping must never raise into the worker thread."""
        try:
            self.queue.update(jid,status='cancelled' if not self.stop_flag.is_set() else 'interrupted',stage='cancelled',error={'stage':'cancel','check':'requested','message':str(e)})
        except ValueError:
            pass    # already terminal: queue.update refuses a rewrite; record nothing
    def _loop(self):
        while not self.stop_flag.is_set():
            try:
                threshold=getattr(self.cfg,'min_free_bytes',1610612736)
                free=shutil.disk_usage(self.queue.root).free;self.disk_free_bytes=free
                if free<threshold:
                    self.disk_low=True
                    self.stop_flag.wait(30);continue
                self.disk_low=False
                job=self.queue.next()
                if not job:self.stop_flag.wait(.2);continue
                jid=job['id'];cancel=lambda:self.stop_flag.is_set() or self.queue.get(jid).get('cancel_requested',False)
                self._running_job_id=jid
                try:
                    if sha256(self.queue.root/jid/'input.json')!=job['input_sha256']:raise ServiceError('input.json SHA changed')
                    if job['input']['kind']=='index':self._run_index(job,cancel)
                    else:self._run_render(job,self._pause_aware_cancel(jid,cancel))
                except (InterruptedError,Cancelled) as e:self._finish_cancelled(jid,e)
                except Exception as e:
                    self.worker_error={'at':time.time(),'type':type(e).__name__,'message':str(e)}
                    if getattr(e,'after_replace',False):
                        try:
                            receipt=getattr(e,'receipt',None) or {}
                            (self.queue.root/'reconciliation.json').write_text(json.dumps({'job_id':jid,'error':str(e),'after_effect':True,'receipt':receipt},ensure_ascii=False),encoding='utf8')
                        except OSError:pass
                    else:
                        details=getattr(e,'details',None) or {'stage':job.get('stage','worker'),'check':type(e).__name__,'message':str(e)}
                        try:self.queue.update(jid,status='failed',stage='failed',error=details)
                        except BaseException:pass
                except BaseException as e:
                    try:
                        if self.queue.get(jid).get('status') not in ('done','failed','cancelled','interrupted'):self.queue.update(jid,status='failed',stage='failed',error={'stage':'worker','check':type(e).__name__,'message':str(e)})
                    except BaseException:pass
                    raise
                finally:self._running_job_id=None
            except BaseException as e:
                self.worker_error={'at':time.time(),'type':type(e).__name__,'message':str(e)}
                jid=self._running_job_id
                if jid:
                    try:
                        if self.queue.get(jid).get('status') not in ('done','failed','cancelled','interrupted'):self.queue.update(jid,status='failed',stage='failed',error={'stage':'worker','check':type(e).__name__,'message':str(e)})
                    except BaseException:pass
                self._running_job_id=None
                self.stop_flag.wait(2)
    def _run_index(self,j,cancel):
        x=j['input'];p=self.store.get_product(j['product_id'])
        if (p.get('source') or {}).get('path')!=(x['source'] or {}).get('path') or not self._snapshot_fresh(x,require_mapping=False):raise ServiceError('index/source generation changed')
        # The GPU lock, the lock file and the embedder (VRAM) are held as one
        # re-acquirable slot: a pause released at a file boundary must give all
        # three back, and a resume must take them again before work continues.
        slot={'emb':None,'guard':None,'held':False}
        def acquire():
            if slot['held']:return
            self.gpu_mutex.acquire()
            try:
                if self.cfg.model_dir:
                    gpu_preflight(self.cfg)
                    slot['guard']=gpu_guard(self.cfg)
                    slot['guard'].__enter__()
                    gpu_preflight(self.cfg)
                slot['emb']=self.embedder_factory()
            except BaseException:
                if slot['guard'] is not None:
                    try:slot['guard'].__exit__(None,None,None)
                    finally:slot['guard']=None
                self.gpu_mutex.release();raise
            slot['held']=True
        def release():
            if not slot['held']:return
            slot['held']=False
            try:
                if slot['emb'] is not None and hasattr(slot['emb'],'close'):slot['emb'].close()
            finally:
                slot['emb']=None
                if slot['guard'] is not None:
                    try:slot['guard'].__exit__(None,None,None)
                    finally:slot['guard']=None
                self.gpu_mutex.release()
        idx=None
        acquire()
        try:
            idx=self._indexer(slot['emb'])
            baseline=None
            cur=self.store.get_product(j['product_id'])
            if cur.get('index_id'):
                prev_rec=self.store.get_index(cur['index_id'])
                if prev_rec and prev_rec.get('source')==x['source']['path']:baseline=prev_rec
            def progress(n,total,reused=0,skipped=0):self.queue.update(j['id'],stage='embedding',progress=round(n/total*95),indexed=n,total=total,reused=reused,skipped=skipped)
            def on_pause():
                # The indexer confirmed the boundary, so the queue may honestly
                # say 'paused' -- and the GPU lock/model are released right away
                # instead of being held while nothing runs.
                self.queue.pause_ack(j['id'])
                release()
            def on_resume():
                acquire()
                idx.embedder=slot['emb']
            rec=idx.build(j['product_id'],x['source']['path'],x['settings'],x['model_identity'],progress,cancel,
                          pause=lambda:self._pause_requested(j['id']),on_pause=on_pause,on_resume=on_resume,baseline=baseline)
        finally:
            release()
        if cancel():raise Cancelled('index/cancel')
        p2=self.store.get_product(j['product_id'])
        if (p2.get('source') or {}).get('path')!=(x['source'] or {}).get('path') or not self._snapshot_fresh({'source':x['source'],'source_fingerprints':[e['fingerprint'] for e in rec['files']]},require_mapping=False) or self._indexer(None).is_obsolete(rec): raise ServiceError('index/source changed during build')
        skipped_public=public_skipped(rec.get('skipped'))
        with self.store.lock,self.queue.lock:
            self.store.put_index(j['product_id'],rec,x['selection_revision'])
            def commit(candidate):
                current=self.store.get_product(j['product_id'])
                queued=candidate[j['id']]
                return ((current.get('source') or {}).get('path')==(x['source'] or {}).get('path') and current.get('index_id')==rec['id'] and self.store.get_index(rec['id'])==rec and queued.get('input_sha256')==j.get('input_sha256') and not self._indexer(None).is_obsolete(rec) and self._snapshot_fresh({'source':x['source'],'source_fingerprints':[e['fingerprint'] for e in rec['files']]},require_mapping=False))
            try:
                self.queue.update(j['id'],status='done',stage='indexed',progress=100,index_id=rec['id'],indexed=len(rec['files']),total=len(rec['files'])+len(skipped_public),skipped=len(skipped_public),skipped_files=skipped_public,condition=commit)
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
            engine=x['settings'].get('voice_engine','qwen')
            if engine=='vox':
                self.queue.update(jid,stage='voice',progress=5)
                receipt=self._run_voice_request('vox',{'lines':x['lines'],'voice':x['settings'].get('vox_voice','natural_female'),'speed':speed,'vox_clone_dir':str(self._vox_clone_dir())},d,cancel)
            else:
                # Keep the original Qwen process/module invocation intact.
                req=d/'voice-request.json';req.write_text(json.dumps({'lines':x['lines'],'speaker':x['settings']['speaker'],'tts_model':self.cfg.tts_model,'speed':speed,'ffmpeg':self.cfg.ffmpeg},ensure_ascii=False),encoding='utf8')
                with self.gpu_mutex:
                    gpu_preflight(self.cfg)
                    with gpu_guard(self.cfg):
                        gpu_preflight(self.cfg);self.queue.update(jid,stage='voice',progress=5)
                        run_owned([self.cfg.tts_python,'-m','workbench.tts_worker',str(req),str(d)],cwd=self.cfg.project_src,timeout=self.cfg.max_stage_seconds,cancel=cancel,log_dir=d)
                receipt=json.loads((d/'voice-result.json').read_text(encoding='utf8'))
                if receipt.get('success') is not True or len(receipt.get('voices',[]))!=len(x['lines']):raise ServiceError('voice receipt incomplete')
            if abs(speed-1.0)>1e-9 and receipt.get('speed')!=speed:raise ServiceError('voice receipt speed mismatch')
            if receipt.get('engine','qwen')!=engine:raise ServiceError('voice receipt engine mismatch')
            x['voices']=receipt['voices']
        else:x['voices']=[]
        x['variant']=j['variant'];x['selection_rank']=x.get('selection_rank',j.get('selection_rank',j['variant']));x['input_sha256']=j['input_sha256'];x['bgm_sha256']=x.get('bgm_sha256')
        self.queue.update(jid,stage='render',progress=20)
        def progress(n,total):self.queue.update(jid,stage='render',progress=20+round(50*n/total))
        final,report=render_job(x,self.cfg,d,cancel,progress)
        if x['settings']['voice']:
            worker=Path(self.cfg.project_src)/'workbench'/('voxcpm_worker.py' if engine=='vox' else 'tts_worker.py')
            report['voice_calibration']={'input_sha256':j['input_sha256'],'worker_sha256':sha256(worker),'engine':engine,'voice':x['settings'].get('vox_voice') if engine=='vox' else x['settings'].get('speaker'),'speed':speed,'voices':copy.deepcopy(receipt['voices'])}
        if cancel():raise Cancelled('render/cancel before export')
        self.queue.update(jid,stage='export',progress=85)
        lexical_under(x['output']['path'], x['output']['path'])
        dest,got=export_numbered(final,x['output']['path'],report,_short_title(p,x),authorization=x['output'].get('_lease'))
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
        try:
            art=self.queue.get(jid).get('artifact') or {}
            if self.get_prefs().get('cleanup_after_success') and isinstance(art,dict) and 'sha256' in art and 'bytes' in art:
                out=Path(self.queue.get(jid).get('output') or '')
                if out.is_file() and out.stat().st_size==art.get('bytes'):
                    receipt=cleanup_job_dir(d,Path(getattr(self.cfg,'font_path','')).name)
                    self.queue.update(jid,cleanup={'at':time.time(),'files':receipt.get('files',0),'bytes':receipt.get('bytes',0),'errors':receipt.get('errors',[])})
                else:self.queue.update(jid,cleanup={'at':time.time(),'files':0,'bytes':0,'errors':['output receipt mismatch; cleanup skipped']})
        except Exception as e:
            try:self.queue.update(jid,cleanup={'at':time.time(),'files':0,'bytes':0,'errors':[str(e)]})
            except Exception:pass
class _null:
    def __enter__(self):return None
    def __exit__(self,*a):return False
