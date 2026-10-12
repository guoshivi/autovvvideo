# 部署手册（给 AI 助手执行）

你是负责在一台**新 Windows 电脑**上部署并验收 autovvvideo 的 AI 助手。
按本文顺序执行；**每一步都要看真实输出**，不要把"命令退出码 0"当作"功能可用"。

## 工作纪律（先读）

1. **先体检、再动手**：每一节都有验证命令，逐项核对后再进下一步。
2. **不要跳步**：特别是第 4、5、6 节的验收，不做完不能说"部署完成"。
3. **同一件事失败 2 次换方法**：贴完整报错原文，说明换了什么、为什么。
4. **遇到账号、密钥、代理、驱动升级**：停下来问主人，不要猜、不要编。
5. **不要改掉检查脚本去"绕过"失败**；不要伪造任何输出。
6. 装完把你实际执行的版本（型号、驱动、Python、两个环境的 torch 版本）整理成一张表发给主人。

## Step 0 · 硬件与系统体检

```powershell
# 系统版本
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version
# 显卡与驱动（没有输出 = 没有 NVIDIA 显卡或驱动未装，停止）
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
# 磁盘剩余（需要 ≥30GB）
Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
# 联网检查（GitHub 与 HuggingFace 可达性）
curl.exe -s -o NUL -w "github:%{http_code}`n" https://github.com
curl.exe -s -o NUL -w "hf:%{http_code}`n" https://huggingface.co
```

判据：
- `nvidia-smi` 版本 **≥ 580**（CUDA 13 要求）。低于此版本 → 升级 NVIDIA 驱动后再继续。
- 磁盘剩余 ≥30GB，否则先清空间。
- GitHub 或 HuggingFace 不通 → 先解决网络（公司网络可能需要代理；让主人确认），不要硬装。
- **RTX 3080 Ti 预期可用**（计算能力 8.6，支持 BF16），但以第 5 步实测为准。

## Step 1 · 获取代码

```powershell
git clone https://github.com/guoshivi/autovvvideo
cd autovvvideo
```
（没有 git 就去 https://git-scm.com/download/win 安装，或让主人下载 ZIP 解压。）

## Step 2 · 预检（此时部分失败是正常的）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\doctor.ps1 -CheckConfigOnly
```

这会生成默认配置并输出 JSON。刚克隆时 `runtime/model not installed` 之类的
WARN/FAIL **属于预期**——别慌，下一步安装会把它们补齐。

## Step 3 · 正式安装

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

安装脚本会依次做（每步都有校验，任何一步失败会明确报错停下）：

1. 从官方来源下载工具：uv、FFmpeg（共享库 GPL 构建）、fdkaac，并**逐个核对 SHA-256**
2. uv 安装独立 Python **3.13.14**（落在本目录 `.uv-python\`，不碰系统 Python）
3. 创建**两套互相隔离的环境**并安装固定版本依赖（`.runtime\embedding\`、`.runtime\tts\`，
   两套的 transformers 版本不同，**严禁合并**）
4. 从 HuggingFace 下载**两个固定版本模型**（EmbeddingGemma-2、Qwen3-TTS），
   逐文件核对大小与 SHA-256，全部通过才写 REVISION 标记
5. 引用系统中文字体（不复制、不随仓库分发）
6. 创建桌面图标（无窗口启动）

- 预计耗时 **30–90 分钟**（取决于网速）；下载总计约 10–15 GB。
- 脚本可**安全重跑**：已校验通过的文件会跳过。
- 网络补充：若 HuggingFace 直连超时，可先 `$env:HF_ENDPOINT='https://hf-mirror.com'`
  再重跑 install（社区镜像，装完报告里注明使用了镜像）。
- 如果模型页面提示需要登录/接受条款：让主人用官方账号在浏览器完成，**不要把 token
  写进命令行或日志**。

## Step 4 · 安装后体检（必须全绿才继续）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\doctor.ps1
```

期望：**没有任何 FAIL**（WARN 允许，但要能解释）。逐项含义：

| 检查 | 含义 | 不通过怎么办 |
|---|---|---|
| config.* | 配置文件完整 | 重跑 install.ps1 |
| ffmpeg / ffprobe / ffmpeg.drawtext / shared-dlls | 剪辑与字幕要用 | 删除 `tools\downloads` 重跑 install |
| fdkaac.actual | 用合成音频实际编码一遍并核验码率 ≥128kbps | 重跑 install；仍失败查杀软是否拦截 |
| embedding.runtime / tts.runtime | 两套环境能 import torch/numpy | 重跑对应环境的依赖安装（install.ps1） |
| model.embedding / model.tts | 模型逐文件校验通过 | 重跑 install（会补下载） |
| nvidia | 驱动可查询 | 装/升级驱动 ≥580 |
| permissions.* | 目录可读写 | 检查杀软/权限 |

## Step 5 · GPU 实测（必须真实跑一次）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\doctor.ps1 -HeavyGPU
```

`cuda-bf16-heavy` 必须 PASS（真实分配显存并做一次 BF16 矩阵乘）。
FAIL 就不要继续，先解决驱动问题。

## Step 6 · 首次真实出片（最终验收）

准备 2–5 条**任意短视频**（手机拍的即可）放进 `materials\` 下随便一个文件夹。

1. 双击桌面 **autovvvideo** 图标 → 出现应用窗口（无终端窗口）
2. ① 选择素材文件夹 → 点建索引，等完成（第一条会加载模型，可能 1–2 分钟）
3. ② 写 3–5 句文案（或点 ✨AI 生成，先填 AI 接口地址+密钥）
4. ③ 点「开始出片」→ 等任务完成
5. 在「结果」区播放，并点「下载」

**验收标准（逐条核对，全部满足才算部署完成）**：
1. doctor.ps1 无 FAIL
2. 桌面图标能打开页面，无终端窗口弹出
3. 索引任务成功
4. 出片任务成功，成片为 1080×1920 竖屏，**有人声、有字幕**
5. 下载的成片文件能正常播放
6. 关闭窗口重开一次，之前的结果仍在（数据持久）

任何一条不过 → 把任务里显示的失败原因原文 + `logs\` 下对应日志发给主人。

## Step 7 · AI 文案（可选）

页面「✨ AI 生成」面板底部：填**接口地址**与**密钥**（OpenAI 兼容服务）。
密钥只存本机；测试一次「开始生成」能出文案即可。
没有 AI 也能用：直接手写文案照常出片。

## 常见问题

| 症状 | 处理 |
|---|---|
| install 报 `dependencies.json is not VERIFIED_BY_PARENT` | 仓库被改坏了，重新 clone |
| 下载 SHA-256 不匹配 | 删 `tools\downloads` 重跑；连续两次失败查网络代理/杀软 |
| `uv python install` 失败 | 检查网络后重跑；不要改用系统 Python 顶替 |
| 模型下载中断 | 直接重跑 install（幂等，会续传并重新校验） |
| 出片时提示「显卡正忙」 | 有游戏/ComfyUI 等占用显卡，关掉后重试 |
| 端口被占用 | 改 `config\workbench.config.json` 的 `port`（如 8766）后重开 |
| 页面打不开 | 看 `logs\launcher.log`；确认没有旧实例占着端口 |
| 启动报错 | 在该目录打开终端执行 `powershell -File .\launch.vbs` 前后检查日志；仍不明把日志发给主人 |

## 红线（Do NOT）

- ❌ 不要合并 `.runtime\embedding` 与 `.runtime\tts`（两套依赖版本互斥）
- ❌ 不要用系统里的其他 ffmpeg 替换 `tools\ffmpeg.exe`（必须带 drawtext 且为共享 DLL 构建）
- ❌ 不要把 `tools\fdkaac.exe` 换成 ffmpeg 内置 aac（成片质检链路依赖 FDK 编码）
- ❌ 不要删除 `config\` `data\` `models\` `tools\`（都是主人的数据与已装资源）
- ❌ 不要把服务暴露到公网；局域网使用先读 `docs/SECURITY.md`
- ❌ 不要在没跑完 Step 4–6 的情况下告诉主人"已经装好可用"

## 给主人的汇总模板

```
autovvvideo 部署完成：
- 机器：<显卡型号> / 驱动 <版本> / <内存>
- 环境：Python 3.13.14（两套隔离环境）；torch <版本>
- 验收：doctor 无 FAIL；实际出片成功（1080×1920，有人声有字幕，可播放可下载）
- 桌面图标：已创建（双击即用）
- 备注：<使用镜像 / WARN 解释 / 其他>
```
