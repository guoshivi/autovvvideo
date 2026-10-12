"""Measured final-media QA. Frozen correlate_pcm is imported by AST from media."""
import json,math,subprocess,wave
from pathlib import Path
from fractions import Fraction
import numpy as np
from .media import correlate_pcm,sha256
from .process import run_owned
class VerificationFailure(ValueError):
    def __init__(self,stage,check,measured,required,index=None):
        self.details={'stage':stage,'check':check,'sentence_index':index,'measured':measured,'required':required}
        super().__init__(f'{stage}/{check} sentence={index} measured={measured} required={required}')
def read_wav(p):
    with wave.open(p if hasattr(p,'read') else str(p),'rb') as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate())!=(1,2,24000):raise VerificationFailure('verify','wav format',str(p),'mono PCM16 24k')
        return np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float64)/32768

def probe(ffprobe,path,packets=False):
    cmd=[str(ffprobe),'-v','error','-show_streams','-show_format','-of','json',str(path)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
    if r.returncode:raise VerificationFailure('verify','ffprobe',r.stderr[-500:],'exit 0')
    data=json.loads(r.stdout)
    if packets:
        q=subprocess.run([str(ffprobe),'-v','error','-select_streams','a:0','-show_packets','-show_entries','packet=size,duration_time','-of','json',str(path)],capture_output=True,text=True,timeout=120)
        if q.returncode:raise VerificationFailure('verify','ffprobe packets',q.stderr[-500:],'exit 0')
        data['packets']=json.loads(q.stdout).get('packets',[])
    return data
def decode(ffmpeg,path,out,with_audio,cancel=None,timeout=900):
    run_owned([str(ffmpeg),'-nostdin','-v','error','-xerror','-i',str(path),'-map','0:v:0',*(['-map','0:a:0'] if with_audio else []),'-f','null','-'],timeout=timeout,cancel=cancel,log_dir=out.parent)
    if with_audio:
        run_owned([str(ffmpeg),'-nostdin','-n','-v','error','-xerror','-i',str(path),'-map','0:a:0','-ac','1','-ar','24000','-c:a','pcm_f32le','-f','f32le',str(out)],timeout=timeout,cancel=cancel,log_dir=out.parent)
        y=np.fromfile(out,dtype='<f4').astype(np.float64)
        if not len(y) or not np.isfinite(y).all():raise VerificationFailure('verify','decoded PCM','empty/nonfinite','finite nonempty')
        return y
    return None

def verify_media(path,ffmpeg,ffprobe,timeline,expected=None,voice_wavs=None,mode='silent',cancel=None,timeout=900,eos_receipts=None,frozen_voice_pcm=None):
    """Verify a final file against the immutable original-clock PCM contract.

    ``expected`` is the complete, read-only final PCM master.  For mixed audio,
    the immutable voice master is rebuilt from the original WAVs and subtracted
    from that master before each sentence is checked again.
    """
    path=Path(path);info=probe(ffprobe,path,packets=mode!='silent');streams=info.get('streams',[])
    video=[s for s in streams if s.get('codec_type')=='video'];audio=[s for s in streams if s.get('codec_type')=='audio'];checks={}
    def _fps(s):
        try:return float(Fraction(str(s or '0')))
        except (ValueError,ZeroDivisionError):return 0.0
    # Container bookkeeping can shave a few ticks (±0.002 fps observed) off a
    # segment-joined timeline; accept avg within 0.02 of nominal 30 while still
    # rejecting real rate drift (25fps=5.0 off, NTSC 29.97=0.03 off).
    spec=len(video)==1 and video[0].get('codec_name')=='h264' and video[0].get('width')==1080 and video[0].get('height')==1920 and _fps(video[0].get('r_frame_rate'))==30 and abs(_fps(video[0].get('avg_frame_rate'))-30)<=0.02
    if not spec:raise VerificationFailure('verify','video specs',[{k:v.get(k) for k in ('codec_name','width','height','r_frame_rate','avg_frame_rate')} for v in video],'one H264 1080x1920 30fps')
    checks['video_specs']=True
    if len(audio)!=(0 if mode=='silent' else 1):raise VerificationFailure('verify','audio streams',len(audio),0 if mode=='silent' else 1)
    if not timeline:raise VerificationFailure('verify','timeline','empty','one or more finite intervals')
    prev_end=0.0
    for i,t in enumerate(timeline):
        try:start=float(t['start']);end=float(t['end'])
        except (KeyError,TypeError,ValueError):raise VerificationFailure('verify','timeline shape',t,'finite start/end',i)
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start or start<prev_end:
            raise VerificationFailure('verify','timeline shape',t,'finite increasing non-overlapping intervals',i)
        prev_end=end
    total_samples=round(prev_end*24000)
    vd=float(video[0].get('duration') or info.get('format',{}).get('duration') or 0)
    if abs(vd-prev_end)>1/30+.001:raise VerificationFailure('verify','timeline duration',vd,prev_end)
    checks['timeline']=True
    if mode=='silent':
        checks.update(audio_specs={'applicable':False,'reason':'voice and BGM disabled'},pcm={'applicable':False,'reason':'no audio'},eos={'applicable':False,'reason':'voice disabled'})
        decode(ffmpeg,path,path.parent/'silent-unused.f32',False,cancel,timeout);checks['full_decode']=True
        return {'ok':True,'checks':checks,'sha256':sha256(path),'bytes':path.stat().st_size,'mode':mode,'correlations':[]}
    a=audio[0]; packets=info.get('packets',[]);sized=sum(int(p['size']) for p in packets if 'size'in p);duration=sum(float(p['duration_time']) for p in packets if 'duration_time'in p)
    measured=8*sized/duration if duration>0 else 0
    audio_spec=a.get('codec_name')=='aac' and int(a.get('sample_rate') or 0)==24000 and int(a.get('channels') or 0)==1 and int(a.get('bit_rate') or 0)>=128000 and measured>=128000
    if not audio_spec:raise VerificationFailure('verify','AAC bitrate',{'stream':a.get('bit_rate'),'packet':measured,'packets':len(packets)},'>=128000 each; 24k mono AAC')
    ad=float(a.get('duration') or 0);vs=float(video[0].get('start_time') or 0);aus=float(a.get('start_time') or 0)
    if abs(vd-ad)>.08 or abs(vs-aus)>.004 or abs(vs)>.004 or abs(aus)>.004:raise VerificationFailure('verify','audio timing',{'video':vd,'audio':ad,'starts':[vs,aus]},'delta<=.08s, starts<=.004s')
    checks['audio_specs']=True
    decoded=np.asarray(decode(ffmpeg,path,path.parent/(path.stem+'-decoded.f32'),True,cancel,timeout),dtype=np.float64)
    if decoded.ndim!=1 or not np.isfinite(decoded).all() or len(decoded)<total_samples:raise VerificationFailure('verify','decoded PCM',len(decoded),'finite full timeline PCM')
    checks['full_decode']=True
    peak=float(np.max(np.abs(decoded)));rms=float(np.sqrt(np.mean(decoded*decoded)))
    if peak>10**(-.5/20) or rms<1e-5:raise VerificationFailure('verify','audio levels',{'peak':peak,'rms':rms},'peak<=-0.5dBFS RMS>=1e-5')
    checks['audio_levels']=True
    if expected is None:raise VerificationFailure('verify','expected PCM','missing','frozen master')
    expected=np.array(expected,dtype=np.float64,copy=True)
    expected.setflags(write=False)
    if expected.ndim!=1 or len(expected)!=total_samples or not np.isfinite(expected).all():raise VerificationFailure('verify','expected PCM shape',{'shape':expected.shape,'timeline_samples':total_samples},'finite 24k full timeline')
    voice_master=None;isolated=decoded[:total_samples];voice_samples=[]
    if voice_wavs:
        if len(voice_wavs)!=len(timeline):raise VerificationFailure('verify','voice count',len(voice_wavs),len(timeline))
        if frozen_voice_pcm is not None and len(frozen_voice_pcm)!=len(voice_wavs):raise VerificationFailure('verify','voice count',len(frozen_voice_pcm),len(voice_wavs))
        voice_master=np.zeros(total_samples,dtype=np.float64)
        voice_master.setflags(write=False)
        # Fill through a mutable work array, then freeze it before any gate.
        vm=np.zeros(total_samples,dtype=np.float64)
        for i,(wav_path,t) in enumerate(zip(voice_wavs,timeline)):
            x=read_wav(wav_path) if frozen_voice_pcm is None else np.array(frozen_voice_pcm[i],dtype=np.float64,copy=True)
            if x.ndim!=1 or not len(x) or not np.isfinite(x).all():raise VerificationFailure('verify','voice PCM','empty/nonfinite','immutable finite original PCM',i)
            x.setflags(write=False);voice_samples.append(x)
            start=round(float(t['start'])*24000);end=round(float(t['end'])*24000)
            if start<0 or start+len(x)>end:raise VerificationFailure('verify','WAV bounds',{'sentence':i,'start':start,'samples':len(x)},end,i)
            vm[start:start+len(x)]=x
        voice_master=vm;voice_master.setflags(write=False)
        if mode=='voice' and not np.array_equal(expected,voice_master):raise VerificationFailure('verify','voice master',{'expected_samples':len(expected)},'immutable original-WAV master')
        if mode=='mixed':
            known_music=np.array(expected-voice_master,dtype=np.float64,copy=True);known_music.setflags(write=False)
            isolated=np.array(decoded[:total_samples]-known_music,dtype=np.float64,copy=False)
        elif mode!='voice':
            raise VerificationFailure('verify','mode',mode,'voice or mixed with voice WAVs')
    correlations=[]
    for i,t in enumerate(timeline):
        start=round(float(t['start'])*24000);end=round(float(t['end'])*24000)
        # Voice contracts are evaluated against each immutable original WAV,
        # including short residual blocks. The padded master is only used for
        # mixed expected-mix validation.
        x=voice_samples[i] if (voice_wavs and mode=='voice') else expected[start:end]
        if not len(x) or (not np.any(x) and mode not in ('bgm',)):
            raise VerificationFailure('verify','expected interval',i,'finite nonzero samples',i)
        got=correlate_pcm(x,decoded,start);got['index']=i;correlations.append(got)
        if got.get('ok') is not True:
            metric={'whole':got.get('whole'),'head':got.get('head'),'tail':got.get('tail'),'min_block':min((b['correlation'] for b in got.get('non_silent_blocks',[])),default=None),'rms_ratio':got.get('rms_ratio'),'offset_samples':got.get('offset_samples')}
            raise VerificationFailure('verify','PCM correlation',metric,'whole>=.97 head/tail>=.95 each block>=.90 offset<=4ms rms .9..1.1',i)
    checks['pcm']=True
    if voice_wavs:
        voice_correlations=[]
        for i,(wav_path,t) in enumerate(zip(voice_wavs,timeline)):
            x=voice_samples[i];start=round(float(t['start'])*24000);got=correlate_pcm(x,isolated,start);got['index']=i;voice_correlations.append(got)
            if got.get('ok') is not True:
                metric={'whole':got.get('whole'),'head':got.get('head'),'tail':got.get('tail'),'min_block':min((b['correlation'] for b in got.get('non_silent_blocks',[])),default=None),'rms_ratio':got.get('rms_ratio'),'offset_samples':got.get('offset_samples')}
                raise VerificationFailure('verify','PCM correlation',metric,'original WAV whole>=.97 head/tail>=.95 each block>=.90 offset<=4ms rms .9..1.1',i)
        checks['voice_pcm']=True
        for i,(x,t) in enumerate(zip(voice_samples,timeline)):
            end=round(float(t['end'])*24000)
            voice_end=round(float(t['start'])*24000)+len(x); pad_start=voice_end+480; pad_end=end-96
            if pad_end <= pad_start: raise VerificationFailure('verify','voice padding window',{'start':pad_start,'end':pad_end},'non-empty frozen padding window',i)
            tail=isolated[pad_start:pad_end]
            pad_rms=float(np.sqrt(np.mean(tail*tail))) if len(tail) else float('nan')
            limit=0.001
            if mode=='mixed':
                # In a mixed master the padding window legitimately carries music;
                # subtracting the known music leaves a codec artifact floor that
                # scales with the music level (observed ~0.0153 x music rms,
                # i.e. ~-36dB).  Use 0.04x for margin: above the real floor
                # (passes the user's BGM renders) but well below any real voice
                # overrun (speech level, >=~0.01), so the guard still fires.
                mt=known_music[pad_start:pad_end]
                m_rms=float(np.sqrt(np.mean(mt*mt))) if len(mt) else 0.0
                limit=max(limit,0.04*m_rms)
            if not math.isfinite(pad_rms) or pad_rms>limit:raise VerificationFailure('verify','voice padding silence',pad_rms,'RMS<=%.4f after voice end+480 through sentence end-96'%limit,i)
        checks['voice_padding']=True
        last=timeline[-1];last_start=round(float(last['start'])*24000);last_x=voice_samples[-1];negative=isolated.copy();negative[last_start:min(total_samples,last_start+len(last_x))]=0.0
        negative_corr=correlate_pcm(last_x,negative,last_start)
        negative_info={'decoded_samples':len(decoded),'required_full_samples':total_samples,'accepted':negative_corr.get('ok') is True,'correlation':negative_corr}
        if negative_info['accepted']:raise VerificationFailure('verify','deleted-last negative',negative_corr,False,len(timeline)-1)
        checks['deleted_last_negative']=True
        if eos_receipts is None:
            checks['eos']={'applicable':False,'reason':'media-only validation; model EOS receipt not supplied'}
        else:
            def valid_eos(r):
                required={'natural_eos','forced_eos_token_id','eos_token_ids','tail_token_ids',
                          'generated_tokens','sequence_shape','padding_trimmed','pad_token_id'}
                if not isinstance(r,dict) or not required.issubset(r):return False
                if r['natural_eos'] is not True or r['forced_eos_token_id'] is not None:return False
                ids=r['eos_token_ids'];tails=r['tail_token_ids'];n=r['generated_tokens'];shape=r['sequence_shape']
                trim=r['padding_trimmed'];pad=r['pad_token_id']
                # Establish types before any comparison; bool is not a token/count.
                if type(n) is not int or not 0<n<1024:return False
                if not (isinstance(shape,list) and len(shape)==2 and all(type(x) is int and x>0 for x in shape) and shape[1]>=n):return False
                if not (isinstance(ids,list) and ids and all(type(x) is int and x>=0 for x in ids)):return False
                if not (isinstance(trim,list) and len(trim)==shape[0] and all(type(x) is int and 0<=x<n for x in trim)):return False
                if not (isinstance(tails,list) and len(tails)==shape[0] and all(type(x) is int and x in ids for x in tails)):return False
                if pad is not None and (type(pad) is not int or pad<0):return False
                # The observer cannot trim an absent pad or an EOS-as-pad token.
                if any(trim) and (pad is None or pad in ids):return False
                return True
            if not isinstance(eos_receipts,(list,tuple)) or len(eos_receipts)!=len(voice_wavs) or any(not valid_eos(r) for r in eos_receipts):raise VerificationFailure('verify','EOS receipt',eos_receipts,'complete natural EOS observation with no forced EOS')
            checks['eos']=True
        return {'ok':True,'checks':checks,'sha256':sha256(path),'bytes':path.stat().st_size,'mode':mode,'correlations':correlations,'voice_correlations':voice_correlations,'negative_control':negative_info,'aac_packet_bps':measured,'peak':peak,'rms':rms}
    checks['eos']={'applicable':False,'reason':'voice disabled'}
    return {'ok':True,'checks':checks,'sha256':sha256(path),'bytes':path.stat().st_size,'mode':mode,'correlations':correlations,'aac_packet_bps':measured,'peak':peak,'rms':rms}
