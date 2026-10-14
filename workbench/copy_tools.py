"""Local copy editing helpers; no network and no AI calls."""
import re
import unicodedata

_EMOJI=re.compile('[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]')
_END='。！？!?；;：:，,、．。'

def _strip_emoji(s):
    return _EMOJI.sub('',s)

def _clean_line(line):
    line=_strip_emoji(str(line)).strip()
    if not line:return ''
    if re.fullmatch(r'-{3,}',line):return '---'
    # Collapse duplicated punctuation while retaining a single delimiter for
    # sentence splitting. Decimal points and a leading negative sign are kept.
    line=re.sub(r'([，。！？!?；;：:,])\1+',r'\1',line)
    chunks=[];start=0
    for m in re.finditer(r'[。！？!?；;]',line):
        end=m.end(); part=line[start:end].strip()
        if part:chunks.append(part)
        start=end
    tail=line[start:].strip()
    if tail:chunks.append(tail)
    out=[]
    for part in chunks:
        part=re.sub(r'[。！？!?；;：:，,、]+$','',part).strip()
        if part:out.append(part)
    return '\n'.join(out)

def clean_punctuation(text):
    """Clean punctuation without losing line breaks or ``---`` script cuts."""
    raw=str(text or '').replace('\r\n','\n').replace('\r','\n')
    return '\n'.join(x for line in raw.split('\n') for x in [_clean_line(line)] if x!='')

def clean_script(text):
    return clean_punctuation(text)

def parse_script_variants(value, max_variants=20):
    """Validate a structured AI response: ``{"variants":["..."]}`` only."""
    import json
    try: obj=json.loads(value) if isinstance(value,str) else value
    except Exception as exc: raise ValueError('AI 裂变返回不是有效 JSON') from exc
    rows=obj.get('variants') if isinstance(obj,dict) else None
    if not isinstance(rows,list) or not rows:raise ValueError('AI 裂变返回缺少 variants')
    out=[];seen=set();exact=set()
    for row in rows[:max_variants]:
        if not isinstance(row,str) or not row.strip():raise ValueError('AI 裂变包含空文案')
        cleaned=clean_punctuation(row)
        if len(cleaned)>3000:raise ValueError('AI 裂变文案过长')
        if cleaned in exact:raise ValueError('AI 裂变返回了重复文案')
        exact.add(cleaned)
        key=''.join(ch for ch in unicodedata.normalize('NFKC',cleaned) if not (ch.isspace() or unicodedata.category(ch).startswith('P')))
        if key in seen:continue
        seen.add(key);out.append(cleaned)
    return out
