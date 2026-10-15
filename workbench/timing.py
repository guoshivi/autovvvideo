"""Measured script-to-duration planning helpers."""
from dataclasses import dataclass,asdict
import math
from .pacing import estimate_slot_seconds

@dataclass(frozen=True)
class TimingEstimate:
    seconds: float
    chars_per_second: float
    speed: float
    calibrated: bool
    available_seconds: float
    window_count: int
    covered_windows: int
    warning: str=''
    sample_count: int=0
    source: str='estimate'
    unique_library_seconds: float=0.0
    def as_dict(self):return asdict(self)

def indexed_windows(index_record):
    groups={}
    for f in (index_record or {}).get('files',[]):
        sha=(f.get('fingerprint') or {}).get('sha256') or (f.get('fingerprint') or {}).get('path')
        for block in f.get('blocks',[]):
            try:
                start=float(block.get('start') or 0);sec=float(block.get('seconds') or 0)
                if sec>0:groups.setdefault(sha,[]).append((start,start+sec))
            except (TypeError,ValueError):continue
    return _union(groups)

def _union(groups):
    spans=[]
    for ranges in groups.values():
        end=None
        for a,b in sorted(ranges):
            if end is None or a>end:
                spans.append(b-a);end=b
            elif b>end:spans.append(b-end);end=b
    return spans

def matched_windows(mapping):
    groups={}
    for row in mapping or []:
        for c in row:
            try:
                a=float(c.get('start') or 0);sec=float(c.get('seconds') or 0)
                if sec>0:groups.setdefault(c.get('source_sha256') or c.get('path'),[]).append((a,a+sec))
            except (TypeError,ValueError):continue
    return _union(groups)

def calibrate_cps(receipts=None,speed=1.0,default=4.0):
    vals=[]
    for row in receipts or []:
        if not isinstance(row,dict):continue
        chars=row.get('characters',row.get('char_count',row.get('text_chars')))
        samples=row.get('samples')
        try:
            chars=float(chars); seconds=float(samples)/24000.0
            prior=float(row.get('speed') or 1)
            if chars>0 and seconds>0 and prior>0:vals.append(chars/seconds/prior*float(speed or 1))
        except (TypeError,ValueError):continue
    if not vals:return float(default),False
    return max(1.0,min(8.0,sum(vals)/len(vals))),True

def estimate_timing(lines,index_record,*,speed=1.0,receipts=None,default_cps=4.0,mapping=None,settings=None):
    lines=[str(x) for x in (lines or []) if str(x).strip()]
    speed=float(speed or 1.0)
    cps,calibrated=calibrate_cps(receipts,speed,default_cps)
    library=indexed_windows(index_record);windows=matched_windows(mapping) if mapping is not None else library;available=sum(windows)
    # Speed is already represented in calibrated historical cps when samples
    # exist.  For the estimate fallback, adjust the neutral 4 cps baseline.
    if not calibrated:cps=max(1.0,min(8.0,float(default_cps)*speed))
    settings=settings or {}
    if settings.get('voice',True):seconds=sum(math.ceil(estimate_slot_seconds(len(line),cps)*30)/30 for line in lines)
    else:seconds=sum(math.ceil(max(2.0,min(8,max(1.8,len(line)/5)))*30)/30 for line in lines)
    seconds+=sum(float((settings.get(k) or {}).get('seconds') or 0) for k in ('intro','outro'))
    covered=min(len(windows),len(lines))
    warning=''
    if not windows:warning='尚无索引窗口，当前只是语速估计；建立索引后再确认。'
    elif seconds>available+1e-6:warning=f'文案预计 {seconds:.1f} 秒，已索引可承接窗口约 {available:.1f} 秒，可能不足。'
    elif not calibrated:warning='没有同引擎/音色的历史语音收据，使用估计语速。'
    return TimingEstimate(round(seconds,2),round(cps,3),speed,calibrated,round(available,2),len(windows),covered,warning,len(receipts or []),'verified_voice_receipt' if calibrated else 'estimate',round(sum(library),2))

def fit_copy_budget(lines,target_seconds,*,speed=1.0,receipts=None,default_cps=4.0):
    est=estimate_timing(lines,{'files':[]},speed=speed,receipts=receipts,default_cps=default_cps)
    target=float(target_seconds)
    if target<=0:raise ValueError('目标时长必须大于 0')
    return {'target_seconds':target,'chars_budget':max(1,round(target*est.chars_per_second)),'chars_per_second':est.chars_per_second,'calibrated':est.calibrated,'warning':est.warning}
