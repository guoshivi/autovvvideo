import numpy as np

from workbench.audio_qa import mixed_voice_gate


def test_mixed_weak_projection_accepts_real_voice_and_rejects_deleted_window():
    x=np.sin(np.linspace(0,20,2400))*0.0012
    music=np.ones_like(x)*0.05
    good=x*0.8241
    bad=np.zeros_like(x)
    corr={'ok':False,'whole':1.0,'offset_samples':0,'rms_ratio':1.0,'head':0.5,'tail':0.5,'non_silent_blocks':[]}
    pos=mixed_voice_gate(x,good,0,music,corr)
    neg=mixed_voice_gate(x,bad,0,music,corr)
    assert pos['classification']=='measurement_limited' and pos['ok']
    assert neg['classification']=='weak_voice_missing' and not neg['ok']
