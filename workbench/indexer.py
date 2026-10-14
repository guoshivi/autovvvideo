from pathlib import Path
import hashlib,json,time,uuid,os,copy,math,subprocess,shutil
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait as futures_wait
from .browser import FolderBrowser,VIDEO_EXT,reject_links
class FakeEmbedder:
    """Synthetic fixtures only. Never constructed by application startup."""
    synthetic=True
    def __init__(self,dim=8):self.dim=dim
    def encode_media(self,payload):return self._v(payload)
    def encode_media_many(self,payloads):return [self._v(p) for p in payloads]
    def encode_texts(self,texts):return [self._v(t) for t in texts]
    def _v(self,x):
        h=hashlib.sha256(str(x).encode()).digest(); a=[(h[i%len(h)]+1)/255 for i in range(self.dim)]; n=math.sqrt(sum(v*v for v in a));return [v/n for v in a]
class ProductionEmbedder:
    synthetic=False
    def __init__(self,model_dir):
        import torch
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise RuntimeError('index/load: CUDA BF16 required; no CPU/fake fallback')
        from reference.core.videopipe.embedder import Embedder
        self.model=Embedder(model_dir,mode='text_vision',preset='fast',device='cuda')
    def encode_media(self,payload):return self.model.encode_media(payload)
    def encode_media_many(self,payloads):return self.model.encode_media_many(payloads)
    def encode_texts(self,texts):return self.model.encode_texts(texts)
    def close(self):
        self.model=None
        import gc,torch
        gc.collect();torch.cuda.empty_cache()
def encode_many(embedder,payloads):
    """Embed several clips in one call when the embedder supports it.

    Batching is what actually feeds the GPU: one forward pass per clip left the
    card idle ~90% of the time (measured 5%,5%,5%,16%,22%,93% utilization).
    A batch that fails for any reason (e.g. a transient OOM) degrades to the
    one-by-one path, which re-raises if the model is genuinely broken."""
    items=list(payloads)
    if not items:return []
    fn=getattr(embedder,'encode_media_many',None)
    if fn is not None:
        try:
            out=list(fn(items))
            if len(out)==len(items):return out
        except Exception:
            pass
    return [embedder.encode_media(p) for p in items]
def wait_while_paused(pause,cancel=None,message='index/cancel while paused'):
    """Cooperative pause point: block while `pause()` reports True.

    Called between batches rather than mid-slice, so a paused job holds no
    GPU work and leaves no half-written file behind.  `cancel` is polled while
    suspended so a cancel issued during the pause terminates promptly."""
    if pause is None:return
    while pause():
        if cancel is not None and cancel():raise InterruptedError(message)
        time.sleep(0.2)
def norm(v):
    a=[float(x) for x in v];n=math.sqrt(sum(x*x for x in a))
    if not a or not math.isfinite(n) or n<=0 or any(not math.isfinite(x) for x in a):raise ValueError('index/vector: invalid numeric vector')
    return [x/n for x in a]
def _finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v))
def _vector_ok(v):
    """A finite, non-empty, non-degenerate float vector."""
    if not isinstance(v,list) or not v:return False
    if any(not _finite(x) for x in v):return False
    n=math.sqrt(sum(float(x)*float(x) for x in v))
    return math.isfinite(n) and n>0
def entry_valid(entry):
    """Full contract check for a reusable (baseline or journalled) entry.

    A same-fingerprint journal is not proof that the *contents* of the entry
    are usable: a corrupted record with an out-of-range window (start=-999,
    seconds=-10) or a non-finite vector must trigger a rebuild instead of
    being republished.  Checks window bounds against the recorded duration,
    vector shape/finiteness, and the source identity fields."""
    if not isinstance(entry,dict):return False
    fp=entry.get('fingerprint')
    if not isinstance(fp,dict):return False
    if not isinstance(fp.get('path'),str) or not fp['path']:return False
    if not _finite(fp.get('bytes')) or fp.get('bytes')<=0:return False
    if not _finite(fp.get('mtime_ns')):return False
    sha=fp.get('sha256')
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):return False
    dur=entry.get('duration')
    if not _finite(dur) or float(dur)<=0:return False
    blocks=entry.get('blocks')
    if not isinstance(blocks,list) or not blocks:return False
    dim=None
    for b in blocks:
        if not isinstance(b,dict):return False
        start=b.get('start');sec=b.get('seconds')
        if not _finite(start) or float(start)<0:return False
        if not _finite(sec) or float(sec)<=0:return False
        # the window must lie inside the recorded source duration (small slack
        # for float rounding in ffprobe)
        if float(start)+float(sec)>float(dur)+0.5:return False
        v=b.get('vector')
        if not _vector_ok(v):return False
        if dim is None:dim=len(v)
        elif len(v)!=dim:return False
    if not _vector_ok(entry.get('vector')) or len(entry['vector'])!=dim:return False
    return True
def mean_vector(blocks):
    return [sum(b['vector'][i] for b in blocks)/len(blocks) for i in range(len(blocks[0]['vector']))]
def reusable_entry(entry,fp):
    """A validated deep copy of `entry` for `fp`, or None when it must be rebuilt.

    The stored mean vector is recomputed from the validated blocks so a record
    whose top-level vector disagrees with its windows cannot poison search."""
    if not isinstance(entry,dict) or entry.get('fingerprint')!=fp:return None
    if not entry_valid(entry):return None
    e=copy.deepcopy(entry)
    try:e['vector']=norm(mean_vector(e['blocks']))
    except ValueError:return None
    return e

# Errors that mean "this particular source file cannot be used" (truncated
# container with no moov atom, unsupported codec, a file that cannot be sliced
# at all) as opposed to "the indexing system is broken".  Such a file is
# skipped and reported on the record; it must never abort the whole folder --
# one 8.9MB truncated mp4 previously killed a run at 723/791.
SOURCE_DEFECT_PREFIX=('index/probe','index/window')
def is_source_defect(exc):
    return str(exc).startswith(SOURCE_DEFECT_PREFIX)
def probe_video(path,ffprobe='ffprobe',timeout=60):
    r=subprocess.run([str(ffprobe),'-v','error','-select_streams','v:0','-show_entries','format=duration:stream=codec_name,duration','-of','json',str(path)],capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise ValueError(f'index/probe: {Path(path).name}: {r.stderr[-300:]}')
    d=json.loads(r.stdout);s=d.get('streams') or [];duration=float((s[0].get('duration') if s else None) or d.get('format',{}).get('duration') or 0)
    if not s or not math.isfinite(duration) or duration<=0:raise ValueError(f'index/probe: invalid video {Path(path).name}')
    return duration

def fingerprint_file(path):
    """Hash through one descriptor and bind metadata before/after the read."""
    p=Path(path);reject_links(p);h=hashlib.sha256()
    with p.open('rb') as f:
        before=os.fstat(f.fileno()); total=0
        for b in iter(lambda:f.read(1024*1024),b''):
            h.update(b); total+=len(b)
        after=os.fstat(f.fileno())
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or total!=before.st_size:
        raise ValueError('source changed while fingerprinting')
    return {'path':str(p.absolute()),'bytes':before.st_size,'mtime_ns':before.st_mtime_ns,'sha256':h.hexdigest()}

def collect_source_fingerprints(source,browser=None):
    src=Path(source).absolute(); b=browser or FolderBrowser([src]); files=sorted(b.files(src))
    return [fingerprint_file(p) for p in files]

def expected_files_unchanged(expected,actual):
    """Contract v2 (task snapshot, 2026-10-11): every expected (manifest) file must
    still exist with an identical fingerprint. Files newly added to the directory are
    tolerated: they are never silently included in an accepted generation and need an
    explicit re-index before use."""
    index={e.get('path'):e for e in actual}
    return all(index.get(e.get('path'))==e for e in expected)

class Indexer:
    # Windows shorter than this are never encoded on their own. A slice shorter
    # than a single frame (e.g. a 30.013s clip whose 4th 10s slice lands at 30.0s)
    # encodes to a 0-frame mp4 that ffmpeg writes with exit 0 and no duration; the
    # embedder then fails on it (torchcodec "Could not seek file to pts=0").
    MIN_WINDOW=0.5
    # Indexing used to be strictly serial per window (slice -> embed -> unlink),
    # which left the GPU idle in bursts (measured 5%,5%,5%,16%,22%,93%).  Slicer
    # threads now cut the next files while the embedder works, and clips are
    # embedded ENCODE_BATCH at a time.
    # Measured on 6 real files / 16 windows (4x 4K 10bit + 2x 1080p):
    #   1 thread / batch 1   17.7s   1.00x   VRAM peak  5.0GB
    #   2 threads / batch 2  12.0s   1.48x   VRAM peak  6.2GB
    #   3 threads / batch 4  10.9s   1.62x   VRAM peak  7.6GB   <- chosen
    #   3 threads / batch 8  11.2s   1.58x   VRAM peak  8.4GB
    #   6 threads / batch 8  10.0s   1.78x   VRAM peak 11.3GB   (too close to 12GB)
    # Set both to 1 to reproduce the old behaviour.
    SLICE_WORKERS=3
    ENCODE_BATCH=4
    def __init__(self,root,embedder,browser=None,ffmpeg='ffmpeg',ffprobe='ffprobe'):
        self.root=Path(root);self.embedder=embedder;self.browser=browser;self.ffmpeg=ffmpeg;self.ffprobe=ffprobe;self.root.mkdir(parents=True,exist_ok=True)
    def _fp(self,p):
        return fingerprint_file(p)
    def _clip_ok(self,clip):
        """A clip counts as usable only if ffprobe can read a positive duration
        and a video stream; GPU encodes that failed mid-write can leave a
        non-empty but broken file that `is_file()` would wrongly accept, and a
        0-frame encode exits 0 with no duration."""
        if not clip.is_file():return False
        try:
            r=subprocess.run([str(self.ffprobe),'-v','error','-select_streams','v:0','-show_entries','format=duration:stream=codec_name','-of','json',str(clip)],capture_output=True,text=True,timeout=30)
            if r.returncode:return False
            d=json.loads(r.stdout)
            if not (d.get('streams') or []):return False
            dur=float((d.get('format') or {}).get('duration') or 0)
            return math.isfinite(dur) and dur>0
        except Exception:return False
    def _windows(self,duration,span=10.0):
        """Split [0,duration) into span-second windows, folding a trailing stub
        shorter than MIN_WINDOW into the previous window rather than emitting a
        near-zero-length slice of its own. Always returns at least one window."""
        out=[];start=0.0
        while start<duration-1e-6:
            sec=min(span,duration-start);out.append((start,sec));start+=sec
        if len(out)>1 and out[-1][1]<self.MIN_WINDOW:
            _,tail=out.pop();ps,psec=out[-1];out[-1]=(ps,psec+tail)
        return out
    def _files(self,src):return sorted(self.browser.files(src) if self.browser else FolderBrowser([src]).files(src))
    def _checkpoint_path(self,src,model_identity,settings):
        """Where the resumable progress of an in-flight build lives.

        A build publishes its manifest only after the very last file, so a run
        that dies at 723/791 used to lose every block it had computed.  Progress
        is therefore journalled next to the records and deleted on success."""
        key=hashlib.sha256(json.dumps([str(src),model_identity,(settings or {}).get('sample_seconds')],ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
        return self.root/(f'resume-{key}.jsonl')
    def _load_checkpoint(self,path,src,model_identity,settings):
        """Entries journalled by an earlier attempt.

        Reuse is keyed by path and gated on full fingerprint equality in
        build(), and every entry must additionally satisfy the full window /
        vector / duration contract (entry_valid) -- a same-fingerprint record
        with an invalid window is rebuilt, never republished.  A torn trailing
        line after a hard kill is discarded, and any other malformed shape
        (non-object head, entry without a path, unexpected types) makes the
        journal unusable -- a rebuild, never a failed run."""
        prev={}
        try:
            with path.open('r',encoding='utf8') as f:
                head=json.loads(f.readline() or '{}')
                if not isinstance(head,dict) or not isinstance(head.get('settings') or {},dict):return {}
                if str(head.get('source'))!=str(src) or head.get('model_identity')!=model_identity or (head.get('settings') or {}).get('sample_seconds')!=(settings or {}).get('sample_seconds'):
                    return {}
                for line in f:
                    try:e=json.loads(line)
                    except ValueError:continue          # torn line after a hard kill
                    if isinstance(e,dict) and isinstance(e.get('fingerprint'),dict) and isinstance(e['fingerprint'].get('path'),str) and e.get('blocks'):
                        prev[e['fingerprint']['path']]=e
        except Exception:
            # The journal is only a reuse cache: any malformed shape costs a
            # rebuild, never the run.
            return {}
        return prev
    def _discard_clips(self,tmp,n):
        """Drop slices already written for source file #n so a skipped file
        leaves no partial clips behind in the staging directory."""
        for c in tmp.glob(f'clip-{n}-*.mp4'):
            try:c.unlink()
            except OSError:pass
    def _journal(self,ck,entry):
        """Append one finished file to the resume journal. A journal failure
        costs resumability, never the run. Only fully-valid entries are written,
        so a resumed build can trust what it reads back."""
        try:
            if not entry_valid(entry):return
            ck.write(json.dumps(entry,ensure_ascii=False,allow_nan=False)+'\n');ck.flush()
        except (OSError,ValueError):pass
    def _slice_file(self,p,n,tmp,fp):
        """Cut every window of one source file.

        This runs on a slicer thread, in parallel with the embedder: file N+1 is
        being cut while file N is being embedded, which is the whole point --
        indexing was serialized on `slice -> embed -> unlink` per window and both
        the GPU (bursty 5%..93%) and ffmpeg sat idle most of the time.
        Returns (duration, [(start,seconds,payload,clip)]); `clip` is the staged
        file to delete once its vector exists (None on the synthetic path)."""
        synthetic=getattr(self.embedder,'synthetic',False)
        try:
            duration=10.0 if synthetic else probe_video(p,self.ffprobe)
            out=[]
            for start,sec in self._windows(duration):
                if synthetic:
                    out.append((start,sec,{'video':[str(p)],'start':start,'seconds':sec,'sha256':fp['sha256']},None));continue
                clip=tmp/f'clip-{n}-{len(out)}.mp4'
                # 10bit (p010) input is rejected by h264_nvenc with
                # "Provided device doesn't support required NVENC features";
                # converting to 8bit inside the CUDA scaler keeps the whole
                # path on the GPU instead of falling back to CPU libx264.
                gpu=[str(self.ffmpeg),'-nostdin','-n','-v','error','-hwaccel','cuda','-hwaccel_output_format','cuda','-ss',str(start),'-t',str(sec),'-i',str(p),'-map','0:v:0','-an','-vf','scale_cuda=w=-2:h=480:format=yuv420p','-c:v','h264_nvenc','-preset','p1','-cq','28',str(clip)]
                cpu=[str(self.ffmpeg),'-nostdin','-n','-v','error','-ss',str(start),'-t',str(sec),'-i',str(p),'-map','0:v:0','-an','-vf','scale=-2:480','-c:v','libx264','-preset','ultrafast','-crf','28',str(clip)]
                r=subprocess.run(gpu,capture_output=True,text=True,timeout=max(60,int(sec*10)))
                if r.returncode or not self._clip_ok(clip):
                    try:clip.unlink()
                    except OSError:pass
                    r=subprocess.run(cpu,capture_output=True,text=True,timeout=max(60,int(sec*10)))
                if r.returncode or not self._clip_ok(clip):
                    try:size=clip.stat().st_size
                    except OSError:size=-1
                    raise ValueError(f'index/window: {p.name} {start}: rc={r.returncode} size={size} {r.stderr[-400:]}')
                out.append((start,sec,{'video':[str(clip)]},clip))
            return duration,out
        except BaseException:
            self._discard_clips(tmp,n);raise
    def _encode(self,payloads):
        return encode_many(self.embedder,payloads)
    def build(self,product_id,source,settings,model_identity,progress=None,cancel=None,pause=None,on_pause=None,on_resume=None,baseline=None):
        src=Path(source).resolve();reject_links(src); files=self._files(src)
        if not files:raise ValueError('index/scan: 选定目录没有可索引视频')
        # Incremental reuse: when the previous index for the same folder/model
        # exists, files whose fingerprint is byte-identical keep their blocks
        # and are skipped entirely (no probe, no GPU embedding).
        prev={}
        if isinstance(baseline,dict) and baseline.get('source')==str(src) and baseline.get('model_identity')==model_identity and (baseline.get('settings') or {}).get('sample_seconds')==(settings or {}).get('sample_seconds'):
            prev={e['fingerprint']['path']:e for e in baseline.get('files',[]) if isinstance(e,dict) and isinstance(e.get('fingerprint'),dict) and 'blocks' in e}
        # Resume: progress journalled by an earlier attempt for this exact
        # source/model/sampling wins over the older published baseline because it
        # is strictly newer (it already folded the baseline in).
        ckpt=self._checkpoint_path(src,model_identity,settings)
        resumed=self._load_checkpoint(ckpt,src,model_identity,settings)
        if resumed:prev=resumed
        rid=uuid.uuid4().hex;tmp=self.root/(rid+'.tmp');tmp.mkdir(exist_ok=False)
        # Staging directories of builds that never finished (killed, or paused
        # and then abandoned). Only old ones are removed, so a concurrent
        # instance is never touched. The current build's own staging dir is
        # excluded explicitly.
        for stale in self.root.glob('*.tmp'):
            if stale.name==tmp.name:continue
            try:
                if time.time()-stale.stat().st_mtime>24*3600:shutil.rmtree(stale)
            except OSError:pass
        entries={};skipped=[];reused=0
        ck=ckpt.open('w',encoding='utf8')
        ck.write(json.dumps({'source':str(src),'model_identity':model_identity,'settings':{'sample_seconds':(settings or {}).get('sample_seconds')}},ensure_ascii=False)+'\n');ck.flush()
        lookahead=max(1,self.SLICE_WORKERS)*2
        pool=ThreadPoolExecutor(max_workers=max(1,self.SLICE_WORKERS),thread_name_prefix='index-slice')
        pending=deque();groups=deque();queue=[];nxt=0
        paused=False;published=False
        def report():
            if progress:progress(len(entries),len(files),reused,len(skipped))
        try:
            while True:
                # Cancel is checked at every file/window boundary -- also while
                # paused -- so a cancel requested mid-run aborts promptly
                # instead of after the whole folder was indexed.
                if cancel and cancel():raise InterruptedError('index/cancel')
                if pause is not None and pause():
                    if not paused:
                        paused=True
                        if on_pause is not None:on_pause()
                    time.sleep(0.2);continue
                if paused:
                    # the pause was lifted while we were parked: reacquire
                    # whatever the pause released (GPU lock/model) before the
                    # pipeline continues
                    paused=False
                    if on_resume is not None:on_resume()
                # 1. keep the slicers fed (reused files need no slicing at all)
                while nxt<len(files) and len(pending)<lookahead:
                    i=nxt;nxt+=1;p=files[i];fp=self._fp(p)
                    old=reusable_entry(prev.get(fp['path']),fp)
                    if old is not None:
                        entries[i]=old;reused+=1
                        self._journal(ck,entries[i]);report();continue
                    pending.append((i,p,fp,pool.submit(self._slice_file,p,i,tmp,fp)))
                # 2. harvest one finished file, in file order
                if pending and pending[0][3].done():
                    i,p,fp,f=pending.popleft()
                    try:duration,blocks=f.result()
                    except ValueError as e:
                        if not is_source_defect(e):raise
                        skipped.append({'path':fp['path'],'name':p.name,'bytes':fp['bytes'],'reason':str(e)[:400]})
                        report();continue
                    g={'i':i,'fp':fp,'duration':duration,'blocks':[{'start':s,'seconds':sec,'vector':None} for s,sec,_,_ in blocks],'need':len(blocks),'got':0}
                    groups.append(g)
                    for j,(s,sec,payload,clip) in enumerate(blocks):queue.append((g,j,payload,clip))
                    continue
                # 3. embed a batch of ready clips
                if queue and (len(queue)>=self.ENCODE_BATCH or (not pending and nxt>=len(files))):
                    batch=queue[:self.ENCODE_BATCH];del queue[:self.ENCODE_BATCH]
                    vecs=self._encode([b[2] for b in batch])
                    if len(vecs)!=len(batch):raise ValueError(f'index/vector: embedder returned {len(vecs)} vectors for {len(batch)} clips')
                    for (g,j,payload,clip),v in zip(batch,vecs):
                        g['blocks'][j]['vector']=norm(v);g['got']+=1
                        if clip is not None:
                            try:clip.unlink()
                            except OSError:pass
                    # clips are queued in file order, so groups complete in order
                    while groups and groups[0]['got']==groups[0]['need']:
                        g=groups.popleft();blocks=g['blocks']
                        entry={'fingerprint':g['fp'],'duration':g['duration'],'blocks':blocks,'vector':norm(mean_vector(blocks))}
                        entries[g['i']]=entry
                        self._journal(ck,entry);report()
                    continue
                # 4. nothing ready yet
                if pending:
                    futures_wait([pending[0][3]],timeout=0.2);continue
                if nxt>=len(files) and not queue:break
                time.sleep(0.05)
            if groups or queue:raise ValueError('index/pipeline: internal error, unfinished windows remain')
            if not entries:raise ValueError('index/scan: 所有素材都无法解析（'+'; '.join(s['name'] for s in skipped[:5])+'）')
            ordered=[entries[i] for i in sorted(entries)]
            rec={'id':rid,'product_id':product_id,'source':str(src),'settings':copy.deepcopy(settings),'model_identity':model_identity,'synthetic':bool(getattr(self.embedder,'synthetic',False)),'count':len(ordered),'reused':reused,'skipped':skipped,'files':ordered,'created_at':time.time()}
            now=[self._fp(p) for p in self._files(src)]
            if not expected_files_unchanged([e['fingerprint'] for e in ordered],now):
                raise ValueError('index/source changed during build')
            (tmp/'manifest.json').write_text(json.dumps(rec,ensure_ascii=False,allow_nan=False),encoding='utf8');os.replace(tmp,self.root/rid)
            published=True
            # Windows: the journal can only be removed once its own handle is
            # closed (a handle blocks the delete), so close before unlinking.
            try:ck.close()
            except Exception:pass
            try:ckpt.unlink()
            except OSError:pass
            return rec
        finally:
            try:ck.close()
            except Exception:pass
            # Slicer threads must be stopped before the staging directory is
            # removed: rmtree first could race a slicer still writing a clip.
            pool.shutdown(wait=True,cancel_futures=True)
            if not published:shutil.rmtree(tmp,ignore_errors=True)
    def is_obsolete(self,rec):
        try:
            expected=[e['fingerprint'] for e in rec['files']]
            src=Path(rec['source'])
            # Fast path: if every recorded file still has the same
            # (path, bytes, mtime_ns) we declare the generation unchanged
            # WITHOUT re-reading gigabytes from disk/NAS.  Content edits that
            # deliberately preserve size+mtime are out of the threat model; any
            # metadata drift falls through to the authoritative full-hash check.
            try:
                meta={}
                for p in self._files(src):
                    pp=Path(p).absolute();reject_links(pp);st=pp.stat()
                    meta[str(pp)]=(st.st_size,st.st_mtime_ns)
                if expected and all(
                        e.get('bytes') is not None and e.get('mtime_ns') is not None
                        and meta.get(e.get('path'))==(e['bytes'],e['mtime_ns'])
                        for e in expected):
                    return False
            except (OSError,ValueError):pass
            actual=[self._fp(p) for p in self._files(src)]
            return not expected_files_unchanged(expected,actual)
        except (OSError,ValueError):return True
    def search(self,rec,query,k=5):
        q=self.embedder.encode_texts([query])[0] if hasattr(self.embedder,'encode_texts') else self.embedder.encode_media(query)
        q=norm(q);rows=[]
        for e in rec['files']:
            for b in e['blocks']:
                v=norm(b['vector'])
                if len(v)!=len(q):raise ValueError('index/search: vector dimension mismatch')
                rows.append({'score':sum(a*c for a,c in zip(v,q)),'path':e['fingerprint']['path'],'source_sha256':e['fingerprint']['sha256'],'start':b['start'],'seconds':b['seconds'],'index_id':rec['id']})
        return sorted(rows,key=lambda x:x['score'],reverse=True)[:k]
