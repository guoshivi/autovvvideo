"""Real FFmpeg/FDK render, strict measured QA, and no-overwrite NAS publication."""
from pathlib import Path
import os,json,uuid,math,wave,shutil,hashlib,subprocess,io,copy,sys,ctypes
import numpy as np
from .media import sha256,validate_report,correlate_pcm
from .verify import verify_media,read_wav,VerificationFailure
from .process import run_owned,Cancelled
from .browser import reject_links,overlaps
from .selection import VariantSelector,shot_usage
from .pacing import SENTENCE_GAP_SECONDS,voice_slot_seconds
from reference.core.videopipe.caption import drawtext_filter,wrap_caption
from .filters import ffmpeg_filter

PHASE_PREFIXES=(0,64,128,192,256,384,512,640,768,896)
MIX_SOFT_START=0.79  # soft-knee start (linear), below MIX_CEILING
MIX_CEILING=0.82     # soft asymptote for the mixed master; observed FDK decode overshoot up to +0.81dB across phases, keeps margin under the -0.5dBFS QA bound
MIX_PRE_CAP=1.35     # only grossly-hot mixes (BGM track decoding over 0dBFS) attenuate the bed at all

def _filters_python(config):
    """Resolve the isolated optional filters interpreter."""
    configured=str(getattr(config,'filters_python','') or '').strip()
    candidates=[Path(configured)] if configured else []
    root=Path(getattr(config,'project_src','.') or '.')
    candidates += [root/'.runtime/filters/Scripts/python.exe',root/'.runtime/filters/bin/python']
    for candidate in candidates:
        if candidate.is_file():return str(candidate)
    raise ValueError('人物轻美颜需要独立 filters Python（配置 filters_python 或 .runtime/filters），当前未安装')
def _wav(path,pcm):
    x=np.asarray(pcm,dtype=np.float64)
    if x.ndim!=1 or not np.isfinite(x).all() or np.max(np.abs(x),initial=0)>=1:raise ValueError('render/PCM invalid or clipped')
    with Path(path).open('xb') as raw:
        with wave.open(raw,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(np.rint(x*32767).astype('<i2').tobytes())
def _command(cmd,jobdir,timeout,cancel):return run_owned([str(x) for x in cmd],cwd=jobdir,timeout=timeout,cancel=cancel,log_dir=jobdir)
def _probe_duration(ffprobe,path):
    r=subprocess.run([ffprobe,'-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=60)
    if r.returncode:raise ValueError(f'render/probe: {r.stderr[-300:]}')
    return float(json.loads(r.stdout)['format']['duration'])
def _filter(settings,caption_file,font_file):
    vf='scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1'
    if settings['dedup']=='light':vf+=',eq=contrast=1.012:saturation=1.015'
    if settings['dedup']=='double':
        # Text and logos must keep their reading direction.  A tiny geometric
        # crop plus restrained colour shift creates a distinct render without
        # horizontal/vertical flips or a 180° rotation.
        vf+=',scale=1102:1960,crop=1080:1920,eq=contrast=1.025:saturation=1.03'
    visual=ffmpeg_filter(settings.get('visual_filter','original'),settings.get('visual_filter_strength',.35))
    if visual:vf+=','+visual
    if caption_file:vf+=','+drawtext_filter(caption_file,font_file,settings['subtitle_size'],settings['subtitle_bottom'],settings.get('subtitle_border',3))
    return vf

def _card_filter():
    return 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1'

def _build_card(config,d,name,entry,cancel):
    """Build one intro/outro card clip (image or trimmed video) at 1080x1920/30."""
    p=Path(entry['path']);reject_links(p)
    if not p.is_file():raise ValueError(f'render/{name}: source missing')
    if entry.get('sha256') and sha256(p)!=entry.get('sha256'):raise ValueError(f'render/{name}: source changed')
    kind=entry.get('kind');frames=max(1,round(float(entry.get('seconds') or 0)*30))
    dest=d/f'card_{name}.mp4'
    base=[str(config.ffmpeg),'-nostdin','-n','-v','error']
    tail=['-vf',_card_filter(),'-r','30','-c:v','libx264','-preset','veryfast','-crf','20','-frames:v',str(frames),'-pix_fmt','yuv420p',str(dest)]
    if kind=='image':
        cmd=base+['-loop','1','-i',str(p),'-t',f'{frames/30:.6f}']+tail
    elif kind=='video':
        cmd=base+['-ss','0','-t',f'{frames/30:.6f}','-i',str(p),'-an']+tail
    else:raise ValueError(f'render/{name}: kind invalid')
    _command(cmd,d,config.max_stage_seconds,cancel)
    if not dest.is_file():raise ValueError(f'render/{name}: card missing')
    measured=round(_probe_duration(config.ffprobe,dest)*30)
    if measured<1:raise ValueError(f'render/{name}: card empty')
    return dest,measured

def _bind_voice_sources(job,jobdir):
    if not job['settings']['voice']:return ()
    bindings=[]
    for i,entry in enumerate(job.get('voices',[])):
        if not isinstance(entry,dict):raise ValueError(f'render/voice receipt sentence={i} invalid object')
        receipt=copy.deepcopy(entry)
        if type(receipt.get('index')) is not int or receipt['index']!=i or type(receipt.get('samples')) is not int or receipt['samples']<=0:
            raise ValueError(f'render/voice receipt sentence={i} index/samples invalid')
        name=receipt.get('file')
        if type(name) is not str or name!=f'voice_{i:03d}.wav':raise ValueError(f'render/voice receipt sentence={i} basename invalid')
        path=Path(jobdir)/name;reject_links(path);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=receipt.get('sha256'):raise ValueError(f'render/voice receipt sentence={i} SHA invalid')
        pcm=read_wav(io.BytesIO(raw))
        if len(pcm)!=receipt['samples']:raise ValueError(f'render/voice receipt sentence={i} sample count changed')
        pcm.setflags(write=False);bindings.append((path,receipt,pcm))
    return tuple(bindings)

def render_video(job,config,jobdir,cancel=None,progress=None,_voice_binding=None,_return_beauty=False):
    """job snapshot has immutable lines, selected candidates and source fingerprints."""
    d=Path(jobdir);s=job['settings'];lines=job['lines'];mapping=job['mapping'];voice=job.get('voices',[])
    if len(mapping)!=len(lines) or (s['voice'] and len(voice)!=len(lines)):raise ValueError('render/count: sentence/mapping/voice mismatch')
    if not lines:raise ValueError('render/script: empty')
    bound=_bind_voice_sources(job,d) if _voice_binding is None else _voice_binding
    if s['voice'] and len(bound)!=len(lines):raise ValueError('render/voice receipt count mismatch')
    # Cross-batch allocation is frozen in the queue-reordered mapping.  Keep
    # VariantSelector's legacy per-film variant semantics intact.
    selector=VariantSelector(0 if job.get('selection_context') else job.get('variant',0),int(s.get('shot_reuse') or 0))
    clips=[];timeline=[];total_frames=0;voice_wavs=[];beauty_reports=[]
    intro=s.get('intro');outro=s.get('outro')
    if intro:
        card,intro_frames=_build_card(config,d,'intro',intro,cancel)
        clips.append(card);total_frames+=intro_frames
    for i,(line,candidates) in enumerate(zip(lines,mapping)):
        if cancel and cancel():raise Cancelled('render/cancel')
        if not candidates:raise ValueError(f'render/candidates sentence={i} required>=1')
        wav=bound[i][0] if s['voice'] else None
        vpcm=bound[i][2] if wav else []
        if wav:
            vpcm=bound[i][2]
            # Rhythm: reserve the measured voice plus SENTENCE_GAP_SECONDS; the
            # shared slot rule keeps short lines readable without long dead air.
            voice_wavs.append(wav);want=voice_slot_seconds(len(vpcm)/24000)
        else:want=max(2.0,min(8,max(1.8,len(line)/5)))
        frames=math.ceil(want*30);required=frames/30;cap=None
        if s['subtitles']:
            cap=d/f'cap_{i:03d}.txt';cap.write_text(wrap_caption(line),encoding='utf8')
        remaining=frames;local=[];turn=0;chosen=[];cuts=[];sentence_start=total_frames/30
        picker=selector.new_sentence(candidates)
        while remaining>0:
            c=selector.pick(picker);chosen.append(c);p=Path(c['path']);reject_links(p)
            if not p.is_file() or sha256(p)!=c['source_sha256']:raise ValueError(f'render/source SHA sentence={i} changed')
            sec=float(c['seconds']);start=float(c['start'])
            nframes=min(remaining,max(1,math.floor(sec*30)));t=nframes/30
            dest=d/f'cut_{i:03d}_{turn:03d}.mp4';vf=_filter(s,None if s.get('beauty') else (str(cap) if cap else None),Path(config.font_path).name if cap and not s.get('beauty') else None)
            # Copy font to controlled ASCII basename, avoiding path escaping in filtergraph.
            if cap and not (d/Path(config.font_path).name).exists():shutil.copyfile(config.font_path,d/Path(config.font_path).name)
            _command([config.ffmpeg,'-nostdin','-n','-v','error','-ss',f'{start:.6f}','-t',f'{t:.6f}','-i',str(p),'-vf',vf,'-an','-c:v','libx264','-preset','veryfast','-crf','20','-r','30','-frames:v',str(nframes),'-pix_fmt','yuv420p',str(dest)],d,config.max_stage_seconds,cancel)
            if not dest.is_file():raise ValueError(f'render/cut sentence={i} missing')
            if s.get('beauty'):
                # Beauty is an explicit opt-in worker.  It streams frames
                # through a local OpenCV ROI pass and then re-encodes H.264;
                # subtitles are added only after the face pass below.
                req=d/f'beauty-{i:03d}-{turn:03d}.json';raw=d/f'beauty-{i:03d}-{turn:03d}.mp4';rep=d/f'beauty-{i:03d}-{turn:03d}.report.json'
                req.write_text(json.dumps({'source':str(dest),'output':str(raw),'report':str(rep),'strength':s.get('beauty_strength',.25),'fps':30,'width':1080,'height':1920,'expected_frames':nframes},ensure_ascii=False),encoding='utf8')
                worker=Path(getattr(config,'project_src','.') or '.').absolute()/'workbench'/'beauty_worker.py'
                _command([_filters_python(config),str(worker),str(req)],d,config.max_stage_seconds,cancel)
                if not raw.is_file() or not rep.is_file():raise ValueError('render/beauty worker produced no output')
                beauty_meta=json.loads(rep.read_text(encoding='utf8'))
                if int(beauty_meta.get('faces_detected') or 0)==0:
                    # Preserve the already validated original cut when no face
                    # exists.  The report records that beauty was enabled but
                    # no pixels were changed and no beauty output was used.
                    beauty_meta['encoded_path_used']=False;beauty_meta['subtitle_encoded']=bool(cap);beauty_reports.append(beauty_meta)
                    if cap:
                        captioned=d/f'captioned-{i:03d}-{turn:03d}.mp4'
                        _command([config.ffmpeg,'-nostdin','-n','-v','error','-i',str(dest),'-vf',drawtext_filter(str(cap),Path(config.font_path).name,s['subtitle_size'],s['subtitle_bottom'],s.get('subtitle_border',3)),'-an','-c:v','libx264','-preset','veryfast','-crf','20','-r','30','-frames:v',str(nframes),'-pix_fmt','yuv420p',str(captioned)],d,config.max_stage_seconds,cancel)
                        dest.unlink(missing_ok=True);captioned.replace(dest)
                    raw.unlink(missing_ok=True);req.unlink(missing_ok=True);rep.unlink(missing_ok=True)
                    local.append(dest);clips.append(dest);remaining-=nframes;turn+=1
                    cut_start=sentence_start+sum(x['used_seconds'] for x in cuts)
                    cuts.append({'candidate_ref':{'source_sha256':c.get('source_sha256'),'index_id':c.get('index_id'),'source_start':start,'seconds':sec},'used_seconds':round(nframes/30,6),'frames':nframes,'output_start':round(cut_start,6),'output_end':round(cut_start+nframes/30,6)})
                    continue
                beauty_out=d/f'beauty-final-{i:03d}-{turn:03d}.mp4'
                tail_vf=drawtext_filter(str(cap),Path(config.font_path).name,s['subtitle_size'],s['subtitle_bottom'],s.get('subtitle_border',3)) if cap else 'null'
                _command([config.ffmpeg,'-nostdin','-n','-v','error','-i',str(raw),'-vf',tail_vf,'-an','-c:v','libx264','-preset','veryfast','-crf','20','-r','30','-frames:v',str(nframes),'-pix_fmt','yuv420p',str(beauty_out)],d,config.max_stage_seconds,cancel)
                dest.unlink(missing_ok=True);beauty_out.replace(dest)
                beauty_meta['encoded_path_used']=True;beauty_reports.append(beauty_meta)
            local.append(dest);clips.append(dest);remaining-=nframes;turn+=1
            cut_start=sentence_start+sum(x['used_seconds'] for x in cuts)
            cuts.append({'candidate_ref':{'source_sha256':c.get('source_sha256'),'index_id':c.get('index_id'),'source_start':start,'seconds':sec},'used_seconds':round(nframes/30,6),'frames':nframes,'output_start':round(cut_start,6),'output_end':round(cut_start+nframes/30,6)})
            if 0<remaining<=3:
                # A ≤3-frame sliver tail (≤0.10s, invisible on screen) is not
                # worth cutting: tiny fragments destabilize concat frame
                # bookkeeping (observed +2 phantom frame slots on a 17s film
                # -> avg_frame_rate 29.885 -> QA rejection).  Drop it: the slot
                # ends a hair early and the voice-margin check below still
                # guards the floor.
                remaining=0
            if turn>10000:raise ValueError('render/window count exceeded')
        if wav:_assert_voice_sources([wav],[bound[i][1]])
        segment_frames=sum(round(_probe_duration(config.ffprobe,x)*30) for x in local)
        slack=frames-segment_frames
        if slack<0 or slack>3:raise ValueError(f'render/frames sentence={i} measured={segment_frames} required={frames}')
        start_time=total_frames/30;total_frames+=segment_frames;end_time=total_frames/30
        # ceil-to-30fps plus a possible three-frame sliver leaves at least ~.08s;
        # .07s is the conservative guard against clipping or overlap.
        if wav and end_time-start_time<len(vpcm)/24000+.07:raise ValueError(f'render/voice margin sentence={i}')
        shortage=len({(c.get('source_sha256'),round(float(c.get('start') or 0),1)) for c in chosen})<len(chosen)
        timeline.append({'index':i,'start':start_time,'end':end_time,'voice_samples':len(vpcm) if wav else 0,'segments':[p.name for p in local],'sources':chosen,'cuts':cuts,'selection_warning':'candidate_shortage/cap_overrun' if shortage else None})
        if progress:progress(i+1,len(lines))
    if outro:
        card,outro_frames=_build_card(config,d,'outro',outro,cancel)
        clips.append(card);total_frames+=outro_frames
        # The trailing card belongs to the film's tail: fold it into the last
        # sentence interval so duration/QA contracts stay on one timeline.
        timeline[-1]['end']=total_frames/30
    listing=d/'concat.txt';listing.write_text(''.join("file '"+p.name+"'\n" for p in clips),encoding='utf8')
    draft=d/'draft.mp4';_command([config.ffmpeg,'-nostdin','-n','-v','error','-f','concat','-safe','0','-i',str(listing),'-an','-c:v','copy',str(draft)],d,config.max_stage_seconds,cancel)
    if abs(_probe_duration(config.ffprobe,draft)-timeline[-1]['end'])>1/30+.001:raise ValueError('render/draft duration mismatch')
    master=np.zeros(round(timeline[-1]['end']*24000),dtype=np.float64)
    if s['voice']:
        for i,t in enumerate(timeline):
            pcm=bound[i][2];start=round(t['start']*24000)
            if start+len(pcm)>len(master):raise ValueError(f'render/voice truncate sentence={i}')
            master[start:start+len(pcm)]=pcm
    master.setflags(write=False)
    if _return_beauty:return draft,timeline,master,voice_wavs,beauty_reports
    return draft,timeline,master,voice_wavs

def _assert_voice_sources(wavs, receipts):
    if len(wavs)!=len(receipts):raise ValueError('render/voice receipt count changed after initial binding')
    for i,(wav,receipt) in enumerate(zip(wavs,receipts)):
        if sha256(wav)!=receipt.get('sha256') or len(read_wav(wav))!=receipt.get('samples'):
            raise ValueError(f'render/voice receipt sentence={i} source changed after initial binding')

def _build_cover(config,d,final,cover,cancel):
    """Build the vertical cover image (feature 8). Returns metadata or None."""
    mode=cover.get('mode');out=d/'cover.jpg';t=None
    if mode=='file':
        p=Path(cover['path']);reject_links(p)
        if not p.is_file():raise ValueError('render/cover: source missing')
        if sha256(p)!=cover.get('sha256'):raise ValueError('render/cover: source changed')
        cmd=[config.ffmpeg,'-nostdin','-n','-v','error','-i',str(p),'-frames:v','1','-vf','scale=1080:-2','-q:v','3',str(out)]
    elif mode in ('auto','frame'):
        dur=_probe_duration(config.ffprobe,final)
        t=1.0 if mode=='auto' else float(cover.get('seconds') or 0)
        t=min(max(0.0,t),max(0.0,dur-.1))
        cmd=[config.ffmpeg,'-nostdin','-n','-v','error','-ss',f'{t:.3f}','-i',str(final),'-frames:v','1','-vf','scale=1080:-2','-q:v','3',str(out)]
    else:raise ValueError('render/cover: mode invalid')
    _command(cmd,d,config.max_stage_seconds,cancel)
    if not out.is_file():raise ValueError('render/cover: extraction failed')
    info={'mode':mode,'sha256':sha256(out),'bytes':out.stat().st_size}
    if t is not None:info['seconds']=t
    return info

def _bgm_master(config,jobdir,bgm_path,length,volume,cancel):
    out=Path(jobdir)/'bgm.f32'
    _command([config.ffmpeg,'-nostdin','-n','-v','error','-stream_loop','-1','-i',str(bgm_path),'-t',str(length/24000),'-ac','1','-ar','24000','-c:a','pcm_f32le','-f','f32le',str(out)],jobdir,config.max_stage_seconds,cancel)
    pcm=np.fromfile(out,dtype='<f4').astype(np.float64)
    if len(pcm)<length:raise ValueError('render/BGM decoded shorter than timeline')
    return pcm[:length]*volume

def _fit_mix_gain(voice,music,ceiling):
    """Largest k in [0,1] such that max|voice + music*k| <= ceiling.
    12-step binary search over the monotone peak in k."""
    if float(np.max(np.abs(voice+music)))<=ceiling:return 1.0
    lo,hi=0.0,1.0
    for _ in range(12):
        mid=(lo+hi)/2
        if float(np.max(np.abs(voice+music*mid)))<=ceiling:lo=mid
        else:hi=mid
    return lo

def _soft_limit_mix(x):
    """Soft-knee limiter for the final mix: samples up to MIX_SOFT_START pass
    untouched; louder ones are smoothly compressed toward MIX_CEILING
    (tanh knee).  Rare voice+BGM coincidences get inaudibly rounded instead of
    forcing the whole BGM bed to be muted to protect one sample."""
    a=np.abs(x)
    if float(a.max(initial=0))<=MIX_SOFT_START:return x
    r=MIX_CEILING-MIX_SOFT_START
    y=x.copy();m=a>MIX_SOFT_START
    y[m]=np.sign(x[m])*(MIX_SOFT_START+r*np.tanh((a[m]-MIX_SOFT_START)/r))
    return y

def _encode_mux(config,draft,master,phase,attempt,cancel):
    attempt=Path(attempt);attempt.mkdir(parents=True,exist_ok=False);wav=attempt/'master.wav';_wav(wav,np.concatenate([np.zeros(phase),master]))
    audio=attempt/'audio.m4a';_command([config.fdk_encoder,'-b','136000','-w','12000','-a','1','-G','2','-o',str(audio),str(wav)],attempt,config.max_stage_seconds,cancel)
    final=attempt/'final.mp4';_command([config.ffmpeg,'-nostdin','-n','-v','error','-i',str(draft),'-itsoffset',str(-phase/24000),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-c','copy','-movflags','+faststart',str(final)],attempt,config.max_stage_seconds,cancel)
    return final

def encode_with_phase_retry(config,draft,master,timeline,voice_wavs,jobdir,mode,cancel=None,eos_receipts=None,source_receipts=None,frozen_voice_pcm=None,expected_engine='qwen'):
    failures=[]
    for phase in PHASE_PREFIXES:
        if cancel and cancel():raise Cancelled('codec/cancel')
        try:
            final=_encode_mux(config,draft,master,phase,Path(jobdir)/f'codec-{phase:04d}',cancel)
            if source_receipts is not None:
                _assert_voice_sources(voice_wavs,source_receipts)
            report=verify_media(final,config.ffmpeg,config.ffprobe,timeline,master,voice_wavs,mode,cancel,config.max_stage_seconds,eos_receipts,frozen_voice_pcm=frozen_voice_pcm,expected_engine=expected_engine)
            report['codec_phase_samples']=phase;report['codec_attempts']=len(failures)+1
            return final,report
        except VerificationFailure as e:
            failures.append(e.details)
            # Only original-PCM codec mismatch may try a new encoder frame phase.
            if e.details['check']!='PCM correlation':raise
    raise VerificationFailure('codec','phase search',failures,'one of bounded phases passes full original-baseline QA')

def render_job(job,config,jobdir,cancel=None,progress=None):
    d=Path(jobdir);bound=_bind_voice_sources(job,d);draft,timeline,voice_master,wavs,beauty_reports=render_video(job,config,d,cancel,progress,_voice_binding=bound,_return_beauty=True);s=job['settings']
    receipts=[v[1] for v in bound];frozen_pcm=tuple(v[2] for v in bound)
    engine=s.get('voice_engine','qwen')
    eos_receipts=[v.get('eos') for v in receipts] if s['voice'] else None
    if s['voice']:
        intermediate,intermediate_report=encode_with_phase_retry(config,draft,voice_master,timeline,wavs,d/'voice-intermediate','voice',cancel,eos_receipts,receipts,frozen_pcm,engine)
        _assert_voice_sources(wavs,receipts)
        (d/'voice-intermediate-report.json').write_text(json.dumps(intermediate_report,ensure_ascii=False),encoding='utf8')
    mix_fit=None
    if s['bgm']:
        bgm=Path(s['bgm_path']);reject_links(bgm)
        if sha256(bgm)!=job['bgm_sha256']:raise ValueError('render/BGM SHA changed')
        music=_bgm_master(config,d,bgm,len(voice_master),s['bgm_volume'],cancel)
        master=voice_master+music
        peak=float(np.max(np.abs(master)))
        if peak>MIX_PRE_CAP:
            # The BGM track itself is grossly hot (observed: decoding at
            # +2dBFS).  Pull only the music down to the pre-limit cap; normal
            # mixes keep the user's requested level, and the soft limiter
            # below catches rare coincidences - fitting the bed to one
            # worst-case sample made the BGM inaudible (user: "BGM 没声音").
            gain=_fit_mix_gain(voice_master,music,MIX_PRE_CAP)
            if gain<0.01:raise ValueError('render/mix clips; lower BGM volume')
            music=music*gain;master=voice_master+music
            mix_fit={'bgm_gain':round(gain,4),'bgm_attenuated_db':round(20*math.log10(gain),1)}
        limited=_soft_limit_mix(master)
        if limited is not master:
            mix_fit=mix_fit or {}
            mix_fit['soft_limit']={'start':MIX_SOFT_START,'ceiling':MIX_CEILING}
        master=limited
        mode='mixed' if s['voice'] else 'bgm'
    else:master=voice_master;mode='voice' if s['voice'] else 'silent'
    if mode=='silent':
        final=d/'final.mp4';shutil.copyfile(draft,final);report=verify_media(final,config.ffmpeg,config.ffprobe,timeline,None,None,'silent',cancel,config.max_stage_seconds)
    else:final,report=encode_with_phase_retry(config,draft,master,timeline,wavs if s['voice'] else None,d/'final-codec',mode,cancel,eos_receipts,receipts if s['voice'] else None,frozen_pcm if s['voice'] else None,engine)
    if s['voice']:
        _assert_voice_sources(wavs,receipts)
    report['timeline']=timeline;report['source_index_id']=job['index_id'];report['input_sha256']=job['input_sha256'];report['voice_wav_sha256']=[v['sha256'] for v in receipts];report['bgm_sha256']=job.get('bgm_sha256');report['eos_receipts']=eos_receipts;report['voice_engine']=engine;report['voice_completion']='natural_stop' if s['voice'] else 'not_applicable'
    report['speed']=s.get('speed',1.0)
    if mix_fit:report['mix_fit']=mix_fit
    report['shot_usage']=shot_usage(timeline)
    if job.get('selection_context'):report['selection_context']=copy.deepcopy(job['selection_context'])
    if s.get('beauty'):report['beauty']={'enabled':True,'frames':sum(x.get('frames',0) for x in beauty_reports),'frames_with_faces':sum(x.get('frames_with_faces',0) for x in beauty_reports),'faces_detected':sum(x.get('faces_detected',0) for x in beauty_reports),'no_op':not any(x.get('faces_detected',0) for x in beauty_reports)}
    cover_meta=s.get('cover')
    if cover_meta:
        info=_build_cover(config,d,final,cover_meta,cancel)
        if info:report['cover']=info
    (d/'report.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False),encoding='utf8')
    validate_report(report,final)
    if s['voice']:_assert_voice_sources(wavs,receipts)
    return final,report

def export_validated(local_file,nas_root,job_id,report,authorization=None,cover_local=None,dest_name=None):
    from .fs_authority import identity_chain, verify_lease, windows_noreplace, UnsupportedNative
    class ExportCommitError(IOError):
        def __init__(self,message,receipt=None):super().__init__(message);self.after_replace=True;self.receipt=receipt or {}
    src=Path(local_file);got=validate_report(report,src);root=Path(nas_root).absolute();reject_links(root)
    if overlaps(src,root):raise ValueError('export/source-output overlap')
    root_chain=identity_chain(root)
    if authorization is not None:
        if authorization.get('path') and Path(authorization['path']).absolute()!=root:raise ValueError('export selection path changed')
        verify_lease(root,authorization.get('chain',()))
    base=dest_name if dest_name else f'job-{job_id}'
    destdir=root/base
    if destdir.exists():
        base=f'{base}_{job_id[:6]}';destdir=root/base
    stage=root/f'.{base[:40]}-{uuid.uuid4().hex}.staging'
    cover_info=report.get('cover') if isinstance(report,dict) else None
    cover_src=Path(cover_local) if cover_local else None
    if cover_info and cover_src is None:raise ValueError('export/cover: metadata present but cover file missing')
    if cover_src is not None and not cover_info:raise ValueError('export/cover: cover file present but no metadata')
    cmeta=cover_info or {}
    verify_lease(root,root_chain)
    stage.mkdir(exist_ok=False)
    stage_id=(stage.stat().st_dev,stage.stat().st_ino);published=False
    # The new directory is only accepted if it was created below the leased
    # root.  If an injected mkdir swaps an ancestor, clean only this inode.
    try: verify_lease(root,root_chain)
    except Exception:
        try:
            if stage.exists() and (stage.stat().st_dev,stage.stat().st_ino)==stage_id:shutil.rmtree(stage)
        except OSError: pass
        raise
    def native_noreplace(a,b):
        if os.name=='nt':
            return windows_noreplace(a,b,root,root_chain)
        if sys.platform=='darwin':
            libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,'renameatx_np',None)
            if fn is None:raise UnsupportedNative('native no-replace rename unavailable')
            fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
            if fn(-2,os.fsencode(str(a)),-2,os.fsencode(str(b)),4)!=0:
                e=ctypes.get_errno();raise OSError(e,os.strerror(e),str(b))
            return
        if sys.platform.startswith('linux'):
            libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,'renameat2',None)
            if fn is None:raise UnsupportedNative('native no-replace rename unavailable')
            fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
            if fn(-100,os.fsencode(str(a)),-100,os.fsencode(str(b)),1)!=0:
                e=ctypes.get_errno();raise OSError(e,os.strerror(e),str(b))
            return
        raise UnsupportedNative('native no-replace rename unsupported on this platform')
    try:
        temp=stage/f'{base}.mp4'
        with src.open('rb') as f,temp.open('xb') as g:
            shutil.copyfileobj(f,g,1024*1024);g.flush();os.fsync(g.fileno())
        if sha256(temp)!=got['sha256'] or temp.stat().st_size!=got['bytes']:raise ValueError('export/staging readback mismatch')
        if cover_src is not None:
            if not cover_src.is_file():raise ValueError('export/cover file missing')
            tempc=stage/'cover.jpg'
            with cover_src.open('rb') as f,tempc.open('xb') as g:
                shutil.copyfileobj(f,g,1024*1024);g.flush();os.fsync(g.fileno())
            if sha256(tempc)!=cmeta.get('sha256') or tempc.stat().st_size!=cmeta.get('bytes'):raise ValueError('export/cover staging readback mismatch')
        report_path=stage/'report.json';raw=json.dumps(report,ensure_ascii=False).encode('utf8')
        with report_path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        verify_lease(root,root_chain)
        if destdir.exists(): raise FileExistsError(destdir)
        native_noreplace(stage,destdir)
        published=True
        try: verify_lease(root,root_chain)
        except Exception as e:
            raise ExportCommitError('publication occurred but output authority changed',{'stage':str(stage),'destination':str(destdir),'sha256':got['sha256']}) from e
        dest=destdir/f'{base}.mp4'
        try:
            if sha256(dest)!=got['sha256'] or dest.stat().st_size!=got['bytes']:raise ValueError('export/final readback mismatch')
        except Exception as e:
            raise ExportCommitError('publication occurred but final readback failed',{'stage':str(stage),'destination':str(destdir),'sha256':got['sha256']}) from e
        if cover_src is not None:
            destc=destdir/'cover.jpg'
            try:
                if sha256(destc)!=cmeta.get('sha256') or destc.stat().st_size!=cmeta.get('bytes'):raise ValueError('export/cover readback mismatch')
            except Exception as e:
                raise ExportCommitError('publication occurred but cover readback failed',{'stage':str(stage),'destination':str(destdir),'sha256':cmeta.get('sha256')}) from e
        return dest,got
    finally:
        if not published and stage.exists():
            try:
                st=stage.stat()
                if (st.st_dev,st.st_ino)==stage_id:shutil.rmtree(stage)
            except OSError:pass

def export_numbered(local_file,out_root,report,short_title,authorization=None,now=None):
    from datetime import datetime
    from .fs_authority import identity_chain,verify_lease,lexical_under,AuthorityError
    class ExportCommitError(IOError):
        def __init__(self,message,receipt=None):super().__init__(message);self.after_replace=True;self.receipt=receipt or {}
    src=Path(local_file);got=validate_report(report,src);root=Path(out_root).absolute();reject_links(root)
    if not root.is_dir():raise ValueError('export/output root missing')
    if overlaps(src,root):raise ValueError('export/source-output overlap')
    root_chain=identity_chain(root)
    if authorization is not None:
        if authorization.get('path') and Path(authorization['path']).absolute()!=root:raise ValueError('export selection path changed')
        verify_lease(root,authorization.get('chain',()))
    short=_safe_segment(short_title);t_dir=root/short;d_dir=t_dir/(now or datetime.now()).strftime('%Y%m%d')
    parent=root;chain=root_chain
    for child in (t_dir,d_dir):
        verify_lease(parent,chain);lexical_under(child,root)
        before=identity_chain(child) if child.exists() else None
        child.mkdir(exist_ok=True)
        verify_lease(parent,chain);lexical_under(child,root)
        chain=verify_lease(child,before);parent=child
    date_chain=chain
    def check_chain():
        lexical_under(d_dir,root);verify_lease(d_dir,date_chain)
    def owned(path):
        if stage_id is None or path is None:return False
        try:
            st=path.lstat();return (st.st_dev,st.st_ino)==stage_id
        except OSError:return False
    check_chain();n=_next_number(d_dir,short);check_chain()
    # A short staging basename also accommodates eighty CJK characters in the
    # final name without exceeding the filesystem's component byte limit.
    stage=d_dir/f'.export-{uuid.uuid4().hex}.staging'
    stage_id=None;published=False;dest=None;cleanup_fd=None;phase='publication verification failed'
    try:
        if os.name!='nt':
            # Only cleanup needs a directory descriptor: after an ancestor swap
            # it can remove our inode, never a file through the replacement path.
            cleanup_fd=os.open(d_dir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            st=os.fstat(cleanup_fd)
            if (st.st_dev,st.st_ino)!=tuple(date_chain[0][1:3]):raise AuthorityError('export/date directory identity changed')
        check_chain()
        with stage.open('xb') as g:
            st=os.fstat(g.fileno());stage_id=(st.st_dev,st.st_ino)
            check_chain()
            with src.open('rb') as f:shutil.copyfileobj(f,g,1024*1024)
            check_chain();g.flush();os.fsync(g.fileno())
        check_chain()
        if not owned(stage):raise AuthorityError('export/staging identity changed')
        if sha256(stage)!=got['sha256'] or stage.stat().st_size!=got['bytes']:raise ValueError('export/staging readback mismatch')
        check_chain()
        for _ in range(200):
            check_chain()
            if not owned(stage):raise AuthorityError('export/staging identity changed')
            dest=d_dir/f'{short} {n}.mp4'
            try:_nv_rename_file(stage,dest,d_dir,date_chain)
            except OSError as e:
                # A wrapper can raise after the syscall. Preserve that effect
                # rather than retrying or claiming that nothing was published.
                if getattr(e,'after_replace',False) or owned(dest):
                    published=True;raise
                check_chain()
                if e.errno in (17,80,183) or getattr(e,'winerror',None) in (80,183):n+=1;continue
                raise
            published=True;break
        else:raise FileExistsError('export/numbering collision retry limit reached')
        phase='output authority changed';check_chain()
        phase='final readback failed'
        if not owned(dest) or sha256(dest)!=got['sha256'] or dest.stat().st_size!=got['bytes']:raise ValueError('export/final readback mismatch')
        check_chain()
        if not owned(dest):raise AuthorityError('export/final identity changed')
        return dest,got
    except Exception as e:
        receipt={'stage':str(stage),'destination':str(dest) if dest else None,'sha256':got['sha256'],'bytes':got['bytes'],'stage_identity':stage_id}
        if published or getattr(e,'after_replace',False) or owned(dest):
            published=True
            raise ExportCommitError('publication occurred but '+phase,receipt) from e
        e.after_replace=False;e.receipt=receipt
        raise
    finally:
        if stage_id is not None and not published:
            try:
                if cleanup_fd is not None:
                    st=os.stat(stage.name,dir_fd=cleanup_fd,follow_symlinks=False)
                    if (st.st_dev,st.st_ino)==stage_id:os.unlink(stage.name,dir_fd=cleanup_fd)
                else:
                    check_chain()
                    if owned(stage):stage.unlink()
            except (OSError,ValueError):pass
        if cleanup_fd is not None:os.close(cleanup_fd)

def _safe_segment(value):
    import unicodedata
    from .browser import _RESERVED
    s=''.join(c for c in str(value or '') if c not in '\\/:*?"<>|' and unicodedata.category(c) not in ('Cc','Cf','Cs')).strip().strip(' .')
    s=s[:80].rstrip(' .') or '视频'
    if s.split('.')[0].rstrip(' ').upper() in _RESERVED|{'CONIN$','CONOUT$'}:s=('_'+s)[:80].rstrip(' .')
    return s

def _next_number(directory,short):
    import re
    pattern=re.compile(re.escape(short)+r' (\d+)\.mp4',re.IGNORECASE)
    return max((int(m.group(1)) for p in directory.iterdir() if (m:=pattern.fullmatch(p.name))),default=0)+1

def _nv_rename_file(stage,dest,root,expected_chain):
    from .fs_authority import windows_noreplace,UnsupportedNative
    if os.name=='nt':
        return windows_noreplace(stage,dest,root,expected_chain)
    if sys.platform=='darwin':
        libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,'renameatx_np',None)
        if fn is None:raise UnsupportedNative('native no-replace rename unavailable')
        fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        if fn(-2,os.fsencode(str(stage)),-2,os.fsencode(str(dest)),4)!=0:
            e=ctypes.get_errno();raise OSError(e,os.strerror(e),str(dest))
        return
    if sys.platform.startswith('linux'):
        libc=ctypes.CDLL(None,use_errno=True);fn=getattr(libc,'renameat2',None)
        if fn is None:raise UnsupportedNative('native no-replace rename unavailable')
        fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
        if fn(-100,os.fsencode(str(stage)),-100,os.fsencode(str(dest)),1)!=0:
            e=ctypes.get_errno();raise OSError(e,os.strerror(e),str(dest))
        return
    raise UnsupportedNative('native no-replace rename unsupported on this platform')
