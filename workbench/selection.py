"""Deterministic shot selection for multi-version renders (feature 10).

Two mechanisms keep films from re-using the same shots:
  * ``spread_candidates`` — run when the mapping snapshot is assembled: for each
    sentence, candidates whose (path,start) already serves as an earlier
    sentence's top pick are demoted behind the rest (order-only, no candidate
    is ever removed, so the immutable mapping contract is untouched).
  * ``VariantSelector`` — run at render time: version N starts its scan at
    offset ``min(N, len-1)`` in each sentence's candidate list (version
    rotation) and grows a per-film usage table so a shot is only re-used up to
    the configured cap while any alternative exists ``(same file, different
    segment is a different shot)``.
"""

import hashlib
import math
from .pacing import estimate_slot_seconds,voice_slot_seconds

def _key(c):
    return (c.get('path'), float(c.get('start') or 0))

def shot_key(c):
    """Stable identity for a real source window.

    The source SHA is included when present so two files with the same path
    after a replacement cannot share a usage bucket.  Start and duration are
    rounded only for bookkeeping; the original values remain in the mapping.
    """
    return (c.get('source_sha256') or c.get('path'),
            round(float(c.get('start') or 0), 3),
            round(float(c.get('seconds') or 0), 3))

def _opening_key(c):
    """Opening identity: overlapping windows from one file are one shot."""
    return (c.get('source_sha256') or c.get('path'),
            round(float(c.get('start') or 0), 1))

def _conflicts(c, recent):
    sha=c.get('source_sha256') or c.get('path'); a=float(c.get('start') or 0); b=a+float(c.get('seconds') or 0)
    for r in recent:
        if isinstance(r,(list,tuple)) and len(r)>=2:
            if r[0]!=sha:continue
            ra=float(r[1]); rb=ra+(float(r[2]) if len(r)>2 else 0.5)
            if (min(b,rb)-max(a,ra))>0.25:return True
    return False

def diversity_rank(mapping, rank=0, recent=None):
    """Return a deterministic mapping order for a batch rank.

    Every candidate stays present and its retrieval score is untouched.  The
    rank rotates candidates, while ``recent`` adds a stable penalty to opening
    windows used by earlier jobs.  This is deliberately an order operation so
    source SHA/index/path guards continue to validate the same candidates.
    """
    rank = max(0, int(rank or 0))
    recent = list(recent or ())
    out=[]
    for row in mapping or []:
        row=list(row or [])
        if not row:
            out.append([]); continue
        n=len(row)
        off=rank % n
        rotated=row[off:]+row[:off]
        # A recent opening gets pushed behind an unused candidate.  Ties use
        # the rotated retrieval order, so the result is reproducible.
        fresh=[c for c in rotated if not _conflicts(c,recent)]
        used=[c for c in rotated if _conflicts(c,recent)]
        out.append(fresh+used if fresh else rotated)
    return out

def recent_openings(jobs, limit=64):
    """Extract opening identities from immutable job snapshots."""
    rows=[]
    for job in jobs or []:
        inp=job.get('input') or job
        reserved=(inp.get('selection_context') or {}).get('reserved_prefix')
        if reserved is not None:
            for cut in reserved:
                rows.append((cut.get('source_sha256') or cut.get('path'),float(cut.get('start') or 0),float(cut.get('used_seconds') or cut.get('seconds') or 0)))
            continue
        for row in (inp.get('mapping') or []):
            if row:
                c=row[0];rows.append((c.get('source_sha256') or c.get('path'),float(c.get('start') or 0),float(c.get('seconds') or 0)))
    return rows[-max(1, int(limit)) :]

def prefix_plan(mapping, lines, settings, voices=None, seconds=15):
    """Predict the same frame slots and candidate sequence as render_video.

    Voice durations are estimates until the WAVs exist; callers may recompute
    this plan after binding real receipts.
    """
    selector=VariantSelector(0,int(settings.get('shot_reuse') or 0))
    elapsed=0;out=[]
    for i,(line,row) in enumerate(zip(lines,mapping)):
        if elapsed>=seconds:break
        if voices and i<len(voices):want=voice_slot_seconds(float(voices[i])/24000)
        elif settings.get('voice'):want=estimate_slot_seconds(len(line),4.0*float(settings.get('speed') or 1))
        else:want=max(2.0,min(8,max(1.8,len(line)/5)))
        remaining=math.ceil(want*30);picker=selector.new_sentence(row)
        while remaining>0 and elapsed<seconds and row:
            c=selector.pick(picker)
            frames=min(remaining,max(1,math.floor(float(c.get('seconds') or 0)*30)))
            used=min(frames/30,seconds-elapsed)
            out.append({'sentence':i,'source_sha256':c.get('source_sha256') or c.get('path'),'index_id':c.get('index_id'),'start':float(c.get('start') or 0),'used_seconds':round(used,6),'output_start':round(elapsed,6),'output_end':round(elapsed+used,6),'purpose':'prefix reservation'})
            elapsed+=frames/30;remaining-=frames
            if 0<remaining<=3:break
    return out

def spread_candidates(rows):
    """Demote clips that already serve as a previous sentence's top pick.

    Stable order otherwise; every candidate stays present.
    """
    seen=set(); out=[]
    for row in rows or []:
        row=list(row or [])
        fresh=[c for c in row if _key(c) not in seen]
        used=[c for c in row if _key(c) in seen]
        merged=fresh+used if fresh else row
        out.append(merged)
        if merged:
            seen.add(_key(merged[0]))
    return out

class VariantSelector:
    """Per-film deterministic picker shared across a job's sentences."""

    def __init__(self, variant=0, cap=0):
        if type(variant) is not int or variant<0:raise ValueError('selection variant must be a non-negative integer')
        if type(cap) is not int or cap<0:raise ValueError('selection cap must be a non-negative integer')
        self.variant=variant
        self.cap=cap
        self.used={}

    def order(self, cands):
        n=len(cands)
        if n==0: return []
        off=min(self.variant, n-1)
        return list(cands[off:]+cands[:off])

    def new_sentence(self, cands):
        return {'seq': self.order(list(cands)), 'idx': 0}

    def pick(self, state):
        seq=state['seq']; n=len(seq)
        if n==0: raise ValueError('selection: empty candidate list')
        idx=state['idx']
        for k in range(n):
            j=(idx+k)%n
            key=shot_key(seq[j])
            if self.cap==0 or self.used.get(key,0)<self.cap:
                self.used[key]=self.used.get(key,0)+1
                state['idx']=(j+1)%n
                return seq[j]
        # Every candidate is at (or above) its reuse cap: take the least-used
        # one, ties resolved by scan order, and count it anyway.
        best_u=None; best_j=0
        for k in range(n):
            j=(idx+k)%n
            u=self.used.get(shot_key(seq[j]),0)
            if best_u is None or u<best_u:
                best_u=u; best_j=j
        j=best_j; key=shot_key(seq[j])
        self.used[key]=self.used.get(key,0)+1
        state['idx']=(j+1)%n
        return seq[j]

def shot_usage(timeline):
    """Aggregate one film's chosen shots for the render report."""
    rows={}; order=[]
    for i,t in enumerate(timeline or []):
        for c in (t.get('sources') or []):
            k=_key(c)
            if k not in rows:
                rows[k]={'path':c.get('path'),'start':float(c.get('start') or 0),'seconds':c.get('seconds'),'count':0,'sentences':[]}
                order.append(k)
            r=rows[k]; r['count']+=1
            if i not in r['sentences']: r['sentences'].append(i)
    return [rows[k] for k in order]
