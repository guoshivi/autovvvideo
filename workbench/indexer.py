from pathlib import Path
import hashlib,json,time,uuid,os,copy,math,subprocess
from .browser import FolderBrowser,VIDEO_EXT,reject_links
class FakeEmbedder:
    """Synthetic fixtures only. Never constructed by application startup."""
    synthetic=True
    def __init__(self,dim=8):self.dim=dim
    def encode_media(self,payload):return self._v(payload)
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
    def encode_texts(self,texts):return self.model.encode_texts(texts)
    def close(self):
        self.model=None
        import gc,torch
        gc.collect();torch.cuda.empty_cache()
def norm(v):
    a=[float(x) for x in v];n=math.sqrt(sum(x*x for x in a))
    if not a or not math.isfinite(n) or n<=0 or any(not math.isfinite(x) for x in a):raise ValueError('index/vector: invalid numeric vector')
    return [x/n for x in a]
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
    def __init__(self,root,embedder,browser=None,ffmpeg='ffmpeg',ffprobe='ffprobe'):
        self.root=Path(root);self.embedder=embedder;self.browser=browser;self.ffmpeg=ffmpeg;self.ffprobe=ffprobe;self.root.mkdir(parents=True,exist_ok=True)
    def _fp(self,p):
        return fingerprint_file(p)
    def _files(self,src):return sorted(self.browser.files(src) if self.browser else FolderBrowser([src]).files(src))
    def build(self,product_id,source,settings,model_identity,progress=None,cancel=None,baseline=None):
        src=Path(source).resolve();reject_links(src); files=self._files(src)
        if not files:raise ValueError('index/scan: 选定目录没有可索引视频')
        # Incremental reuse: when the previous index for the same folder/model
        # exists, files whose fingerprint is byte-identical keep their blocks
        # and are skipped entirely (no probe, no GPU embedding).
        prev={}
        if isinstance(baseline,dict) and baseline.get('source')==str(src) and baseline.get('model_identity')==model_identity and (baseline.get('settings') or {}).get('sample_seconds')==(settings or {}).get('sample_seconds'):
            prev={e['fingerprint']['path']:e for e in baseline.get('files',[]) if isinstance(e,dict) and isinstance(e.get('fingerprint'),dict) and 'blocks' in e}
        rid=uuid.uuid4().hex;tmp=self.root/(rid+'.tmp');tmp.mkdir(exist_ok=False); entries=[];reused=0
        try:
            for n,p in enumerate(files):
                if cancel and cancel():raise InterruptedError('index/cancel')
                fp=self._fp(p)
                old=prev.get(fp['path'])
                if old is not None and old.get('fingerprint')==fp:
                    entries.append(copy.deepcopy(old));reused+=1
                    if progress:progress(n+1,len(files),reused)
                    continue
                duration=probe_video(p,self.ffprobe) if not getattr(self.embedder,'synthetic',False) else 10.0
                blocks=[];start=0.0
                while start<duration-1e-6:
                    sec=min(10.0,duration-start);clip=tmp/f'clip-{n}-{len(blocks)}.mp4'
                    if getattr(self.embedder,'synthetic',False):vec=self.embedder.encode_media({'video':[str(p)],'start':start,'seconds':sec,'sha256':fp['sha256']})
                    else:
                        gpu=[str(self.ffmpeg),'-nostdin','-n','-v','error','-hwaccel','cuda','-hwaccel_output_format','cuda','-ss',str(start),'-t',str(sec),'-i',str(p),'-map','0:v:0','-an','-vf','scale_cuda=-2:480','-c:v','h264_nvenc','-preset','p1','-cq','28',str(clip)]
                        cpu=[str(self.ffmpeg),'-nostdin','-n','-v','error','-ss',str(start),'-t',str(sec),'-i',str(p),'-map','0:v:0','-an','-vf','scale=-2:480','-c:v','libx264','-preset','ultrafast','-crf','28',str(clip)]
                        r=subprocess.run(gpu,capture_output=True,text=True,timeout=max(60,int(sec*10)))
                        if r.returncode or not clip.is_file():
                            try:clip.unlink()
                            except OSError:pass
                            r=subprocess.run(cpu,capture_output=True,text=True,timeout=max(60,int(sec*10)))
                        if r.returncode or not clip.is_file():raise ValueError(f'index/window: {p.name} {start}: {r.stderr[-400:]}')
                        vec=self.embedder.encode_media({'video':[str(clip)]});clip.unlink()
                    blocks.append({'start':start,'seconds':sec,'vector':norm(vec)});start+=sec
                entries.append({'fingerprint':fp,'duration':duration,'blocks':blocks,'vector':norm([sum(b['vector'][i] for b in blocks)/len(blocks) for i in range(len(blocks[0]['vector']))])})
                if progress:progress(n+1,len(files),reused)
            rec={'id':rid,'product_id':product_id,'source':str(src),'settings':copy.deepcopy(settings),'model_identity':model_identity,'synthetic':bool(getattr(self.embedder,'synthetic',False)),'count':len(entries),'reused':reused,'files':entries,'created_at':time.time()}
            now=[self._fp(p) for p in self._files(src)]
            if not expected_files_unchanged([e['fingerprint'] for e in entries],now):
                raise ValueError('index/source changed during build')
            (tmp/'manifest.json').write_text(json.dumps(rec,ensure_ascii=False,allow_nan=False),encoding='utf8');os.replace(tmp,self.root/rid);return rec
        except BaseException:
            import shutil
            shutil.rmtree(tmp);raise
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
