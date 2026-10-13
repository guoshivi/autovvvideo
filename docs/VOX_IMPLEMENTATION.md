# VoxCPM2 实现报告

## 最终交付状态

源码与本轮三项定点修复已通过全新独立复验：83 项自动回归、49 个唯一探针组（35 个对抗子情景）通过。Windows 上仅用自造配置验证写入/替换排他、HANDLE/FD 异常释放，4 项原生用例通过；原 Qwen worker 与既有断言保留。

下文的 38 项记录是早期实施历史，不是最终数字。实际使用、分层证据边界与目标机待验清单见 [VOX.md](VOX.md)。真实 Vox GPU 合成、听感与完整语音成片仍待目标 Windows 试用，不能用本报告代替它们。

## 变更文件

- `workbench/config.py`：增加可选 Vox Python、模型和缓存配置，空值仍兼容 Qwen-only 配置。
- `workbench/settings.py`：增加 `voice_engine` 和 `vox_voice` 的严格枚举校验；缺失字段默认 Qwen 与自然分享女声。
- `workbench/voice_engines.py`：引擎/预设边界、可用性检查、预览 payload 校验和缓存身份工具。
- `workbench/voxcpm_worker.py`：VoxCPM 2.0.3 本地适配器、预设锚点缓存、非变异 stop_head 观察、48 kHz float 到 24 kHz PCM16 转换、语速和收据。
- `workbench/service.py`：按所选引擎调度渲染，添加受 GPU 锁保护的试听流程和校验过的预览缓存。
- `workbench/server.py`：试听 POST、带 SHA 的 WAV GET、Range 支持。
- `workbench/renderer.py`、`workbench/verify.py`：把 engine 绑定到不可变报告，并为 Vox 收据要求实际 natural stop；Qwen EOS 校验保持原规则。
- `static/app.js`、`static/style.css`：Qwen/Vox 选择、中文预设、试听按钮、忙碌反馈和手机可用音频控件。
- `docs/VOX.md`：父任务安装和使用说明。

## 实施者中途验证记录（不是最终验收）

以下为实施期间的历史记录，最终父级回归和真实 UI 验证另见试用说明：

```text
python -m pytest tests/ -q
38 passed in 0.14s
node --check static/app.js
python -m py_compile workbench/config.py workbench/settings.py workbench/voice_engines.py workbench/voxcpm_worker.py workbench/service.py workbench/server.py workbench/renderer.py workbench/verify.py
```

测试中的 Vox 模型、SDK 和音频是明确标记的 synthetic doubles；没有下载模型，也没有在 Mac 上运行真实 GPU 语音生成。没有运行 `smoke_v3_render.py`，因为本次变更没有可用的 Windows Qwen/Vox 模型和素材工作台配置。

## 限制

目标 Windows 机器仍需要调用方先运行父任务提供的可选安装脚本。真实 Vox GPU 冷启动、显存占用、语音质量和长文案稳定性必须由父任务在目标环境独立验收。
