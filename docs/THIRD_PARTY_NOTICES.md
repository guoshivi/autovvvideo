# 第三方组件与许可说明（THIRD PARTY NOTICES）

本仓库只包含自有源码、文档，以及下列**明确许可**的贴图素材；不包含第三方二进制、模型权重或字体。
下列组件全部在**安装时**从官方/公示来源下载（地址与校验值见 `deploy/dependencies.json`）。
各组件的许可与注意如下；如计划对外再分发本工具或其下载物，请先阅读对应条目。

## 随仓库分发的素材

| 素材 | 来源 | 许可 |
|---|---|---|
| `workbench/assets/stickers/*.png` | Microsoft Fluent Emoji（github.com/microsoft/fluentui-emoji） | MIT，见同目录 LICENSE |
| `workbench/assets/sfx/*.wav` | 基础 6 个由本项目合成；switch/rollover/error/notify/sparkle/powerup/slide/cash/win 取自 Kenney Audio；cart/purchase/coupon/checkout/reward/achievement/levelup/warn/swipe/hover/toggle/complete 取自 `romainsimon/uisfx` soft 包；均重采样 24k 单声道 | Kenney CC0-1.0；uisfx 音频 CC0（代码 MIT）；合成音随本项目分发。详见 `workbench/assets/sfx/LICENSE.md` |
| `workbench/assets/music/*.mp3` | 本项目用 `_generate.py` 以纯振荡器合成，未使用第三方采样 | 随本项目分发 |

## 工具类

| 组件 | 版本/固定 | 许可与注意 |
|---|---|---|
| **uv**（Astral） | 0.13.0（Windows x64） | Apache-2.0 / MIT 双许可。https://github.com/astral-sh/uv |
| **FFmpeg**（BtbN 构建） | n8.1.3-9 `autobuild-2026-09-30-13-08`，**win64-gpl-shared** | **GPLv3**。BtbN 是 ffmpeg.org 官方下载页链接的第三方构建方。GPL 构建（含 DLL，供 torchcodec 使用）。若你把下载的工具再分发给别人，需遵守 GPL 义务。https://github.com/BtbN/FFmpeg-Builds |
| **fdkaac**（nu774，RareWares 编译包） | 1.0.5 / FDK-AAC 2.0.2，Windows x64 | fdkaac 本体 Zlib/BSD/MIT；**FDK-AAC 库另有其分发条款，且不授予 AAC 专利许可**。RareWares 为第三方编译、**无数字签名**。仅供本机使用，不建议再分发。https://github.com/nu774/fdkaac · https://www.rarewares.org |
| **Python**（uv 托管） | 3.13.14 | PSF License。https://www.python.org |

## Python 依赖（安装时从 PyPI / 官方源获取）

- **PyTorch / torchvision / torchaudio**（+cu130）：BSD 风格许可，wheel 内含其第三方许可证清单
- **torchcodec**：BSD 风格（要求 FFmpeg **共享库**，安装脚本已处理）
- **sentence-transformers / transformers / tokenizers / huggingface_hub / safetensors / accelerate / tokenizers**：Apache-2.0
- **numpy**：BSD-3-Clause
- **soundfile**（内置 libsndfile）：BSD-3-Clause（libsndfile 为 LGPL）
- **av**（PyAV，内置 FFmpeg 库）：BSD-3-Clause（内置 FFmpeg 为 LGPL/GPL 组合，随 wheel 声明）
- **qwen-tts**：Apache-2.0（https://github.com/QwenLM/Qwen3-TTS）
- **voxcpm 2.0.3（可选）**：Apache-2.0（https://github.com/OpenBMB/VoxCPM）；独立环境使用配对的 torch / torchaudio 2.8.0+cu128 与 torchcodec 0.7.0，不替换原 Qwen/选镜依赖。可选下载清单见 `deploy/vox_dependencies.json`。
- 其余传递依赖以各项目声明为准

## 模型

| 模型 | 固定版本 | 许可与注意 |
|---|---|---|
| **EmbeddingGemma-2**（google） | `914f7f89142e33e77833254d9c9b90c3cef7303b` | **Apache-2.0**；另见 Google Gemma 使用政策（禁用于受限用途）。https://huggingface.co/google/embeddinggemma-2 |
| **Qwen3-TTS-12Hz-0.6B-CustomVoice**（Qwen） | `85e237c12c027371202489a0ec509ded67b5e4b5` | **Apache-2.0**。https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice |
| **VoxCPM2（OpenBMB，可选）** | `32279effe8c19989596f05d353d1447f51d9e915` | **Apache-2.0**。https://huggingface.co/openbmb/VoxCPM2；参考音频须有合法授权，不得用于冒充或欺诈。 |

模型权重**不在本仓库**；由安装脚本从 HuggingFace 官方仓库按上述固定版本下载并逐文件校验。

## 字体

中文字幕使用**系统已安装的中文字体**（安装脚本自动探测，如微软雅黑）。
本仓库不附带、不复制、不再分发任何字体文件。请确保使用字体符合其授权范围。

## 免责

- AAC 编码相关专利许可不随本仓库或上述开源组件授予，商用前请自行评估。
- 使用 AI 服务商（如 DeepSeek）产生的内容与费用由使用者承担，遵守对应服务条款。

## 本次可选 filters 依赖

- **opencv-python-headless 4.10.0.84**：Apache-2.0；仅安装在独立 `.runtime/filters` 环境，用于 Haar cascade 局部人脸检测与 ROI 处理。
- **numpy 2.1.3**：BSD-3-Clause；与 filters 环境配对，不替换工作台既有 embedding/TTS 环境。

本项目没有引入 MediaPipe、CodeFormer 或 GFPGAN；GitHub 高星项目仅作为选型和许可证参考。
