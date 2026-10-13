from dataclasses import dataclass,field,asdict
from pathlib import Path
import json,ipaddress
from .ai_endpoint import normalize_endpoint
class ConfigError(ValueError):pass
@dataclass
class RuntimeConfig:
    host:str='127.0.0.1';port:int=8765;data_root:str='./data';nas_roots:list[str]=field(default_factory=lambda:['./materials']);output_root:str='./output'
    model_dir:str='';tts_model:str='';tools_dir:str='';font_path:str='';gpu_lock_path:str='./data/gpu.lock';project_src:str='.'
    ffmpeg:str='';ffprobe:str='';fdk_encoder:str='';tts_python:str='';embedding_python:str='';max_index_seconds:int=14400;max_stage_seconds:int=3600;comfy_ports:list[int]=field(default_factory=list)
    # Optional trial engine.  Empty values keep all existing Qwen-only
    # configurations valid; production_validate deliberately does not require
    # these fields because Vox is an opt-in installation.
    voxcpm_python:str='';voxcpm_model:str='';voxcpm_cache:str=''
    ai_api_key:str='';ai_base_url:str='https://api.deepseek.com';ai_model:str='deepseek-chat'
    @classmethod
    def from_dict(cls,d):
        unknown=set(d)-set(cls.__dataclass_fields__)
        if unknown:raise ConfigError(f'unknown config keys: {sorted(unknown)}')
        x=cls(**d);x.validate();return x
    @classmethod
    def load(cls,path):return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    def validate(self):
        try:
            ip=ipaddress.ip_address(self.host)
            if ip.is_unspecified or ip.is_multicast:raise ValueError()
        except ValueError:raise ConfigError('host must be explicit IP address')
        if type(self.port) is not int or not 1<=self.port<=65535:raise ConfigError('port out of range')
        if not isinstance(self.nas_roots,list) or not self.nas_roots or any(not isinstance(x,str) or not x for x in self.nas_roots):raise ConfigError('nas_roots must be explicit nonempty paths')
        for k in ('max_index_seconds','max_stage_seconds'):
            if type(getattr(self,k)) is not int or getattr(self,k)<30:raise ConfigError(f'{k} must be >=30 integer')
        try:normalize_endpoint(self.ai_base_url,bool(self.ai_api_key))
        except ValueError as e:raise ConfigError(str(e)) from None
        if self.ai_api_key and not isinstance(self.ai_api_key,str):raise ConfigError('ai_api_key must be a string')
        if not isinstance(self.ai_model,str) or not self.ai_model:raise ConfigError('ai_model must be a nonempty string')
        for k in ('voxcpm_python','voxcpm_model','voxcpm_cache'):
            if not isinstance(getattr(self,k),str):raise ConfigError(f'{k} must be a string')
        return self
    def production_validate(self):
        for k in ('model_dir','tts_model','ffmpeg','ffprobe','fdk_encoder','tts_python','embedding_python','font_path','gpu_lock_path'):
            if not getattr(self,k):raise ConfigError(f'production config missing {k}')
        return self
    def to_dict(self):return asdict(self)
