# 贴纸 / 花字 / 挂坠 / 动态素材库调研

日期：2026-10-23。只记录可商用许可与实际验证过的来源，不把「看起来很全」当成可用。

## 结论

没有一套「即创同款中文花字 + 挂坠 + 动态」的 GitHub 一键素材库。可安全复用的是**图标/表情**和**程序化动效**；中文花字与挂坠只能自建模板，或使用明确许可的字体与 CC0 形状。

## 可用来源

| 来源 | 内容 | 许可 | 结论 |
|---|---|---|---|
| `microsoft/fluentui-emoji` | 1500+ 3D/Color/Flat 表情，含皇冠、绶带、奖章、铃铛、奖杯、标签、硬币、链环、手袋、钱包 | MIT | **已接入**，贴纸菜单从 26 扩到 36 |
| `twitter/twemoji` | 3000+ 扁平 emoji SVG | 代码 MIT；图形 CC-BY 4.0，需署名 | 可作补充，必须带署名；暂不混入现有菜单 |
| `googlefonts/noto-emoji` | Noto 彩色 emoji 字体/SVG | OFL-1.1 / Apache-2.0 | 可作字体回退，不直接当贴纸 |
| `iwenzhou/kenney` | Kenney 全量素材，UI/奖章/棋盘/形状，CC0 | CC0-1.0 | 可商用；游戏像素风，和电商花字不搭，只适合做挂坠/边框参考 |
| `cyanidecupcake/openclipart-svg` | Openclipart SVG 合集 | CC0-1.0 | 2.1GB，需按需取单个 SVG，不整包克隆 |
| `airbnb/lottie-web` | Lottie 播放器 | MIT | 播放器可商用，但**动画 JSON 本身许可另算** |
| `useanimations/react-useanimations` | React 动画组件 | MIT | 组件 MIT，内置动画素材需逐个核许可；不适合直接进 ffmpeg 出片 |

## 不采用

- 剪映/即创/CapCut 模板拆包、所谓「花字合集」网盘包：来源与授权不清，不能进商业项目。
- LottieFiles 免费动画整包：很多是「免费使用但禁止转售/再分发」，且需逐个记录作者与许可，不批量接。
- OpenMoji：CC BY-SA 4.0，闭环商业素材有 share-alike 风险。
- JoyPixels：免费版许可限制商用，不做默认素材。

## 已落地

- 贴纸：26 → 36，新增 `crown / rosette / ribbon / bell / trophy / tag / coin / chain / handbag / purse`，全部 Fluent Emoji 3D，256×256 RGBA。
- 花字：10 → 14 风格，新增 `ribbon_badge`（绶带横幅）、`pendant_tag`（吊牌挂坠）、`seal`（圆形印章）、`frame_gold`（金色画框），PIL 本地绘制，无第三方文字素材。
- 动态：贴纸进入窗口时 0.16 秒淡入 + 30px 侧滑，结束前 0.12 秒淡出；花字已有 0.2 秒淡入/0.15 秒淡出。
- 验证：Mac 单测 + Windows 原生 `_apply_stickers`/`_apply_flower` 抽帧，皇冠与绶带横幅均正常。

## 后续可加

- Fluent Emoji 继续扩到 60–80 个常用语义（脸、手势、工具、天气），只改 `ICON_MEANINGS` + 资产 + 一致性测试。
- 用 `google/fonts` 的 OFL 字体（站酷快乐体、马善政、龙藏）做字体型花字，需附 OFL 许可。
- 若要多帧 Lottie 动效，先在独立渲染器里把 Lottie 转 PNG 序列，逐条记录动画许可；不要直接塞进现有 ffmpeg 链。
