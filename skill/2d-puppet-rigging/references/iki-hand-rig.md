# 手写 .iki：从分层 PSD 直接生成带独立枢轴的傀儡

自动绑定工具（Anime2.5DRig 一类）的枢轴是它自己按连通域算的，改不了：尾巴横向伸出时会被钉在
尾鳍尖、`legwear` 压根没有摇摆属性、`mouth` 还会自己缩放。要做到「尾巴整条从根摆、梢部摆幅最大、
嘴不动、腿能微动」，只能自己写 `.iki`——`parts` 是自由数组，想加多少层都行。

格式定义看 `refs/iki/packages/format/src/types.ts`（467 行，读它比读文档快）；引擎实现看
`packages/engine/src/{iki-motion,hair-chain-motion,warp,idle-motion}.ts`。

## 坐标系与 mesh（写错这里整张图会错位）

- `part.transform.x/y` 是**中心位置**，model 空间：原点 = 画布中心，**+y 向上**。
  从 PSD 像素换算：`mx = px - W/2`、`my = H/2 - py`。
- `part.width/height` 是模型空间尺寸（像素即可，画布本身也是像素）。
- `mesh.vertices` 是 **local ±0.5** 单位空间，**顶行 y = +0.5**（对应 uv 的 v=0，即图的上沿），
  底行 y = −0.5。写成“y 向下递增”会让每层上下翻转。
- `mesh.uvs` 与 `vertices` 平行，直接给 atlas 归一化坐标，不做翻转。
- `texture.source` 必须是 `data:image/png;base64,...`（v1 只收 `data:image/` URI）。
- `order` 小的先画（在更后面）。

```python
def grid_mesh(cols, rows, uv):
    verts, uvs, idx = [], [], []
    for r in range(rows + 1):
        v = r / rows
        for c in range(cols + 1):
            u = c / cols
            verts += [u - 0.5, 0.5 - v]          # 顶行 +0.5
            uvs   += [uv["x"] + u*uv["width"], uv["y"] + v*uv["height"]]
    stride = cols + 1
    for r in range(rows):
        for c in range(cols):
            a = r*stride + c; b = a + 1; d = a + stride; e = d + 1
            idx += [a, d, b, b, d, e]
    return {"vertices": verts, "uvs": uvs, "indices": idx}
```

## 切块 → 打包 atlas

- **bbox 用 `alpha > 128` 求核心，再向外扩 8 px 保留软边**。用 `alpha > 8` 会被抗锯齿噪点撑成整张画布：
  22 层全部 1280×1280，atlas 长到 1336×21917、base64 16.7 MB，模型 22 MB。阈值修对后同一套层
  是 1369×1661、base64 2.4 MB、模型 2.5 MB。
- 货架打包（按高度降序、行高取最大）足够，不必上 maxrects；层间留 2 px。
- 分段切（把一整块切成几段给不同枢轴）**默认不做**。勉强要切也必须重叠 30–40 px + 羽化
  （`slice_x(img, x0, x1, soft=18)`），但要清楚静态合成看不出问题：VLM 看拼图会判「没有接缝」，
  而在播放器里转起来段界就错位露缝。验收层缝只能在**转动起来的帧序列**上量，不要在合成图上量。
- 想加层但不改图时，直接按 mask 相乘切：`a[:, :, 3] = (a[:, :, 3] * mask).astype(np.uint8)`。

## parts / deformers / physics 三张表

每个 part 只能挂**一个** deformer，所以「同一块图要分节摆动」= 拆成几个 part，各挂自己的 deformer。

```json
// deformer：matrix + 一条 rotate binding（参数范围线性映射到角度）
{"kind":"matrix", "id":"def_Tail2", "parent":"def_Tail1",
 "pivot":{"x":-190,"y":-210},
 "bindings":[{"parameter":"ParamTailSeg1","channel":"rotate","from":-60,"to":60}]}
```

binding 的语义是「参数值先按 `[min,max]` 归一化到 0..1，再映射到 `[from,to]`」，不是直接相加。
所以同一个参数可以给多个 deformer 配不同 `from/to`，得到幅度递增的串联效果。

**枢轴按解剖位置算，别用 bbox 中心**：尾巴根在靠身体那端、耳根靠头侧、发根在头顶、
裙腰在下装顶端、髋在 legwear 上沿。Q 版尾巴横向伸出时「根部」是 x 大的那一端。

**耳朵枢轴必须放在贴头那一侧（内端），放外端就是「漂浮感」。** 症状：耳朵看着像悬在头外边绕自己
打转、跟头没有连接。Q 版鳍状耳在 1280 画布上 `ear_L` 内端 x≈622、`ear_R` 内端 x≈730，枢轴 y 取耳根
（≈268）。想做出「动物耳朵抖一下」的手感：`stiffness 96 / damping 3.2`（硬且回弹久）、输出 `scale 150–210`、
binding rotate ±20°；太软（stiffness 60）会变成慢悠悠地飘。

## 尾巴：一整块绕尾根转（拆段=撕裂，同参数=单图层）

先把三条**不要再试**的记下（都实测过）：

- `physicsChains` 在引擎里**只跟 anchorDeformer 的矩阵**（`hair-chain-motion.ts`）。anchor 是静态
  matrix 时，即使把 binding 拉到 ±90 px / ±34°，`ParamTailSeg1..3` 摆幅只有 **0.01 / 0.02 / 0.08 度**，
  等于冻结。加刚度阻尼、改重力、放大 binding 全无效。
- anchor 反过来被链输出驱动也不行，加载直接报
  `physicsChains[0].segments[0].output.parameter "…" feeds its own anchor deformer chain (feedback)`。
- 两个 `physics` 互相串也报 `physics[0].output.parameter "…" is used as a physics input (feedback loops are not supported)`。

**也不要拆段。** 根/中/梢三段各挂自己的 deformer 是上一版做法，用户当场否掉两条：静态合成看上去无缝
（VLM 也判「没有接缝」），但在播放器里转起来段界就错位露缝（「撕裂感又出来了」）；三段绑同一参数则
同相位刚性转动，看成一整块平移（「变成了单图层了」）。

**可用做法：尾巴 = 一个 part，一个 matrix deformer，枢轴在尾根。**

```json
{"kind":"matrix", "id":"def_Tail", "parent":"def_Body",
 "pivot":{"x":-12,"y":-190},
 "bindings":[{"parameter":"ParamTailSway","channel":"rotate","from":-24,"to":24}]}
```

```json
"physics": [{"id":"tail",
  "inputs":[{"parameter":"ParamAngleX","weight":1.0,"mode":"lag"},
            {"parameter":"ParamBodyAngleX","weight":0.9,"mode":"lag"},
            {"parameter":"ParamAngleZ","weight":0.6,"mode":"lag"}],
  "output":{"parameter":"ParamTailSway","scale":420},
  "mass":1,"stiffness":22,"damping":2.2}]
```

- `scale` 要给到几百：写 26 时输出只有 ±1.4 度（乘以 24/30 的映射≈1 度），肉眼看不出；
  `scale: 420` 实测输出 ±23 度（参数满量程 ±30），梢部甩幅极大。
- 枢轴取「尾巴靠身体那一端的中点」：Q 版尾巴横向伸向画布左下，根部就是 **x 大的一端**。
  取 bbox 中心会让尾巴绕自己腰转，尾尖反而几乎不动。
- 整块旋转的额外好处：尾尖离枢轴最远，摆幅天然最大，正好就是「尾巴尖上下摆动」这个需求。

**warp deformer 对横向尾巴实测无效**：grid 8×3 覆盖尾 bbox、keyform 从梢到根递减、
手动把 `ParamTailSway` 设到 ±30，尾鳍尖只位移 **0.3–2.8 px**。别在 warp 上耗时间。

## 头发：也要一整块，而且要在切层阶段就不切

这条是尾巴那条的延伸，但**症状和排查方式不同**，单独记：

- 从 `front hair` 里切出 `ahoge` / `side hair`、把 `back hair` 按 y 切三段，静态把三块叠起来和原图一模一样，
  所以合成图、层数统计、绑定诊断全部正常。**一旦各自绕不同枢轴转动，切口就错开，露出下面的脸（额头肤色）
  或后发底层**。用户会直接说「前发还有侧面有很明显的开缝 露出额头底层」。
- 补边（alpha 外扩）救不了：露出来的是**下一层的内容**，不是透明背景。
- 正解是回到切层脚本把它合回去，让 `front hair` 与 `back hair` 各是一整块。合并后 bbox：
  `front hair` 345×449（含呆毛+刘海+两侧鬓发）、`back hair` 430×229 起。全模型 part 数基线：不拆腿鞋时 **19**，
  `back hair` / `front hair` / `tail` / `ear_L` / `ear_R` / `headwear` / `face` / `nose` / `mouth` /
  `eyebrow` / `eyewhite` / `irides` / `eyelash` / `topwear` / `bottomwear` / `handwear` /
  `leg_L` / `leg_R` / `shoe_L` / `shoe_R` / `neck`。
- 代价必须提前讲：**侧发**不再能独立弹动，只能跟着整片前发摆。要兼得只有在 PSD 里给侧发补画
  一块额头衬底（under-paint），属于画图工序。
- **呆毛是唯一可以安全切出来的头发。** 它长在头骨外侧，下方压着的是头顶头发而不是脸皮，所以切口
  不会露肤色。做法：`ahoge = front hair ∩ (y < 160)`、`front hair = front hair ∩ (y >= 130)`（重叠 30 px，
  根 y≈128，重叠区被呆毛自己盖住）。挂 `def_Ahoge`，枢轴在根 `px(650,155)`，binding rotate ±26°，
  物理 `stiffness 24 / damping 1.8 / scale 65`。实测自然 idle 下呆毛区每帧 7800–17000 px 在变、
  质心漂 4.4 px、根部不露底。注意钩形呆毛绕根转的视觉是「弧度高低」而不是左右甩，
  别拿「方向没有反转」判失败。
- 摆动幅度也要收：整片前发带着呆毛和鬓发一起走，`def_FrontHair` 的 rotate 用 ±9°、
  `def_BackHair` 用 ±7°，比拆层时的 ±18/±25 小一档，否则发梢甩得太夸张。

**验收：数 bbox 内的肤色像素，不要问 VLM。** VLM 对一两像素的缝两次都答「没有异常」——
它在缩略拼图上读不出来。做法是取前发 bbox，按 `r>195 & g>160 & b>140 & 18<r-b<95 & g-b>5` 数肤色像素，
逐帧比静止帧：**任何一帧高于静止帧就是开缝**。实测合层后 59 帧最大增加 **0 px**（静止 3406，
其余帧 1182–1262，因为头一转头发遮得更多）。脚本章 `scripts/measure_underlayer_leak.py`。

## head / body 两级总成（缺了就是「头和头发被撕开」）

只给头发/尾巴挂 binding、不给脸挂，转头时就是「头发在动、脸不动」，用户会直接判撕裂 —— 这是比段缝更
根本的结构缺口。必顶两层：

```json
{"kind":"matrix", "id":"def_Head", "pivot":{"x":10,"y":210},
 "bindings":[{"parameter":"ParamAngleX","channel":"rotate","from":-11,"to":11},
             {"parameter":"ParamAngleY","channel":"translateY","from":-10,"to":10},
             {"parameter":"ParamAngleZ","channel":"rotate","from":-9,"to":9}]},
{"kind":"matrix", "id":"def_Body", "pivot":{"x":0,"y":-180},
 "bindings":[{"parameter":"ParamBodyAngleX","channel":"translateX","from":-16,"to":16},
             {"parameter":"ParamBodyAngleZ","channel":"rotate","from":-4,"to":4}]}
```

- `def_Head` 枢轴在**脖子**（Q 版 1280 画布上约 `px(650,430)`），`def_Body` 枢轴在**腰**（约 `px(640,820)`）。
- 没有自身物理的件**直接挂总成**：`part.deformer = "def_Head"`（face / nose / mouth / eyebrow /
  eyewhite / irides / eyelash / headwear）、`"def_Body"`（neck / topwear / handwear）。
  ⚠️ 腿和鞋**不要**挂 `def_Body`：那样只有整体平移、没有自己的摆动，而且腿一旦有自己的弹簧，
  鞋留在 `def_Body` 上就等于「腿动鞋不动」。腿/鞋按下面「双腿独立」一节走。
- 有物理的件（发、耳、呆毛、裙、腿、尾）把自己 deformer 的 `parent` 指向这两级，先整体跟着走、
  再叠加局部形变。
- 症状对照：脸不动而头发动 = 脸的 part 没挂 `def_Head`；头身交界露缝 = 脖子/领口那层没挂 `def_Body`。

## 物理只在 `IkiMotion.update()` 里跑

`IkiPlayer` 自己不 tick 物理。只调 `player.setParameter(...)` 时，所有物理输出参数**永远是 0**，
画面看起来完全静止——很容易误判成「模型写坏了」或「引擎不支持」。

```ts
const motion = new IkiMotion(model,
  (id) => player.getParameter(id),
  (id, v) => player.setParameter(id, v));

const tick = (now) => {
  // 1) 先写外部驱动（头/身的缓慢摆动）
  player.setParameter("ParamAngleX", 3.6 * Math.sin(2*Math.PI*s/5.0));
  player.setParameter("ParamBodyAngleX", 2.0 * Math.sin(2*Math.PI*s/6.5));
  // 2) 物理在这里算（弹簧 / 角摆链）
  motion.update(now);
  requestAnimationFrame(tick);
};
```

顺序不能反：先写驱动、再 update，否则物理读到的是上一帧的角度。
多个轴用**互质周期**（5.0 / 6.5 / 7.5 / 4.0 秒）叠加，才不像机械来回。

`model.motions.Idle` 在播放器里**不会自动播**（播放器用的是自己的 `IkiMotion`），别指望写个 idle clip 就动起来；
驱动要自己写。

## 独立页播放（vite dev 直接服务根目录 html）

examples/player 的 vite 会把项目根的任意 `.html` 当入口，所以只需两个文件，不必改 `main.ts`
（那个文件背着 Android 的 `window.iki` 封装，改动回归风险大）：

- `examples/player/standalone.html`：`<canvas id="c">` + `<script type="module" src="/src/standalone.ts">`
- `examples/player/src/standalone.ts`：上面那段 tick

关键一行：`canvas.width = model.canvas.width; canvas.height = model.canvas.height;`
不设就停在 HTML 默认 **300×150**，截图是放大 4 倍的糊图。

访问 `http://127.0.0.1:5200/standalone.html?model=/model_hand.iki`。

**验单个 deformer 用 `?nodrive=1`。** `motion.update()` 每帧重算物理输出参数，手动 `setParameter`
立刻被覆盖（实测设 25 回读 4.39），会误判成「deformer 没生效」。在 tick 里加一个开关：

```ts
const noDrive = new URLSearchParams(location.search).has("nodrive");
const tick = (now) => {
  if (noDrive) { requestAnimationFrame(tick); return; }   // 参数完全由控制台摆布
  /* …写驱动 + motion.update(now)… */
};
```

之后在 `?nodrive=1` 下手动 `set('ParamX', ±满量程)` 截图做像素差分，就能证明 deformer 接没接上
（实测呆毛 ±25 → 5492 px 差异、耳朵 ±14 → 21298 px）。

## 验收数字（这条路线跑通后的基线）

60 帧自然 idle，分区平均灰度差（1100 画布，整块绕根转的版本）：呆毛 14.45、脸 21.40、
耳朵 13.34、后发 14.38、尾根 13.39、尾中 6.12、尾鳍尖 3.31、裙 12.56、腿 20.32；
嘴 0.01（不绑任何参数就完全不动）。

- 尾巴摆得很大时固定裁剪框会**低估**数字：尾鳍出了框，框内只剩根部的位移。看到“尾巴梢数字小”先
  核对框是否跟得上，别直接判“没摆”；判形状要看 ±满量程的手动 set 对比图（`set('ParamTailSway', ±30)`）。

- **嘴只有在不绑任何参数时才不缩放**。想让它动就绑 `ParamMouthOpenY`，别像自动绑定那样给它
  一个会自己振荡的通道（用户对「嘴一会大一会小」的反应是直接判成品报废）。
- 腿：**不能只给一条 `legwear`**。要按左右拆四个 part —— `leg_L` / `shoe_L` / `leg_R` / `shoe_R` ——
  并且**同一侧的腿和鞋挂同一个 deformer、共用同一个枢轴**（枢轴在髋，`px(612,800)` / `px(722,800)`）：

  ```json
  {"kind":"matrix","id":"def_LegL","parent":"def_Body","pivot":{"x":-28,"y":-160},
   "bindings":[{"parameter":"ParamLegSwayL","channel":"rotate","from":-5,"to":5}]}
  ```

  两腿要「独立」= 吃**不同输入 + 不同刚度**，相位自然错开：左腿 `ParamBodyAngleX` 权重 0.8 / 刚度 40 /
  阻尼 4.6，右腿 `ParamBodyAngleZ` 0.8 / 刚度 31 / 阻尼 3.8，输出 `scale` 30。
  幅度按用户口径收小：rotate 只给 ±5°（末端位移约 30 px），「卖萌用不需要大动作」。
- **腿怎么分：逐行沿轮廓，绝不能垂直直线切。** 按 `x<660` 一刀切会把左腿右半边划给右腿、右腿左半边
  划给左腿，用户会点名「直挺挺画了个区域，把左边的腿的一部分裁切了 右边的脚的一部分也裁切了」。
  watershed 也不行：两个种子大小悬殊（实测 8734 / 5526 px）时大地形会吞掉并拢段，`leg_L` 只剩 4919 px。
  可用做法（纯 numpy、秒级）：从最下面一行往上扫，每行求 alpha 的连续段 —— 两段及以上就直接分左右
  （分界取两段之间空隙的中点）；只剩一段（大腿根并拢）就沿用它下方已知的分界线切开。切完对分界线
  按 y 做 11 行滑动平均，避免锯齿。自检：`L px + R px == 原 legwear 前景 px`、未分配为 0
  （实测 19489 + 23900 / 未分配 0）。切口天然落在两腿实际相接的最细处，而那一段正好在裙下
  （裙下沿 y≈906、腿根 765）。鞋按原图天然空隙切（`footwear` 在 x 631–638 有 7 px 空白）。
- 验收双腿独立：对每侧取帧序列的区域平均灰度，算左右相关 —— **负相关就是独立**（实测 −0.219），
  而「腿↔同侧鞋」应当高正相关（实测 +0.87 / +0.42，右鞋那侧被裙摆像素稀释所以偏低）。
  分区均值受整体平移影响，别拿绝对运动量当独立性的证据。
- 判「哪块有没有动」仍然用 `scripts/measure_motion.py`（分区差 + 热图），VLM 只看形状有没有崩。

## 物理 `scale` 的标定法（别猜）

binding 的 `from/to` 是**角度**，参数值先按 `[min,max]` 归一化再映射过去：

    实际角度 = (参数摆幅 / 参数半量程) × binding 角度

要「看得见的次级运动」（约 8–20°），先测 `scale` 给某值时的参数摆幅，再反算。实测对照：

| 部件 | scale | 参数摆幅 | 折算角度 | 观感 |
|---|---|---|---|---|
| 耳朵 | 26 | 1.39 | ≈1.4° | 看不出，像没动 |
| 耳朵 | 150 | 8.57 | ≈8.6° | 轻微抖动，可用 |
| 呆毛 | 30 | 11.29 | ≈5.9° | 缩略图看不出，要特写才辨得出 |
| 呆毛 | 65 | 19.77 | ≈15.8° | 明显但不夸张 |
| 尾巴 | 26 | 1.41 | ≈2.7 px 位移 | 几乎静止 |
| 尾巴 | 420 | 23.08 | ≈24° | 尾鳍甩出取景框 |

给完 `scale` 一律回读实测摆幅再换算，不要拿代码里的数字当结论。

## 量运动时的两个测量坑（会让你误判模型坏了）

- **Windows 下 `glob` 大小写不敏感**：`glob.glob('f*.png')` 会把 `FULL_frames.png` 一起匹配进来并按
  ASCII 排在 `f000.png` 之前，于是「首帧」变成一张 1705×452 的拼图 —— 角色 bbox、缩放系数 k、所有
  切片坐标全错，量出来的分区运动量是 0.00、相关性是 `nan`。帧序列一律用
  `glob.glob('f[0-9][0-9][0-9].png')`，并在取用前 `print(len(fs), Image.open(fs[0]).size)` 对一眼。
- **VLM 答「看不出动」时，先确认 crop 里真的有那个部件。** 两次按设计坐标换算裁腿脚，实际裁到的是
  裙摆（金色蝴蝶结 + 白色荷叶边），VLM 就答「四帧几乎完全一样，没有摆腿」，而同一批帧的分区数字是
  19–24。规则：分区数字与 VLM 结论冲突时，**先查裁剪框**，再查模型；裁完先看一眼里面是不是目标部件
  再拿去提问。
