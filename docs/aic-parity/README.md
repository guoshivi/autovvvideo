# 即创功能盘点（AIC Parity）

采集日期：2026-10-23
账号：河图洛书 / 超管 / BP-1807808861465674
采集方式：ego-browser（hetu 空间）逐页点击 + DOM 文本 + 截图

## 产物

| 文件 | 内容 |
|---|---|
| `nav.json` | 工作台首屏 DOM/链接 |
| `menus.json` | 资产/成片创作/原料创作悬停后的子菜单 |
| `pages.json` | 8 个一级入口的 URL 与页面文本 |
| `subpages.json` | 成片库、原料库、演员库、数字人 |
| `smart-clip-tabs.json` | 智能成片 6 个配置 Tab 全文 |
| `smartclip-subdialogs.json` | 音色克隆、我的音乐、我的图片、其他 |
| `dialogs-main.json` | 脚本库、AI帮写、音频脚本 |
| `limits.md` | 限制与默认值 |
| `features.csv` | 69 行逐控件清单（have/partial/missing/n/a） |
| `gap.md` | 差距矩阵与优先开发项 |
| `github-reuse.md` | autoclip/bridgeclip/模板/素材的可复用清单 |
| `screens/` | 页面与弹窗截图 |

## 覆盖率与已知缺口

- 已覆盖：一级导航 8 项、资产 3 个子库、智能成片 6 个 Tab、脚本库/AI帮写/音频脚本、音色克隆/我的音乐/我的图片、包装/比例。
- 未打开：数字人（点击不跳转，疑似账号无权限）；商品选择器与素材上传器（涉及文件选择/账号数据，未做破坏性点击）；设置面板。
- 未量化：脚本条数上限、并发数、历史保留天数、花字模板精确数量（按截图约 18）。

## 使用方式

1. 先读 `gap.md` 看总量：69 行中 have 31、partial 14、missing 21、n/a 3（2026-10-23 晚）。
2. 开发时以 `features.csv` 的行号为准，做完一行把 `ours` 改为 `have`。
3. 任何 UI 复刻只借结构，不复制即创的 Logo、文案、音色名、音乐与模板素材。
