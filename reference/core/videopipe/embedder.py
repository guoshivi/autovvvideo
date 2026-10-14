"""EmbeddingGemma2 lazy wrapper；支持注入 fake embedder。"""
from __future__ import annotations
from pathlib import Path
import numpy as np
PRESETS={"quality":{},"fast":{"video":{"max_soft_tokens":70}},"ultra":{"video":{"max_soft_tokens":70,"fps":0.5}}}
MODE_CONFIG_KWARGS={"text":{"vision_config":None,"audio_config":None},"text_vision":{"audio_config":None},"full":{}}
def pick_dtype(device):
    import torch
    if device=="cuda" and torch.cuda.is_bf16_supported(): return torch.bfloat16
    if device=="mps": return torch.bfloat16
    return torch.float32

def normalize(x):
    a=np.asarray(x,dtype=np.float32)
    if a.ndim==1: a=a[None,:]
    if a.ndim!=2 or a.shape[1]==0: raise ValueError("embedding 必须为非空二维向量数组")
    n=np.linalg.norm(a,axis=1,keepdims=True)
    if np.any(~np.isfinite(a)) or np.any(~np.isfinite(n)) or np.any(n<=0): raise ValueError("embedding 含 NaN/Inf 或零向量")
    return a/n
class Embedder:
    def __init__(self,model_dir,mode="text_vision",preset="fast",device=None,model=None):
        if mode not in MODE_CONFIG_KWARGS: raise ValueError(f"未知 mode: {mode}")
        if preset not in PRESETS: raise ValueError(f"未知 preset: {preset}")
        self.model_dir,self.mode,self.preset=model_dir,mode,preset
        if model is not None: self.model=model; self.device=device or "fake"; return
        if not model_dir or not Path(model_dir).expanduser().is_dir(): raise FileNotFoundError(f"本地模型目录不存在: {model_dir}")
        self.model_dir=str(Path(model_dir).expanduser().resolve())
        from .env import pick_device
        self.device=device or pick_device()
        from sentence_transformers import SentenceTransformer
        self.model=SentenceTransformer(self.model_dir,device=self.device,local_files_only=True,model_kwargs={"torch_dtype":pick_dtype(self.device)},config_kwargs=MODE_CONFIG_KWARGS[mode])
    def encode_texts(self,texts,prompt_name="SearchQuery",batch_size=16):
        x=self.model.encode(list(texts),prompt_name=prompt_name,batch_size=batch_size,normalize_embeddings=True,show_progress_bar=False)
        return normalize(x)
    def encode_media(self,payload):
        x=self.model.encode(payload if isinstance(payload,list) else [payload],normalize_embeddings=True,show_progress_bar=False,processing_kwargs=PRESETS[self.preset] or {})
        return normalize(x)[0]
    def encode_media_many(self,payloads,batch_size=None):
        """One call for several media payloads, returning every vector.

        `encode_media` accepts a list but returns only the first row, so a
        batch of clips could not previously be embedded in a single forward
        pass. Batching is what keeps the GPU busy (indexing was latency-bound)."""
        items=list(payloads)
        if not items: raise ValueError("encode_media_many 需要至少一个 payload")
        kwargs={} if batch_size is None else {"batch_size":int(batch_size)}
        x=self.model.encode(items,normalize_embeddings=True,show_progress_bar=False,processing_kwargs=PRESETS[self.preset] or {},**kwargs)
        return normalize(x)
