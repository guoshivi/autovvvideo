import re
def split_script(text,max_sentences=100,max_chars=3000):
    s=str(text).strip()
    if len(s)>max_chars: raise ValueError('脚本过长')
    parts=[]
    for line in s.splitlines():
        if re.fullmatch(r'\s*-{3,}\s*',line):
            continue
        for p in re.split(r'(?<=[。！？!?；;])\s*|\n+',line.strip()):
            p=p.strip(' \t\r\n')
            if p: parts.append(p)
    if len(parts)>max_sentences: raise ValueError('句子数量超限')
    return parts
