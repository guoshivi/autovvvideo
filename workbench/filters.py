"""Small, deterministic full-frame colour filter vocabulary."""

FILTERS=('original','clear','warm','cinema')

def validate_filter(name, strength=0.35):
    if name not in FILTERS: raise ValueError('画面滤镜需为原画/清透/暖色/电影')
    if type(strength) not in (int,float) or float(strength)!=float(strength) or not 0<=float(strength)<=1:
        raise ValueError('画面滤镜强度需为 0–1')
    return name,float(strength)

def ffmpeg_filter(name='original', strength=0.35):
    name,strength=validate_filter(name,strength)
    if name=='original' or strength==0:return ''
    if name=='clear':
        return f'eq=contrast={1+0.08*strength:.6f}:brightness={0.018*strength:.6f}:saturation={1+0.06*strength:.6f}'
    if name=='warm':
        return f'colorbalance=rs={0.08*strength:.6f}:gs={0.02*strength:.6f}:bs={-0.06*strength:.6f}'
    return f'eq=contrast={1+0.12*strength:.6f}:saturation={1-0.08*strength:.6f},colorbalance=rs={0.03*strength:.6f}:bs={-0.03*strength:.6f}'
