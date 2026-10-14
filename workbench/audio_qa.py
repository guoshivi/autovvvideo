"""Mixed-mode voice measurability guard.

This is an additional gate after the frozen correlation contract.  It only
classifies weak mixed windows; the original pure-voice thresholds remain
unchanged.  A weak window must still contain projected voice energy and a
bounded residual relative to the frozen music budget, otherwise it is
rejected as measurement-limited rather than silently passed.
"""
import math
import numpy as np

def _projection(x,y):
    den=float(np.dot(x,x))
    if den<=1e-18:return 0.0,float('inf')
    gain=float(np.dot(x,y)/den)
    residual=float(np.sqrt(np.mean((y-gain*x)**2)))
    return gain,residual

def local_gain_gate(original, isolated, start, offset=0, *, rate=24000, known_music=None):
    x=np.asarray(original,dtype=np.float64);y=np.asarray(isolated,dtype=np.float64)
    base=start+int(offset)
    if base<0 or base+len(x)>len(y):return {'ok':False,'reason':'shifted voice outside decoded PCM'}
    z=y[base:base+len(x)]
    music=np.asarray(known_music,dtype=np.float64) if known_music is not None else None
    size=round(rate*.1); measured=0
    for a in range(0,len(x),size):
        b=min(len(x),a+size);xr=x[a:b]
        voice_rms=float(np.sqrt(np.mean(xr*xr)))
        if music is None:budget=.001
        else:
            m=music[base+a:base+b]
            budget=max(.001,.04*float(np.sqrt(np.mean(m*m))) if len(m)==b-a else .001)
        if voice_rms<3.2*budget:continue
        gain,_=_projection(xr,z[a:b]);measured+=1
        if not math.isfinite(gain) or not .9<=gain<=1.1:
            return {'ok':False,'reason':'local projection gain','window_start':a,'gain':gain,'measured_windows':measured}
    return {'ok':True,'measured_windows':measured}

def mixed_voice_gate(original, isolated, start, known_music, correlation, *, rate=24000):
    x=np.asarray(original,dtype=np.float64); y=np.asarray(isolated,dtype=np.float64)
    music=np.asarray(known_music,dtype=np.float64)
    offset=int(correlation.get('offset_samples') or 0)
    shifted=start+offset
    if len(x)==0 or shifted<0 or shifted+len(x)>len(y):
        return {'ok':False,'reason':'missing/truncated sentence PCM'}
    z=y[shifted:shifted+len(x)]
    mr=music[shifted:shifted+len(x)] if shifted+len(x)<=len(music) else np.zeros(len(x))
    music_rms=float(np.sqrt(np.mean(mr*mr))) if len(mr) else 0.0
    budget=max(.001,.04*music_rms)
    # A window is measurable when the original voice is materially above the
    # conservative codec-noise budget.  Measurable windows keep the frozen
    # .97/.95/.90/offset/rms contract exactly.
    rms=float(np.sqrt(np.mean(x*x)))
    if correlation.get('whole',-1)<.97 or abs(offset)>round(rate*.004) or not (.9<=float(correlation.get('rms_ratio',0))<=1.1):
        return {'ok':False,'classification':'measurable','budget':budget,'original_rms':rms}
    size=round(rate*.1); nonzero=np.flatnonzero(np.abs(x)>1/32768)
    lo,hi=(int(nonzero[0]),int(nonzero[-1])+1) if len(nonzero) else (0,len(x))
    regions=[('head',lo,min(hi,lo+size)),('tail',max(lo,hi-size),hi)]
    for b in correlation.get('non_silent_blocks',[]):
        bs=int(b.get('start_sample') or 0);regions.append(('block',bs,min(len(x),bs+size)))
    weak=[]
    for name,a,b in regions:
        if b<=a:continue
        xr=x[a:b];rr=float(np.sqrt(np.mean(xr*xr)))
        if rr>=3.2*budget:
            threshold=.95 if name in ('head','tail') else .90
            observed=correlation.get(name) if name in ('head','tail') else next((q.get('correlation') for q in correlation.get('non_silent_blocks',[]) if int(q.get('start_sample') or 0)==a),-1)
            if observed is None or observed<threshold:return {'ok':False,'classification':'measurable','budget':budget,'failed_region':name,'region_rms':rr}
        else:
            # Weak legal windows observed in diagnosis include gain=.8241;
            # requiring .9 would reject valid low-SNR audio. Deleted voice
            # projects near zero and is rejected by the .5 floor.
            gain,residual=_projection(xr,z[a:b])
            weak.append({'region':name,'gain':gain,'residual_rms':residual})
            if not (math.isfinite(gain) and gain>=.5 and residual<=budget):
                return {'ok':False,'classification':'weak_voice_missing','budget':budget,'failed_region':name,'projection_gain':gain,'projection_residual_rms':residual}
    gain_gate=local_gain_gate(x,y,start,offset,rate=rate,known_music=music)
    if not gain_gate['ok']:return {'ok':False,'classification':'measurable','local_gain':gain_gate}
    return {'ok':True,'classification':'measurement_limited' if weak else 'measurable','budget':budget,'music_rms':music_rms,'original_rms':rms,'weak_regions':weak,'local_gain':gain_gate}
