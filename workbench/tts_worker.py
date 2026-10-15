"""Offline one-shot Qwen voice process; one natural EOS observation per sentence."""
import sys,json,time,wave,hashlib,traceback,os,subprocess
from pathlib import Path
import numpy as np
from .pacing import trim_wav_edges

def _denoise_floor(arr, threshold=12):
    """Zero out sub-quantization-floor dither (<= threshold LSB ~ -68dBFS) so that
    stretches which are silent stay EXACTLY silent.  The atempo resampler leaves
    +-1..6 LSB noise in silent regions; the strict per-block QA only skips
    exact-zero blocks, so surviving sparse micro-samples (measured <=6 LSB) would
    be checked against codec quantization noise and fail.  Verified on the real
    FDK+QA chain: threshold 8+ turns the failing renders into one-phase passes."""
    a = np.asarray(arr)
    out = a.copy()
    out[np.abs(out) <= threshold] = 0
    return out

def apply_speed_wav(ffmpeg,path,speed):
    """Re-time one PCM16 wav with ffmpeg atempo (pitch preserved); returns the new sample count."""
    p=Path(path);tmp=p.with_name(p.stem+'.speed.tmp.wav')
    try:
        if tmp.exists():tmp.unlink()
    except OSError:pass
    cmd=[str(ffmpeg),'-nostdin','-n','-v','error','-i',str(p),'-filter:a',f'atempo={float(speed):.6f}','-ar','24000','-ac','1','-c:a','pcm_s16le',str(tmp)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=300)
    if r.returncode or not tmp.is_file():raise RuntimeError(f'voice/speed: atempo failed: {(r.stderr or "")[-300:]}')
    with wave.open(str(tmp),'rb') as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate())!=(1,2,24000):raise RuntimeError('voice/speed: unexpected wav format')
        arr=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').copy()
    samples=len(arr)
    if samples<=0:raise RuntimeError('voice/speed: empty output')
    arr=_denoise_floor(arr,12)
    with wave.open(str(tmp),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(arr.astype('<i2').tobytes())
    os.replace(tmp,p)
    return samples

VOICE_CHAIN=('compand=attacks=0.005:decays=0.25:points=-90/-90|-24/-18|-9/-7:gain=6,'
             'alimiter=limit=0.79:attack=5:release=80:latency=true,volume=-2dB')
VOICE_PASSES=2

def _measure_lufs(ffmpeg,wav_paths,tmpdir):
    """Integrated loudness (LUFS) of the concatenated voice set via ffmpeg loudnorm."""
    import re as _re
    tmp=Path(tmpdir)/'loudness-probe.wav'
    try:
        with wave.open(str(tmp),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000)
            for w in wav_paths:
                with wave.open(str(w),'rb') as r:f.writeframes(r.readframes(r.getnframes()))
        r=subprocess.run([str(ffmpeg),'-nostdin','-v','info','-i',str(tmp),'-af','loudnorm=print_format=json','-f','null','-'],capture_output=True,text=True,timeout=300)
        m=_re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"',r.stderr or '')
        return float(m.group(1)) if m else None
    finally:
        try:tmp.unlink()
        except OSError:pass

def _normalize_loudness(ffmpeg,out,result):
    """Reference-class voice loudness via a fixed soft-knee compand + brickwall
    limiter chain, applied VOICE_PASSES times per wav.  Measured journey on the
    real chain: raw TTS sits ~-24.5 LUFS with ~19.7dB crest while the 跑量
    reference films are squashed to ~-10.3 LUFS / ~11.6dB crest; plain gains or
    single-pass loudnorm cannot bridge that (limiter/loudnorm are peak-budget
    bound, measured stuck at -15.9..-16 regardless of target).  Each pass of
    this chain cuts ~2-3dB of crest and lifts the body: 1 pass -> ~-15.7,
    2 passes -> ~-12.1, 3 passes -> ~-10.7.  Two passes chosen: films land
    ~-13 LUFS (reference class) with sample peak pinned at -2dBFS, leaving the
    same 1.4dB QA margin under the -0.5dBFS ceiling as before.  alimiter's
    fixed 5ms lookahead delay is compensated (latency=true); silence is
    re-pinned afterwards so the exact-zero QA contract survives; shas are
    refreshed and wavs rewritten in place so every downstream contract binds
    to the new bytes."""
    import re as _re
    try:
        for v in result['voices']:
            p=out/v['file']
            cur=p
            temps=[]
            for pi in range(VOICE_PASSES):
                tmp=out/(p.stem+f'.norm{pi}.tmp.wav')
                temps.append(tmp)
                try:
                    if tmp.exists():tmp.unlink()
                except OSError:pass
                r=subprocess.run([str(ffmpeg),'-nostdin','-n','-v','error','-i',str(cur),
                                  '-filter:a',VOICE_CHAIN,
                                  '-ar','24000','-ac','1','-c:a','pcm_s16le',str(tmp)],
                                 capture_output=True,text=True,timeout=300)
                if r.returncode or not tmp.is_file():
                    raise RuntimeError(f'voice/loudness: chain failed rc={r.returncode}: {(r.stderr or "")[-200:]}')
                cur=tmp
            with wave.open(str(cur),'rb') as f:
                if (f.getnchannels(),f.getsampwidth(),f.getframerate())!=(1,2,24000):
                    raise RuntimeError('voice/loudness: unexpected wav format')
                arr=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').copy()
            samples=len(arr)
            if samples<=0:raise RuntimeError('voice/loudness: empty output')
            arr=_denoise_floor(arr,12)
            with wave.open(str(p),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(arr.astype('<i2').tobytes())
            for tmp in temps:
                try:tmp.unlink()
                except OSError:pass
            v['samples']=samples
            v['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
            v['loudness_norm']='compand_limiter_x%d'%VOICE_PASSES
        set_lufs=_measure_lufs(ffmpeg,[out/v['file'] for v in result['voices']],out)
        if set_lufs is not None:result['loudness_set']=set_lufs
    except Exception as e:
        result['loudness_warning']=str(e)[:200]

def main():
    request=Path(sys.argv[1]);out=Path(sys.argv[2]);d=json.loads(request.read_text(encoding='utf8'));result={'success':False,'voices':[]}
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():raise RuntimeError('voice/load: CUDA BF16 required')
        torch.manual_seed(42);torch.cuda.manual_seed_all(42)
        model=Qwen3TTSModel.from_pretrained(d['tts_model'],device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True)
        talker=model.model.talker;original_generate=talker.generate;observations=[]
        def observed_generate(*args,**kwargs):
            generated=original_generate(*args,**kwargs)
            seq=generated.sequences if hasattr(generated,'sequences') else generated
            if not isinstance(seq,torch.Tensor) or seq.ndim!=2:raise RuntimeError('unsupported talker generation result; EOS not verifiable')
            conf=kwargs.get('generation_config',getattr(talker,'generation_config',None))
            eos=kwargs.get('eos_token_id',getattr(conf,'eos_token_id',None))
            pad=kwargs.get('pad_token_id',getattr(conf,'pad_token_id',None))
            forced=kwargs.get('forced_eos_token_id',getattr(conf,'forced_eos_token_id',None))
            if eos is None:raise RuntimeError('actual talker EOS token unavailable')
            eos_ids=[int(v) for v in eos] if isinstance(eos,(list,tuple)) else [int(eos)]
            scores=getattr(generated,'scores',None)
            if scores is not None and len(scores):count=len(scores)
            else:
                ids=kwargs.get('input_ids');embeds=kwargs.get('inputs_embeds')
                if ids is None and args and isinstance(args[0],torch.Tensor):ids=args[0]
                if ids is not None:count=seq.shape[1]-ids.shape[1]
                elif embeds is not None:count=seq.shape[1]
                else:raise RuntimeError('cannot establish generated token count')
            rows=seq.detach().cpu().tolist()
            if count<0 or count>seq.shape[1]:raise RuntimeError('generated token count outside actual sequence')
            rows=[row[-count:] if count else [] for row in rows]
            trimmed=[]
            for row in rows:
                row=list(row)
                while row and row[-1]==pad and pad not in eos_ids:row.pop()
                trimmed.append(row)
            tails=[row[-1] if row else None for row in trimmed]
            obs={'tail_token_ids':tails,'eos_token_ids':eos_ids,'pad_token_id':pad,'generated_tokens':int(count),'sequence_shape':list(seq.shape),'padding_trimmed':[len(a)-len(b) for a,b in zip(rows,trimmed)],'forced_eos_token_id':forced}
            obs['natural_eos']=0<count<1024 and all(v in eos_ids for v in tails) and forced is None
            observations.append(obs)
            return generated
        talker.generate=observed_generate
        speed=float(d.get('speed') or 1.0)
        if not 0.85-1e-9<=speed<=1.5+1e-9:raise RuntimeError('voice/speed: out of range 0.85–1.5')
        ffmpeg=str(d.get('ffmpeg') or 'ffmpeg')
        for i,line in enumerate(d['lines']):
            before=len(observations);wavs,rate=model.generate_custom_voice(text=line,language='Chinese',speaker=d['speaker'],instruct='',max_new_tokens=1024)
            current=observations[before:]
            if len(current)!=1 or current[0]['natural_eos'] is not True:raise RuntimeError(f'voice/EOS sentence={i} measured={current} required=one natural EOS before token 1024')
            if rate!=24000 or len(wavs)!=1:raise RuntimeError(f'voice/format sentence={i} required=one 24kHz mono WAV')
            audio=np.asarray(wavs[0],dtype=np.float64)
            if audio.ndim!=1 or not len(audio) or not np.isfinite(audio).all():raise RuntimeError(f'voice/PCM sentence={i} invalid')
            peak=float(np.max(np.abs(audio)));rms=float(np.sqrt(np.mean(audio**2)))
            if peak>=1 or rms<1e-5:raise RuntimeError(f'voice/level sentence={i} peak={peak} rms={rms}')
            gain=min(1.0,10**(-2/20)/peak);pcm=np.rint(audio*gain*32767).astype('<i2');p=out/f'voice_{i:03d}.wav'
            with p.open('xb') as raw:
                with wave.open(raw,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(pcm.tobytes())
            samples=len(pcm)
            if abs(speed-1.0)>1e-9:samples=apply_speed_wav(ffmpeg,p,speed)
            result['voices'].append({'index':i,'file':p.name,'samples':samples,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'eos':current[0]})
        _normalize_loudness(ffmpeg,out,result)
        for v in result['voices']:
            samples=trim_wav_edges(out/v['file'])
            v['samples']=samples
            v['sha256']=hashlib.sha256((out/v['file']).read_bytes()).hexdigest()
        result['speed']=speed
        result['success']=True
    except Exception as e:result.update(error=str(e),traceback=traceback.format_exc())
    (out/'voice-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf8')
    return 0 if result['success'] else 1
if __name__=='__main__':sys.exit(main())
