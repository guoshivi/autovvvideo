"""videopipe：离线、文件契约驱动的视频生产管线。"""
from __future__ import annotations
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
__version__ = "0.1.0"
