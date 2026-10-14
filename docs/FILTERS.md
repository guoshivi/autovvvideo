# 可选 filters 环境

人物轻美颜只使用本机独立的 OpenCV 环境。当前固定 PyPI 依赖为 `numpy==2.1.3` 与 `opencv-python-headless==4.10.0.84`，由 `install-filters.ps1` 或 `deploy/filters_setup.py` 安装到 `.runtime/filters`。安装失败会让开启美颜的任务明确失败，不会静默回退；关闭美颜的旧任务不会导入 `cv2`。

实现使用 OpenCV 官方 Haar cascade 做局部人脸定位，在羽化 ROI 内做受限双边滤波和亮度提升，不识别人、不保存 embedding、不上传帧。字幕在美颜后最后叠加。无脸时保留原 cut 并在报告标记 `no_op=true, encoded_path_used=false`。Mac 本机不代表 Windows 真脸验收；父级需在目标 Windows filters 环境执行原生探针和短视频帧数/时长检查。

GitHub 参考快照记录了 opencv/opencv（Apache-2.0）与 FFmpeg；本项目仅实际使用可选 PyPI OpenCV，不引入 MediaPipe、CodeFormer 或 GFPGAN。
