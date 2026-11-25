# 06 流程手册：立绘 → Live2D 傀儡模型

> 从一张二次元立绘到会动的 `.iki` 模型，完整可复现流程。
> 本文档记录**实测有效的做法**和**踩过的坑**，不含未验证的设想。

---

## 0. 一句话流程

```
立绘 PNG → 居中成 1280×1280 白底 → See-through 拆 17 层语义 PSD
        → 后处理（合并/挖空/换色/拆腿）→ 打包 atlas + 绑 deformer/物理 → .iki
```

---

## 1. 输入素材要求（决定成败）

| 项目 | 要求 | 原因 |
|---|---|---|
| 背景 | **纯白或纯色**，无场景元素 | See-through 按语义分层，背景太杂会污染各层 |
| 构图 | **单人全身**，四肢不被画框裁切 | 裁切处的部件无法补全 |
| 姿态 | A-pose / 自然站立最好 | 手臂贴身会让袖子与躯干粘连 |
| 分辨率 | 长边 ≥ 1000px | 拆层后每块还要单独放大，太低会糊 |
| 部件 | 头发/耳朵/尾巴/眼睛/嘴/四肢/裙子各自独立可辨 | 层内粘连的东西没法单独动 |

**竖图处理**：等比缩放到高 1280，居中放进 1280×1280 白底。

```python
im = Image.open(src).convert("RGB")
h = 1280; w = int(im.width * h / im.height)
im2 = im.resize((w, h), Image.LANCZOS)
canvas = Image.new("RGB", (1280, 1280), (255, 255, 255))
canvas.paste(im2, ((1280 - w) // 2, 0))
canvas.save("input/xxx_square1280.png")
```

### 1.5 背景有干扰时：先抠图（ComfyUI RMBG）

带背景渐变、文字、其他头像框的设定图，必须先抠出**中间那个人物**，否则 See-through 会把它们当内容分层。

**本机可用的抠图节点**：`LayerMask: RemBgUltra`（RMBG-1.4，权重在 `models/rembg/` 与 `models/background_removal/`）

⚠ **两个坑**：
1. **`process_detail=True` 会报错** `cannot import name 'fix_trimap' from 'pymatting'` —— LayerStyle 的 pymatting 版本不兼容，必须设 `False`。
2. **`SaveImage` 存 RGB 会丢 alpha**，看起来"什么都没去掉"。要另外用 `MaskToImage` 导出 mask。

```python
wf = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "src.png"}},
    "2": {"class_type": "LayerMask: RemBgUltra",
          "inputs": {"image": ["1", 0], "detail_range": 8,
                     "black_point": 0.01, "white_point": 0.99, "process_detail": False}},
    "3": {"class_type": "MaskToImage", "inputs": {"mask": ["2", 1]}},
    "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "rmbg_mask"}},
}
```

**GroundingDino+SAM 在本机不好用**：prompt 宽泛（`full body girl`）时框会把整个画面选进去，
实测 07 图前景占 78.8%；而且 `GroundingDinoSAMSegment` 输出的 image **只有 3 通道**，
用 `ImageToMask(channel=alpha)` 会报 `index 3 is out of bounds`。先用 RMBG。

**抠完要剔碎片**：RMBG 会在左侧留少量零散白点（实测 84 块、共 10218px）。
按连通域面积剔除（阈值 3000px），再裁到人物 bbox、居中放白底。

---

## 2. 第一步：See-through 拆层

```bash
cd <pipeline>
bash run_local.sh ../input/xxx_square1280.png 1280 30
```

- 输出：`out/layerdiff_output/xxx.psd`（约 40MB，17 层）
- 耗时：10–25 分钟（30 步，1280 分辨率）
- 显存：约 8–12GB，跑之前确认 ComfyUI 没在生成

**必须用本地模型**（`run_local.sh`），`run_psd.sh` 会走 HF 下载。

### 17 层语义标签（固定）

`back hair` / `front hair` / `headwear` / `ears` / `face` / `nose` / `mouth` /
`eyebrow` / `eyewhite` / `irides` / `eyelash` / `neck` / `topwear` / `bottomwear` /
`handwear` / `legwear` / `footwear`

> ⚠ 这是**上游资产，不是终态**。直接拿来绑会有一堆问题，必须后处理。

### ⚠ 每层都带一层「底膜」，必须先清掉

See-through 会给某些层输出一层覆盖**几乎整个画布**的极低 alpha 底膜：

```
             alpha 1-60 的像素     真实内容（alpha>200）
c04 mouth          1,520,597              1,259
c04 eyewhite       1,252,128              4,399
c15 eyewhite       1,130,543                957
c15 face             878,744             16,422
```

这层底膜叠上去会让**整张画面发亮**（实测 c04 偏亮 24.29）。
**处理：alpha < 60 一律清零。** 真实轮廓的抗锯齿通常 >100，砍 60 以下不伤边缘。

```python
a = arr[:, :, 3]
arr[(a > 0) & (a < 60), 3] = 0
```

清完 15 号图一次砍掉 2,071,883px。

---

## 3. 第二步：后处理（关键，决定质量上限）

> 通用脚本：`postprocess_generic.py <seethrough.psd> <原图png> <输出.psd>`
> 15 号图实测：合成差 **21.26 → 5.30**（c04 是 4.76）

后处理要解决五类问题，按优先级：

### 3.0 清掉每层的「底膜」（见上节）

不清的话合成会整体发亮 20 以上。

### 3.1 ⚠ 层顺序必须按语义画序，不能按面积排

写 PSD 时**先写的在下面**。按「面积升序」写（想当然是小的在下面）会让裙子盖住脸、头发盖住眼睛。

实测 15 号图：
```
按面积升序写   合成差 28.03
按语义画序写   合成差  5.30
```

画序表见 §4.1。

### 3.2 颜色必须换回原图

**See-through 合成的图片比原图整体偏亮约 25**（实测 c04：偏亮 24.29），直接当纹理就是「蒙了一层白纱」。

```python
ref = np.asarray(Image.open(SRC_ORIG).convert("RGB"))   # 原图
# 逐层：本层 RGB ≈ 原图（差异 ≤ 60）才替换，否则保留
```

**不能整张画布无条件换色** —— 各层 alpha 都有溢出（实测 tail 14520px、handwear 5250px、headwear 6939px），
无条件换会把下层内容烤进本层。

### 3.3 该合的层要合

| 合并 | 原因 |
|---|---|
| `legwear` + `footwear` → `legs` | 鞋/袜/脚视为一个整体，拆了会鞋盖袜或袜盖鞋 |
| `apron_top` 从 `topwear` 抠出 | 围裙和裙子必须共用同一个 deformer，否则一个摆一个不动 |

**合并时补洞必须限制范围**：`legwear+footwear` 合成后，两层边界会有洞，
补洞判据是「原图非白 & 该处没有内容」。但**不能全画布补** ——
15 号图实测全画布补会补出 374,055px（整个人物才 425,781px），腿层变成 45 万 px。
必须加「靠近腿层」的距离限制：

```python
near = ndi.binary_dilation(core, np.ones((3,3)), iterations=8)
hole = (~core) & (ref.min(axis=2) < 245) & near     # 实测只补 15,135px
```

### 3.5 该补的要补：兜底层 backdrop

**See-through 会整块漏掉一些区域**（所有层 alpha 全 0，但原图那里是实体内容）。
实测 07 图 x[668,689] y[1100,1140] 一大块裙子被漏掉，拆腿后直接露出白色背景。

做法：原图非白 & 任何层都没覆盖 → 放进一张**画序最底**的 `backdrop` 层。
它不会盖住已分好的层，只是把漏掉的内容垫在底下。

```python
covered = np.zeros((H, W), bool)
for arr in layers.values():
    covered |= arr[:, :, 3] > 0
hole = (~covered) & (ref.min(axis=2) < 245)
# 去掉零星噪点（JPEG 在纯白背景上的脏点）：按连通域面积 ≥60px 过滤
```

实测 07 图补了 7779px，合成差 6.47 → **5.30**。
`build_iki_generic.py` 的 ORDER 里 `backdrop` 必须排第一（否则会盖住一切）。

### 3.6 ⚠ 层尺寸不等于画布

PSD 层可以带 offset / 被裁切。`topil()` 拿到的图可能不是 1280×1280
（实测 07 图某层是 1230×757），直接当画布会炸 `operands could not be broadcast`。

```python
im = l.topil().convert("RGBA")
ox, oy = l.offset
if im.size != (W, H) or ox or oy:
    full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    full.paste(im, (ox, oy))
    im = full
```

### 3.7 ⚠ 合并层时 `p/out_a` 会算出纯黑

`legwear + footwear` 用 over 合成，RGB 要除以 alpha。**alpha 极低处除法会出 (0,0,0)**，
而后面「颜色换回原图」的容差 60 够不到这种差异（黑 vs 蓝白差 150+），黑块就留在纹理里 ——
实测 07 图出现 2323px 纯黑块（x[630,682] y[1079,1191]）。

两道修：
1. 合并后 **RGB 直接取原图**（腿/袜/鞋内容本来就是原图）
2. 通用兜底：**本层近黑(≤15) 但原图非黑(>40) → 取原图**

拆腿时同理：**RGB 必须取原图**，不能取 `legs` 层（补色区在原 legs 里 alpha=0、RGB 是 0）。

### 3.8 该挖的要挖（"某层上多了东西"的先查谁盖着它）

这是最容易走错方向的一类问题。**先查「谁盖住我」，再查「我自己的像素」**。

| 现象 | 真因 | 判据 |
|---|---|---|
| 脖子半透明/灰紫 | `topwear`(order 8) 盖住 `neck`(order 4) | 挖 topwear：`原图色 ≈ neck 色（≤30）` |
| 耳朵底下有只静止的耳朵 | `headwear` 把耳朵也画进去了 | `headwear` 在耳朵 mask 处挖空 |
| 腿上有条暗线 | `bottomwear` 错拿了裙摆下沿的腿 | `bottomwear 有像素 & 有腿 & |bottomwear_RGB − 原图| > 40` |

### 3.9 该拆的要拆（腿）—— 现已自动化

双腿拆成 `leg_L` / `leg_R` 才能左右分开摆。

**通用脚本：`auto_split_legs.py <split.psd> <原图png> <mask前缀>`**

它从像素结构自动推导所有参数，不再手测：

1. 逐行测「段数」→ 找分叉点 `fork_y`（连续 5 行都是 2 段的第一行）
2. **分叉点位置决定路线**：有合并区 → 上段沿前腿实测宽度切；无合并区（腿一上来就两条）→ 直接按连通域
3. **前腿宽度 `FRONT_W` = 分叉区左段宽度的中位数**（⚠ 不能用「单段行」的宽度：
   单段行往往只剩鞋底几行，实测 07 图只有 3 行、算出 37px，真实腿宽是 76px，导致鞋被整块划给一条腿）
4. 单段行要分两种情况：**明显偏左/偏右 ⇒ 只是其中一条腿存在，整块归它**；
   横跨中线且够宽 ⇒ 两腿连成一片（鞋靠一起），按 `FRONT_W` 切
5. 边界放到描边外侧（逐行找亮度 <110 的突变）
6. 输出**保留原始抗锯齿 alpha**（不 0/255 硬切，不羽化）

⚠ **必须以「未拆分的原始 legs 层」为基准**，绝不能拿自己上一版产物当基准（循环论证）。

**⚠ 最坑的一个 bug：numpy 广播**

```python
xx = np.arange(W)[None, :]        # (1, W)
split_x                            # (H,)
m_front = upper & (xx <= split_x)  # 广播成「第 x 列 vs split_x[x]」—— 行列完全错位
```

不报错，只是边界永远不生效。修法：`split_x[:, None]`。
**自检**：赋值后立刻打印「设计值 vs 实际结果」并排比。

### 3.10 腿摆：用 translateX，不要用 rotate

腿很长（07 图 350px），绕腿根转 1.3° 就会让脚踝横移 8px，
把本来只有 1px 的两腿间隙拉成 19px（实测 y=1120 +18px）。

腿基本被裙子盖住、只露小腿时，**整体平移不会变形，也不会撑开缝**：

```python
mat("def_LegL", PIVOT["leg_L"], "ParamLegSwayL", 3.0, parent="def_Body", channel="translateX")
```

配套把物理 `scale` 降到 8（from/to 是 ±3，参数范围 ±8，scale 24 会让参数一直饱和）。

实测 07 图：静态腿位置与原图**逐行完全一致**（左右差 +0），动态分界缝 +2px。

---

## 4. 第三步：绑定成 .iki

### 4.1 画序（低 → 高）

```
back hair → tail → leg_L → leg_R → neck → bottomwear → handwear →
apron_top → topwear → headwear → ear_L → ear_R → face → nose → mouth →
eyebrow → eyewhite → irides → eyelash → front hair
```

原则：**后面（远）的先画，前面（近）的后画**。

### 4.2 两级总成必须有

- `def_Head`（pivot = 脖子位置）：所有脸/五官/头饰/头发挂它
- `def_Body`（pivot = 腰）：躯干类挂它

没有这两级，转头时只有「绑了 ParamAngleX 的件」在动，头和脸会被切开的错觉。

### 4.3 各部件的枢轴（决定动起来自不自然）

| 部件 | 枢轴位置 | 说明 |
|---|---|---|
| `back hair` | 后脑勺发根 | bbox 顶部中心 |
| `front hair` | 刘海根 | bbox 顶部中心 |
| `ear_L/R` | **各自的内侧端**（贴着头那一侧） | 不是外端，也不是共同中点 |
| `bottomwear` | 裙腰（bbox 顶部中心） | |
| `leg_L/R` | **各自腿根的左右位置** | 不是身体中线 |
| `tail` | 尾根（bbox 顶部中心） | |

### 4.4 物理：`输出 ≈ (x − L) × scale`

读自 `packages/engine/src/physics-motion.ts`：

- `weight` 只是输入占比，**振幅靠 `scale` 补**
- 想让某部件动得明显：`scale` 调大，不是 `weight` 调大

### 4.5 左右对称件：必须 flip + 拆两个弹簧

镜像枢轴 + 绑同一参数 + 同向旋转 = **跷跷板**（一只上一只下）。

正确做法：
1. 两个独立参数（`ParamEarSwayL` / `ParamEarSwayR`）
2. `flip=True` 让右件 `from/to` 互换
3. 两个独立 physics
4. **吃不同输入**（左吃头侧摆、右吃身体横摆）

实测验收：像素相关 **r = −0.076**、亮度偏移 +0.412、运动量 29.36 / 19.19 ⇒ 完全独立。

### 4.6 双腿：既要分开，又不能撑开缝

**这是个两难，实测的取舍是这样：**

| 配置 | 结果 |
|---|---|
| 两腿同源输入（都吃 BodyAngleX） | 缝不宽 ✓ 但**同步摆，视觉上变一条腿** ✗ |
| 两腿异源 + 等幅 | 分开 ✓ 但**缝被撑宽 7–11px** ✗ |
| **两腿异源 + 后腿幅度 60%** | **分开 ✓ 缝只 +4px ✓** ← 采用 |

后腿幅度压小的道理：前腿摆开时露出的是后腿那块，后腿自己几乎不动，露出的就还是原图内容。

**验收必须同时看两个数**：
- 参数相关 **< 0.85** → 真在分头动
- 分界处最大连续空白宽度 **≈ 原图** → 没撑开间隙

只看一个必然调错方向。

### 4.7 ❌ 绝对不要用 alpha 羽化消接缝

羽化后叠合公式是 `result = front×a + back×(1−a)`，
a=0.5 处两腿颜色各掺一半 ⇒ **视觉上融合成一条腿**（用户原话：「又回到没分开的情况」）。

**接缝靠重叠带宽解决，不靠羽化。**

---

## 5. 第四步：部署与验收

```bash
# 部署
cp out/xxx.iki <iki>/examples/player/public/model_hand.iki

# 播放器
http://127.0.0.1:5200/standalone.html          # 动态
http://127.0.0.1:5200/standalone.html?nodrive=1  # 静态（冻结，用于像素比对）
```

### 验收判据（按可信度排序）

1. **像素硬信号**（最可信）：与原图逐像素比，`?nodrive=1` 1280×1280 截图
   ```python
   d = np.abs(orig - shot).max(2)
   both = (orig.min(2) < 250) & (shot.min(2) < 250)
   n = int(((d > 25) & both).sum()); m = d[both].mean()
   ```
2. **逐行暗像素组比对**（判"多出一条线"）：按行提取暗像素成组，逐组对比原图
3. **动态指标**：参数相关、分界缝宽度
4. **VLM 看图**：⚠ **只当线索，不当结论**

### ⚠ VLM 的三个坑

1. 会把**原图本来就有的描边**当成"多余的线"（实测连续两次误判，而像素逐行完全一致）
2. 在摆动帧上会把**腿本身的位移**描述成"空白变宽"（那是运动，不是缺陷）
3. 措辞几乎不变，连续 8 版都说"有硬边"

**当像素数据说好、VLM 说不好时，不要无限迭代** —— 交出数据让用户裁定。

---

## 6. 命令速查

```bash
# 0) 竖图先居中成正方形白底（见 §1）
#    背景有干扰先抠图（见 §1.5）

# 1) 拆层（10–25 分钟，后台跑）
bash run_local.sh ../input/xxx_square1280.png 1280 30

# 2) 后处理（通用，无需改代码）
python postprocess_generic.py \
    out/layerdiff_output/xxx_square1280.psd \
    input/xxx_square1280.png \
    out/layerdiff_output/xxx_split.psd

# 2.5) 需要腿分开摆时：先自动拆腿，再带 --legs 重跑后处理
python auto_split_legs.py out/layerdiff_output/xxx_split.psd input/xxx_square1280.png out/pose/xxx
python postprocess_generic.py \
    out/layerdiff_output/xxx_square1280.psd \
    input/xxx_square1280.png \
    out/layerdiff_output/xxx_split.psd --legs out/pose/xxx

# 4) 绑定（通用，枢轴全自动）
python build_iki_generic.py \
    out/layerdiff_output/xxx_split.psd \
    out/xxx_hand.iki "模型名"

# 5) 部署
cp out/xxx_hand.iki <iki>/examples/player/public/model_hand.iki
```

### 三张图的实测对照

| | c04（原基准） | c15（女仆，无干扰） | c07（有背景干扰 + 拆腿） |
|---|---|---|---|
| 原图 | 1024×1024 | 750×1344 | 1858×3178 |
| 抠图 | 不需要 | 不需要 | **RMBG**，剔 84 块碎片 |
| 拆层耗时 | — | 约 7 分钟 | 约 7 分钟 |
| 后处理耗时 | — | 40 秒 | 50 秒 |
| 合成差 | 4.76 | 5.30 | **5.30** |
| parts / deformers / physics | 20 / 10 / 8 | 16 / 6 / 4 | **18 / 8 / 6** |
| 静态渲染 | 16499px / 3.31 | 22888px / 4.59 | **25525px / 5.09** |
| 腿 | 拆 leg_L/R（手调） | 未拆（腿不动） | **自动拆 + translateX** |
| 动态分界缝 | — | — | **+2px** |

### 15 号图（c15）实测耗时

| 步骤 | 耗时 | 结果 |
|---|---|---|
| 居中成正方形 | 秒级 | 750×1344 → 1280×1280，人物宽 714 |
| See-through 30 步 | **约 7 分钟**（layerdiff 30 步 ×2 轮 + marigold 4 步） | 17 层 PSD 38MB |
| 后处理 | 40 秒 | 16 层，合成差 5.30 |
| 绑定 | 15 秒 | 16 parts / 6 deformers / 4 physics，2.8MB |
| 渲染验证 | 静态 22888px / 差 4.59 | 人物完整、无缺件、无层错位 |

### 自动枢轴的可靠性

`build_iki_generic.py` 全部用 bbox 推导，15 号图实测：

```
back hair    (-64.0,  581.0)    bbox 顶部中心 = 发根
front hair   (-15.0,  624.0)    bbox 顶部中心 = 刘海根
bottomwear   (-29.5,  191.0)    bbox 顶部中心 = 裙腰
tail         (163.5,   65.0)    bbox 顶部中心 = 尾根
def_Head     由 neck 层推导
def_Body     由 bottomwear 推导
```

物理实测（40 帧）：尾巴 ±12.04、前发 ±3.55、后发 ±3.23、裙 ±2.84，
尾-前发相关 **r=−0.169**、尾-裙 **r=−0.102** ⇒ 各走各的节奏，不是整体同步晃。

> **注意**：`legs` 是单层时（不拆左右），`ParamLegSwayL/R` 没有对应部件，实测恒为 0.00。
> 需要腿动就必须先跑 `split_legs.py` 拆成 `leg_L` / `leg_R`。

---

## 7. 已知遗留（尚未解决）

| 项 | 状态 |
|---|---|
| 表情系统 | 6 个参数（眼开/嘴开/嘴形/眼珠 XY）无任何部件绑定；`mouth` 只有一条闭合嘴线 |
| 头发分缕 | 只有前发整块 + 后发整块，未分缕 |
| See-through 补画不完整 | `face` 刘海下 54.1%、`eyewhite` 睫毛下 36.0% |
| Anime2.5DRig | 不能导出 Iki |
| 膝盖弯曲 | 单 deformer 旋转，无法做腿部弯曲 |
