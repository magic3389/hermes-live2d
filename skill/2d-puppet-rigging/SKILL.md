---
name: 2d-puppet-rigging
description: Use when 要把立绘或 PSD 做成会动的 2D 傀儡模型（Iki/Live2D 类）：切层、绑定、导出。
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [live2d, iki, rigging, avatar, puppeteer, web, vite]
---

# 2D 傀儡模型绑定（Iki = Live2D 的开源替代）

## When to Use

要把立绘做成会眨眼、张嘴、转头、能被语音驱动的 2D 角色（桌宠、分身 App 形象、vtuber），
但不想碰 Cubism 授权。成品是 `.iki`（JSON），播放器 80KB，不背编辑器。

本机路线：开源 **Iki**（`refs/iki`：`packages/engine` 播放、`packages/format` 校验、
`packages/editor` 编辑器、`packages/mcp` 切层/绑定）。

**管线已脚本化**，通用脚本在
[`github.com/magic3389/hermes-live2d`](https://github.com/magic3389/hermes-live2d)：

| 脚本 | 作用 |
|---|---|
| `pipeline/matting/rmbg_cut.py` + `extract_subject.py` | 抠图（背景有干扰时）→ 剔碎片 → 居中正方白底 |
| `pipeline/run_seethrough.sh` | See-through 拆层 → 17 层 PSD（约 7 分钟） |
| `pipeline/postprocess.py` | 清底膜 · 分区换色 · 合层 · 补洞 · backdrop · 语义层序 |
| `pipeline/auto_split_legs.py` | 双腿全自动拆分 |
| `pipeline/build_iki.py` | PSD → `.iki`，枢轴全部由 bbox 推导 |

换一张完全不同的立绘，这五个脚本**不用改任何常数**。完整流程与三例实测数据见
`references/pipeline.md` 与仓库 `docs/01_pipeline.md`。

---

## 先分清素材，别把能力说大

| 素材 | 能做到 | 入口 |
|---|---|---|
| 命名分层 PNG（face/eye_L/mouth…）或分层 PSD | 全套绑定：眨眼、张嘴、头转、视线、物理摆动，秒级 | 编辑器「导入图层组」 |
| 单张成品图 | 整张作为一个部件：能摆位、能整体变形，**不会眨眼张嘴** | 「导入单张图片」 |
| 单张成品图且要会动 | 必须先切层；切层**不含 ML** | See-through 出 17 层 PSD |
| 要求「被刘海遮的额头、眼睑下的眼白也完整」 | 单图切层做不到；生成式拆件只补到 **36–54%** | 同上，但不承诺全补 |

**已有 See-through PSD 时先复用**：工程里躺着 `out/layerdiff_output/*.psd` 就是可用上游资产，
另起一套单图切层等于丢掉已付成本，换来层数更少、还带层缝的结果。开工先 `ls` 一遍。

### ⛔ `@ikijs/mcp` cut 管线只算调试件

按颜色 + 形态学硬切，稳定只出 **14 层**（`face/nose/mouth/eye_*/iris_*/lash_*/hair_front/hair_back/body`）。
不认识四肢、不认识尾巴，头发只有前后两层，每层像素硬切互不重叠。用户会直接判「还不如上一个 live2d」。
**要交「会动的模型」时先自检**：头发 / 眼白 / 虹膜 / 睫毛 / 眉 / 嘴 / 上衣 / 手 / 腿袜 / 鞋 / 尾巴
各自独立成层吗？有一条答不上就直接上 See-through。

---

## 13 条铁律（细则见 references）

1. **分离/切割的基准必须是未处理的原始层**，不能拿自己上一版产物当基准（会循环论证）。
2. **「某层上多了东西」先查谁盖在它上面**，再查这层自己的像素。已三次同一模式：脖←上衣、耳←头饰、腿←裙子。
3. **判断「两个部件有没有真分开」必须两个数一起看**：参数相关 < 0.85 **且** 接缝宽度 ≈ 原图。
   只看一个必然调错方向（看缝→改同源→融合；看相关→改异源→撑缝）。
4. **不要用 alpha 羽化消接缝**。两层颜色掺混（`a=0.5` 时各掺一半）会让它们**视觉上融合成一条**。
   接缝靠**重叠带宽**解决。
5. **「部件不动」先调奥行き / 物理 `scale`，拆层是最后手段。** 连续整块 = 一个 part，绕根部转，不拆段
   （拆段必错位露缝，绑同一参数又变刚性整体）。
6. **前发 / 后发 / 呆毛不能拆**。切口露的是**下一层内容**（脸皮、后发），不是透明背景，
   所以「透明底量缝」查不出来、补边也救不了。
7. **切之前先数连通域**：1 个连通域 + 无颜色分界 = 没有可切的结构，任何切线都是自己画的。
   细分只有两种合法依据：**连通域**、**颜色/语义**。按坐标切一律被用户点破。
8. **`PSDImage.new(mode="RGBA")`**。`mode="RGB"` 会让每层 alpha 静默变 255，
   症状是「只有最上面几层有物理」，极易误判成「工具不支持那些部件」。
9. **层序按语义画序写 PSD（先写=在下）**，不能按面积排。实测合成差 28.03 vs 5.30。
10. **`psd.composite()` 不是原图**（偏亮约 25）；颜色源必须 `Image.open(原图)`。
    但也不能整张无条件替换——各层 alpha 有溢出，会拖出别人的身体。判据：本层色 ≈ 原图（差 ≤60）才换。
11. **层 `topil()` 尺寸 ≠ 画布**（可带 offset 或被裁切），必须按 `l.offset` 贴回全画布。
12. **See-through 底膜（alpha 1–60 铺满画布）必须清零**；**整块漏层**要加画序最底的 `backdrop` 兜底层。
13. **像素验收四要素**：1:1 原生尺寸 · 屏蔽背景（只算两边都有内容）· 关缓存 · cache-bust 不能覆盖 `?nodrive=1`。
    判定看数字不看 VLM（它会把原图本就有的描边当成「多余的线」）。

---

## 起服务

```bash
# 编辑器（5174）
cd refs/iki/examples/editor
node node_modules/vite/bin/vite.js --port 5174 --host 127.0.0.1

# 播放器常驻 5200（别抢）
curl -s --noproxy '*' http://127.0.0.1:5200/ | grep -o '<title>[^<]*</title>'
```

- 用 `explorer.exe "http://127.0.0.1:5174/"` 打开；渲染是否正常用 `browser_exec(local=True)` 看
  `document.body.innerText` + canvas 数量 + 截图，别只看 curl 200。
- workspace 包要有 `dist`；`packages/mcp` 默认没有：`pnpm --filter @ikijs/mcp build`。
- 改过 packages 源码必须重新 build，否则脚本用的还是旧产物。

## 用户一定会问的三个入口

1. **撤销**：功能一直在 `store.undo/redo`，只是按钮初态灰、无快捷键。补 `Ctrl+Z` 时要
   `if (焦点在 input/textarea/contenteditable) return`，把撤销让给浏览器原生。
2. **导入**：原来只有「导入图层组」，且按**文件名**卡 role，乱命名 / jpg 全被拒。
   「没有导入的地方」是真实缺口，不是用户没找到。加「导入单张图片」时 UI 必须**明写**
   「整张作为一个部件，不会眨眼张嘴」。
3. **自动识别**：切层要 landmarks，本机没有可用的二次元人脸关键点模型（照片域 DWPose 在动漫图约 30%）。

## 验收：先让它在播放器里动起来

`cut → autoRig` 只代表文件写出来了，必须自己加载一次并逐项回读参数。

```js
await fetch('/model.iki').then(r=>r.text()).then(t=>window.iki.load(t))   // load 吃文本，不吃 URL
window.__h = setInterval(() => window.iki.set('ParamMouthOpenY', 0.9), 16)  // 待机会覆盖，要持续重设
window.iki.get('ParamMouthOpenY')                                          // 回读才算真设上
```

- **`canvas.width` 必须是模型画布尺寸**（不是 300×150）。后台标签页把 rAF 和 setInterval 一起节流，
  resize 根本没跑，截到的是 300×150 放大图，1px 的层缝会被看成 4px。先 `Page.bringToFront`
  + `Emulation.setFocusEmulationEnabled` + `Emulation.setDeviceMetricsOverride`。
- `capture_screenshot()` 每次返回**同一路径**，连拍会互相覆盖 → 拍完立刻改名。
- 判「某部件动没动」用**分区运动量**，不要看缩略拼图：
  `python scripts/measure_motion.py <帧目录> --heat heat.png --gif out.gif`。
- 判「有没有露底层」用**肤色像素数**，摆动帧不得高于静止帧：
  `python scripts/measure_underlayer_leak.py <帧目录>`。
- 判「旋转/摆头是否生效」用上半张像素差（均值差 <2 才是真没动），不要问 VLM。

## 交付口径

- 交付物按**标准命名 PSD** 设计，让美工能参与；别把「先转成自家格式」当默认步骤。
- 交模型必须同时给：`.iki` 路径、层目录、四格效果图、**能动的参数清单**、以及下面的固有限制。
- **默认表演照原图叙事**，不能套通用笑脸：先写「她正在干什么 + 什么情绪」，再翻成参数。
- **测运动的帧不是交付动画**。量完立刻撤驱动、姿态归零，另拍一遍纯 idle 作为交付片。
- **调参工具不是播放器**。交付要另起一个「打开就在播」的页面（GIF 或本地 http 服务），
  并用一张表说清每个端口是什么。
- 逐部位迭代时**一轮只动他点名的那一处**，并主动交代这一改的代价。
- 卡在视觉判断上时，出「同框同倍」的成对局部图 + 编号问题让用户肉眼判，不要反复猜。
- **回归比缺功能严重**：改编辑器只做加法，不碰既有导入/绑定路径。

## 固有上限（要主动说，不是 bug）

| 限制 | 说明 |
|---|---|
| 补画是部分的 | 背遮区域实测只补 36–54% |
| 没有 lip set | 张嘴 = 把嘴线纵向拉大，开度 0.9 有锯齿感 |
| Q 版转头幅度小 | 头大脖短，±30° 像素差只有 ~10，肉眼仍是正面 |
| 单图切层层缝 | 每层硬切互不重叠，靠补边压到 0.0x%，压不到 0 |
| 缺内容补不了 | 口腔、侧脸内耳、被完全遮住的皮肤 |

---

## references 索引

| 文件 | 什么时候查 |
|---|---|
| `references/pipeline.md` | 四段管线的完整流程、命令、三例实测数据 |
| `references/layer-splitting-rules.md` | 切层 / 拆腿 / 补洞（含 §7 自动拆腿完整判定流程）|
| `references/rigging-rules.md` | 绑定：deformer、枢轴、物理、层序、对称件 |
| `references/acceptance-rules.md` | 验收与诊断：所有可量化判据 |
| `references/iki-hand-rig.md` | 手写 `.iki` 的坐标系、atlas、pivot 换算、物理参数标定表 |
| `references/layer-gap-quality.md` | 层缝量化、补边旋钮、什么改动反而更差 |
| `references/landmark-authoring.md` | `@ikijs/mcp` cut 的 landmark 逐点标法 |
| `references/psd-consumer-tools.md` | 现成能吃 PSD 并自动绑定的工具 + 层名规约 |
| `references/see-through-deployment.md` | See-through 部署、显存、耗时、保真度实测 |
| `references/apose-generation.md` | 要自己生成绑定素材（A-pose）时的配方 |

## templates / scripts

- `templates/landmarks-min.json` — cut 管线的最小 landmark 骨架
- `scripts/probe_landmarks.py` — 像素游程扫描定眼框/虹膜（别让 VLM 打坐标）
- `scripts/apose_check.py` — 三段 75% 判 A-pose（只对骨架图标定）
- `scripts/bg_whiten.py` — 背景 flood-fill 归纯白 + 四角验收
- `scripts/zoom_face.py` — 头区/嘴部放大拼条
- `scripts/measure_motion.py` / `measure_underlayer_leak.py` / `measure_gaps.py` /
  `diff_against_original.py` — 运动量 / 露底 / 层缝 / 差异统计
