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

def _key(c):
    return (c.get('path'), float(c.get('start') or 0))

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
        variant=int(variant or 0)
        self.variant=max(0,variant)
        self.cap=int(cap or 0)
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
            key=_key(seq[j])
            if self.cap==0 or self.used.get(key,0)<self.cap:
                self.used[key]=self.used.get(key,0)+1
                state['idx']=(j+1)%n
                return seq[j]
        # Every candidate is at (or above) its reuse cap: take the least-used
        # one, ties resolved by scan order, and count it anyway.
        best_u=None; best_j=0
        for k in range(n):
            j=(idx+k)%n
            u=self.used.get(_key(seq[j]),0)
            if best_u is None or u<best_u:
                best_u=u; best_j=j
        j=best_j; key=_key(seq[j])
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
