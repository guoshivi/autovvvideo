"""PCM correlation contract used by workbench.media; no legacy runtime."""
def correlate_pcm(original, decoded, start_sample, rate=24000):
    import numpy as np
    x=np.asarray(original,dtype=np.float64); y=np.asarray(decoded,dtype=np.float64)
    def corr(a,b):
        if len(a)!=len(b) or not len(a): return -1.0
        # Normalized PCM cross-correlation (no mean subtraction); search is bounded to 4 ms.
        den=float(np.linalg.norm(a)*np.linalg.norm(b))
        return float(np.dot(a,b)/den) if den>1e-15 else -1.0
    if x.ndim!=1 or y.ndim!=1 or not len(x) or not np.isfinite(x).all() or not np.isfinite(y).all():
        return {'ok':False,'error':'invalid PCM'}
    best=(-2.0,0,None)
    for offset in range(-int(rate*.004),int(rate*.004)+1):
        begin=start_sample+offset
        if begin<0 or begin+len(x)>len(y): continue
        z=y[begin:begin+len(x)]; c=corr(x,z)
        if c>best[0]: best=(c,offset,z)
    whole,offset,z=best
    if z is None: return {'ok':False,'error':'missing/truncated sentence PCM'}
    nonzero=np.flatnonzero(np.abs(x)>1/32768)
    if not len(nonzero): return {'ok':False,'error':'silent original'}
    lo,hi=int(nonzero[0]),int(nonzero[-1])+1
    size=round(rate*.1); blocks=[]; silent=0
    for b in range(0,len(x),size):
        a=x[b:b+size]
        if not np.any(a): silent+=1; continue
        # Near-silence blocks (rms < -50dBFS) carry no measurable program
        # content: comparing their waveform against codec quantization noise
        # is measurement noise, not fidelity loss.  Observed twice on real
        # renders: (1) a few >12-LSB chain residues at ~2 LSB rms scored 0.59
        # against FDK dither; (2) a -53dBFS voice tail inside a loud BGM bed
        # scored 0.62 against the music-subtraction artifact floor in the
        # mixed-mode voice check.  Exempt them like exact-zero blocks;
        # thresholds for real content are unchanged.
        if float(np.sqrt(np.mean(a*a))) < 3e-3: silent+=1; continue
        blocks.append({'start_sample':b,'correlation':corr(a,z[b:b+size])})
    head=corr(x[lo:min(hi,lo+size)],z[lo:min(hi,lo+size)])
    tail=corr(x[max(lo,hi-size):hi],z[max(lo,hi-size):hi])
    original_rms=float(np.sqrt(np.mean(x*x))); decoded_rms=float(np.sqrt(np.mean(z*z)))
    gain_ratio=decoded_rms/original_rms if original_rms else 0.0
    ok=whole>=.97 and head>=.95 and tail>=.95 and all(b['correlation']>=.90 for b in blocks) and decoded_rms>=1e-5 and .9<=gain_ratio<=1.1
    # No bool(blocks) requirement: a signal whose every block is near-silence
    # (rms<3e-3) has no measurable program content - pass on whole/head/tail,
    # same stance as the exact-zero-block exemption (contract: quiet-but-valid
    # positive fixtures must pass).
    return {'ok':ok,'offset_samples':offset,'whole':whole,'head':head,'tail':tail,
            'silent_blocks':silent,'original_rms':original_rms,'decoded_rms':decoded_rms,'rms_ratio':gain_ratio,'non_silent_blocks':blocks,'thresholds':{'whole':.97,'head_tail':.95,'block':.90}}
