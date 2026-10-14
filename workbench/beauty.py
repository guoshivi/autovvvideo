"""Optional, local-only light face beauty processing.

The implementation deliberately uses a classical OpenCV Haar cascade and a
feathered face ROI.  It does not identify people, create embeddings, upload
frames, or blur the full image.  OpenCV is imported lazily so old jobs with
beauty disabled keep the original dependency-free path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover - platform dependent
        raise RuntimeError('人物轻美颜需要可选 filters 环境中的 OpenCV，请先安装 filters 依赖') from exc
    return cv2, np


def validate_strength(value) -> float:
    if type(value) not in (int, float):
        raise ValueError('beauty_strength 必须是 0–1 的数字')
    value = float(value)
    if value != value or value < 0 or value > 1:
        raise ValueError('beauty_strength 必须是 0–1 的数字')
    return value


def detect_faces(frame, classifier=None):
    cv2, np = _cv2()
    if frame is None or getattr(frame, 'size', 0) == 0:
        return []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    classifier = classifier or cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'))
    if classifier.empty():
        raise RuntimeError('OpenCV 人脸检测器不可用，请检查 filters 安装')
    boxes = classifier.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5,
                                        minSize=(max(24, frame.shape[1] // 18),
                                                 max(24, frame.shape[0] // 18)))
    return [tuple(int(x) for x in box) for box in boxes]


def _feather_mask(shape, box, feather=0.22):
    cv2, np = _cv2()
    h, w = shape[:2]
    x, y, bw, bh = box
    x=max(0,x); y=max(0,y); x2=min(w,x+bw); y2=min(h,y+bh)
    if x2<=x or y2<=y:return np.zeros((h,w), dtype=np.float32)
    mask=np.zeros((h,w), dtype=np.float32)
    mask[y:y2,x:x2]=1.0
    k=max(3,int(min(bw,bh)*float(feather))|1)
    return cv2.GaussianBlur(mask,(k,k),0)


def beautify_frame(frame, strength=0.25, boxes=None, classifier=None):
    """Return ``(frame, detected_count)`` with a restrained local treatment."""
    cv2, np = _cv2(); strength=validate_strength(strength)
    if strength <= 0:return frame.copy(), 0
    boxes=list(boxes if boxes is not None else detect_faces(frame,classifier))
    out=frame.copy()
    for box in boxes:
        x,y,w,h=box; x=max(0,x);y=max(0,y);x2=min(frame.shape[1],x+w);y2=min(frame.shape[0],y+h)
        if x2<=x or y2<=y:continue
        roi=frame[y:y2,x:x2]
        # Bilateral filtering keeps eyes/mouth edges while smoothing skin
        # texture.  Blend is limited by strength and a feathered face mask.
        smooth=cv2.bilateralFilter(roi, 5, 24 + 28*strength, 7 + 8*strength)
        hsv=cv2.cvtColor(smooth,cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:,:,2]=np.minimum(255.0,hsv[:,:,2] + 10.0*strength)
        bright=cv2.cvtColor(hsv.astype(np.uint8),cv2.COLOR_HSV2BGR)
        alpha=_feather_mask(frame.shape,box)[y:y2,x:x2] * (0.55*strength)
        out[y:y2,x:x2]=(roi*(1-alpha[...,None]) + bright*alpha[...,None]).astype(np.uint8)
    return out, len(boxes)


@dataclass
class BeautyReport:
    frames: int = 0
    frames_with_faces: int = 0
    faces_detected: int = 0
    no_op: bool = False

    def as_dict(self):
        return {'frames': self.frames, 'frames_with_faces': self.frames_with_faces,
                'faces_detected': self.faces_detected, 'no_op': self.no_op}


def process_video(src, dst, strength=0.25, *, fps=30, width=1080, height=1920, expected_frames=None):
    """Stream a video through OpenCV without loading it into memory.

    This helper is intentionally only called when the user enables beauty;
    disabled/old jobs never import OpenCV.  The output frame count follows the
    input count and no-face material is reported as a true no-op.
    """
    cv2, _ = _cv2(); strength=validate_strength(strength)
    cap=cv2.VideoCapture(str(src))
    if not cap.isOpened():raise RuntimeError('人物轻美颜无法读取视频')
    fourcc=cv2.VideoWriter_fourcc(*'mp4v'); writer=None; report=BeautyReport()
    try:
        while True:
            ok,frame=cap.read()
            if not ok:
                if report.frames==0 or expected_frames is not None and report.frames!=int(expected_frames):
                    raise RuntimeError('人物轻美颜读取帧数不足或视频读取失败')
                break
            report.frames+=1
            frame=cv2.resize(frame,(int(width),int(height)),interpolation=cv2.INTER_AREA)
            boxes=detect_faces(frame)
            if boxes:report.frames_with_faces+=1;report.faces_detected+=len(boxes)
            out,_=beautify_frame(frame,strength,boxes=boxes)
            if writer is None:
                writer=cv2.VideoWriter(str(dst),fourcc,float(fps),(int(width),int(height)))
                if not writer.isOpened():raise RuntimeError('人物轻美颜无法写出视频')
            writer.write(out)
    finally:
        cap.release()
        if writer is not None:writer.release()
    if not Path(dst).is_file() or Path(dst).stat().st_size<=0:raise RuntimeError('人物轻美颜写出视频失败')
    report.no_op=report.faces_detected==0
    if expected_frames is not None and report.frames!=int(expected_frames):raise RuntimeError('人物轻美颜输出帧数不匹配')
    return report.as_dict()
