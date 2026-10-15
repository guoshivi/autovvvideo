from dataclasses import dataclass,asdict
from pathlib import Path
from typing import Optional
from .voice_engines import is_clone_voice_id

_DEDUP={'off','light','double'}
_SPEAKERS={'Serena','Vivian','Uncle_Fu'}
_VOICE_ENGINES={'qwen','vox'}
_VOX_VOICES={'natural_female','bright_female','calm_male','story_female'}
_COVER_MODES={'auto','frame','file'}
_CARD_KINDS={'image','video'}
_VISUAL_FILTERS={'original','clear','warm','cinema'}

def _check_card(v,label):
    if not isinstance(v,dict): raise ValueError(f'{label}设置无效')
    if set(v)-{'path','sha256','seconds','kind'}: raise ValueError(f'{label}设置含未知字段')
    p=v.get('path')
    if not isinstance(p,str) or not p.strip(): raise ValueError(f'{label}需要先选择文件')
    if v.get('kind') not in _CARD_KINDS: raise ValueError(f'{label}类型需为图片或视频')
    sec=v.get('seconds')
    if type(sec) is not int and type(sec) is not float: raise ValueError(f'{label}时长需为数字')
    if not 0.5<=float(sec)<=30: raise ValueError(f'{label}时长需在 0.5–30 秒')
    sha=v.get('sha256')
    if sha is not None and (not isinstance(sha,str) or len(sha)!=64): raise ValueError(f'{label}指纹无效')

def _check_cover(v):
    if not isinstance(v,dict): raise ValueError('封面设置无效')
    if set(v)-{'mode','path','sha256','seconds'}: raise ValueError('封面设置含未知字段')
    mode=v.get('mode')
    if mode not in _COVER_MODES: raise ValueError('封面模式需为 auto/frame/file')
    if mode=='file':
        p=v.get('path')
        if not isinstance(p,str) or not p.strip(): raise ValueError('封面需要先选择图片')
        sha=v.get('sha256')
        if sha is not None and (not isinstance(sha,str) or len(sha)!=64): raise ValueError('封面指纹无效')
    if mode=='frame':
        sec=v.get('seconds')
        if type(sec) is not int and type(sec) is not float: raise ValueError('封面需要指定秒数')
        if not 0<=float(sec)<=3600: raise ValueError('封面秒数需在 0–3600 之间')

@dataclass(frozen=True)
class ProductionSettings:
    voice:bool=False;speaker:str='Serena';voice_engine:str='qwen';vox_voice:str='natural_female';subtitles:bool=True;subtitle_size:int=54;subtitle_bottom:int=380;subtitle_border:int=3;bgm:bool=False;bgm_path:str='';bgm_volume:float=0.20;dedup:str='off';beauty:bool=False;beauty_strength:float=0.25;visual_filter:str='original';visual_filter_strength:float=0.35;speed:float=1.0;target_seconds:int=0;shot_reuse:int=1;cover:Optional[dict]=None;intro:Optional[dict]=None;outro:Optional[dict]=None;width:int=1080;height:int=1920;fps:int=30;codec:str='h264'
    @classmethod
    def from_dict(cls,d):
        if set(d)-set(cls.__dataclass_fields__):raise ValueError('unknown production setting')
        x=cls(**d);x.validate();return x
    def validate(self):
        for k in ('voice','subtitles','bgm','beauty'):
            if type(getattr(self,k)) is not bool:raise ValueError(f'{k} must be boolean')
        if not isinstance(self.speaker,str) or self.speaker not in _SPEAKERS:raise ValueError('speaker must be Serena, Vivian or Uncle_Fu')
        if not isinstance(self.voice_engine,str) or self.voice_engine not in _VOICE_ENGINES:raise ValueError('voice_engine must be qwen or vox')
        if not isinstance(self.vox_voice,str) or not (self.vox_voice in _VOX_VOICES or is_clone_voice_id(self.vox_voice)):raise ValueError('vox_voice must be a built-in preset or a clone folder name')
        if self.bgm and (not self.bgm_path or not Path(self.bgm_path).is_file()):raise ValueError('BGM 文件不存在')
        if self.dedup not in _DEDUP:raise ValueError('dedup must be off/light/double')
        if self.visual_filter not in _VISUAL_FILTERS:raise ValueError('visual_filter must be original/clear/warm/cinema')
        for k in ('beauty_strength','visual_filter_strength'):
            v=getattr(self,k)
            if type(v) not in (int,float) or not 0<=float(v)<=1:raise ValueError(f'{k}范围需为 0–1')
        if (self.width,self.height,self.fps,self.codec)!=(1080,1920,30,'h264'):raise ValueError('输出规格固定为 1080x1920/30/H264')
        if type(self.subtitle_size) is not int or not 24<=self.subtitle_size<=100:raise ValueError('subtitle_size 24..100')
        if type(self.subtitle_bottom) is not int or not 100<=self.subtitle_bottom<=800:raise ValueError('subtitle_bottom 100..800')
        if type(self.subtitle_border) is not int or not 0<=self.subtitle_border<=8:raise ValueError('subtitle_border 0..8')
        if type(self.bgm_volume) not in (int,float) or not 0<=self.bgm_volume<=1:raise ValueError('BGM 音量范围 0..1')
        if type(self.speed) not in (int,float):raise ValueError('speed must be numeric')
        if not 0.85-1e-9<=float(self.speed)<=1.5+1e-9:raise ValueError('语速需在 0.85–1.5 之间')
        if type(self.target_seconds) is not int or not 0<=self.target_seconds<=3600:raise ValueError('目标时长需为 0–3600 秒的整数（0 表示不设）')
        if type(self.shot_reuse) is not int or self.shot_reuse not in (0,1,2,3):raise ValueError('镜头重复上限需为 0/1/2/3（0 表示不限）')
        if self.cover is not None:_check_cover(self.cover)
        if self.intro is not None:_check_card(self.intro,'片头')
        if self.outro is not None:_check_card(self.outro,'片尾')
        return self
    def snapshot(self):return asdict(self)
