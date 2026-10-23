# GitHub 可复用实现清单（autoclip / bridgeclip / 模板）

调研日期：2026-10-23

## 1. `zhouxiaoka/autoclip`（MIT，★9.6k）

仓库：https://github.com/zhouxiaoka/autoclip
定位：本机 AI 长视频切片，React + Python，Tauri 桌面。

### 可直接借鉴的文件

| 文件 | 作用 | 我们怎么用 |
|---|---|---|
| `backend/services/studio/caption_layout.py` | 标点优先断句、宽度计算（CJK=1，Latin=0.55）、最多两行、按显示宽度分配屏幕时间 | 我们的 `workbench/subtitles.py` 现在是「单排 + 缩字号」，可借 `width()` 与 `_cut()` 的自然断点规则，做「单排超长时的语义缩字」 |
| `backend/services/studio/burned_subtitles.py` | 无 OCR 检测源视频是否已烧字幕（亮字+暗描边、多帧变化） | 加一个「素材自检」：导入素材时提示「检测到已烧字幕」，避免双重字幕 |
| `backend/services/studio/title_art_v6.py` | 花字/标题美术：字形 mask、描边、渐变、`neon/arena/editorial` 三种风格、按宽度缩放、最多三行 | 我们的 `workbench/flower_text.py` 可直接换用它的 `_mask/_ink/_place` 思路，补「描边+厚度+渐变」三种风格 |
| `backend/services/studio/templates/spec.py` + `registry.py` | 模板目录契约：`template.yaml`（name/fit/canvas/base/overlay/fields/audio/cover/qa），加载时校验；页面必须有 `setData/renderFrame/occupiedRects`，禁止 CSS 动画 | 建我们自己的 `workbench/templates/<id>/spec.json`，字段含安全区、槽位、限制、字体、许可；加载即校验，坏模板不进候选 |
| `backend/assets/fonts/manifest.json` | 字体来源 URL + SHA256 + OFL 许可 | 我们加字体时照这个格式记录来源与哈希，避免许可丢失 |
| `frontend/src/features/studio/PackagingSettings.tsx` / `TemplatePicker.tsx` | 包装/模板选择 UI | 右栏「包装」Tab 的交互参考 |

### 字体（OFL，可商用）

- Anton（英文标题）
- Barlow Condensed Black / BlackItalic（英文数字）
- Noto Sans SC（中文正文/标题，可变字重）
- Press Start 2P（像素风）
- 许可文件同在 `backend/assets/fonts/*-OFL.txt`

### 不要做

- 不整包替换我们的渲染引擎（它是面向长视频切片，不是逐句素材匹配）。
- 不复制它的品牌、案例视频、模板图片。
- 代码复用保留 MIT 版权声明。

## 2. `bridge-mind/bridgeclip`（MIT，★380）

仓库：https://github.com/bridge-mind/bridgeclip
定位：桌面 AI 剪辑，TypeScript + Python 引擎。

可借鉴：`engine/` 的许可证组织、字体打包、桌面端进程/队列结构；`docs/assets` 的界面组织。功能上与 autoclip 重叠，优先级低于 autoclip。

## 3. 动效模板

- `AleBrito124356/remotion-video-templates`（MIT）：logo intro、lower thirds、kinetic typography、captions、product promo。
  - 只借视觉节奏与分层方式；Remotion 对公司规模收费，商用前确认。
  - 我们的落地方式：用 ffmpeg + ASS 或 PIL 序列实现，不引入 Remotion 运行时。
- `ali-abassi/remotion-templates`：无许可证，仅看，不复制。

## 4. 素材库

| 类型 | 来源 | 许可 | 用法 |
|---|---|---|---|
| 图标 | Microsoft Fluent Emoji（已用） | MIT | 现有 26 个 |
| 图标 | OpenMoji | CC BY-SA 4.0 | 需要更多表情时，必须署名+同许可 |
| 图标 | Noto Emoji | Apache-2.0 | 可商用 |
| 音效 | Kenney Asset Pack | CC0 | 点击/弹出/转场 |
| 音乐 | Free Music Archive / Pixabay / Uppbeat | 逐曲不同 | 建 `music/tags.json` 记录许可与情绪 |
| 字体 | Google Fonts | OFL/Apache | 只挑 3–5 款，附许可 |

## 5. 结论

1. GitHub 没有「即创同款模板库」可直接下；可复用的是**模板契约、花字美术、字幕排版、字体清单**。
2. 先借 `caption_layout.py` + `title_art_v6.py` + `templates/spec.py` 三份实现，成本最低、收益最大。
3. 所有复用文件在提交时附 `docs/THIRD_PARTY_NOTICES.md` 条目与 MIT/OFL 原文。
