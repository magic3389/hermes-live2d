# warp（网格变形）绑定配方

iki 有两套 deformer，能力差一个量级：

| 类型 | 结构 | 能做什么 |
|---|---|---|
| `IkiMatrixDeformer`（`kind` 缺省或 `"matrix"`）| `pivot` + `transform` + `bindings`（translateX/Y、rotate、scaleX/Y）| 整体平移/旋转/缩放 |
| `IkiWarpDeformer`（`kind: "warp"`）| `grid`（控制点网格）+ `warps[].keyforms[].offsets`（逐点 delta）| **逐点弯曲变形** |

**布料、尾巴、长发必须用 warp。** matrix 旋转是把整块当刚体转：裙摆像木板绕点转，
尾巴像棍子甩，末端横移 `sin(角度)×长度`，一定越出遮挡余量露洞（见 SKILL.md 铁律 24）。
warp 能做到「底部偏移大、顶部不动」——既是布料该有的形变，末端位移又完全可控。

---

## 格式

```json
{
  "kind": "warp",
  "id": "def_SkirtWarp",
  "parent": "def_Body",
  "grid": {
    "cols": 4,
    "rows": 4,
    "points": [x0, y0, x1, y1, ...]
  },
  "warps": [{
    "parameter": "ParamSkirtSway",
    "keyforms": [
      {"value": -8.0, "offsets": [dx, dy, ...]},
      {"value": 0.0,  "offsets": [0, 0, ...]},
      {"value":  8.0, "offsets": [dx, dy, ...]}
    ]
  }]
}
```

- `points` 是 **model space**（原点画布中心、+y 向上），row-major，共 `(cols+1)*(rows+1)` 个点。
- `offsets` 长度与 `points` 相同，**加到 rest grid 上**（不是绝对坐标）。
- 子 part 通过 `part.deformer` 引用它，且**该 part 必须有 `mesh`**。
- warp 的 `parent` 必须是 matrix deformer（warp 不能挂 warp）。
- 一个 warp 只能带 `warps` **或** `warp2d`，不能both。

---

## 五个必守点（每个都实测翻车过）

### 1. rest grid 必须规则轴对齐：row 0 的 y 最大

校验器要求 `col 0` 的 x 最小、**`row 0` 的 y 最大**（+y 向上）、行列都严格单调且间距非零。

生成函数若签名是 `grid_points(x0, y0, x1, y1, cols, rows)`，调用时**必须把较小的 y 传给 y0、较大的传给 y1**。
传反的直接后果：

```
IkiFormatError: deformers[7].grid.points must be a regular axis-aligned grid
(row 0 top / largest y, column 0 left / smallest x, strictly ordered, nonzero spacing)
```

```python
def grid_points(x0, y0, x1, y1, cols, rows):
    """y1 = 顶（较大值），y0 = 底。row 0 落在顶边。"""
    pts = []
    for r in range(rows + 1):
        y = y1 - (y1 - y0) * r / rows
        for c in range(cols + 1):
            pts += [round(x0 + (x1 - x0) * c / cols, 2), round(y, 2)]
    return pts
```

### 2. keyform 的 `value` 是参数的 own-range 值，不是归一化

`value` 与参数**自己的 min/max** 对齐，引擎用 `params.get(warp.parameter)` 取原始值去 clamp+lerp。

写死 `-1 / 0 / 1` 而参数声明是 `min: -8, max: 8` ⇒ 参数推到 1 就到 keyform 顶、
只走了 **1/8 量程**，实测位移几乎为 0，用户判「还是没动」。

**从模型里读回范围再写**：

```python
_pm = {p["id"]: p for p in model.get("parameters", [])}
_pr = _pm.get("ParamSkirtSway", {"min": -1.0, "max": 1.0})
_vmin, _vmax = float(_pr["min"]), float(_pr["max"])
keyforms = [
    {"value": _vmin, "offsets": bend(-amp)},
    {"value": 0.0,   "offsets": [0.0] * n},
    {"value": _vmax, "offsets": bend(+amp)},
]
```

### 3. grid 必须覆盖**整个** part 的 bbox

只盖下半部分（`top_ratio=0.55` 之类）会让上半段根本不参与变形，和下层叠加成
**「一层动的和一层不动的叠在一起」**——这正是用户报的「裙子分层了」。

「上端不弯」要靠 offsets 的曲线指数实现，**不能靠缩小网格范围**：

```python
def bend_offsets(cols, rows, amp_x, curve=2.0):
    """curve 越大，顶部越不动、底部越明显（布料上端钉在腰上）。"""
    off = []
    for r in range(rows + 1):
        k = (r / rows) ** curve        # r=0 顶 → 0；r=rows 底 → 1
        for c in range(cols + 1):
            mid = 1.0 - abs((c / cols) - 0.5) * 2.0     # 中间鼓、两侧收
            off += [round(amp_x * k * (0.55 + 0.45 * mid), 2), 0.0]
    return off
```

### 4. 摆幅按 part 尺寸取，不要给常数

`amp = max(10, min(34, part_width * 0.045))` —— 小部件给 34px 会翻出去，大部件给 10px 看不见。

### 5. 网格密度决定「切割感」

warp 的变形是**格子内的线性插值**：格子越大，相邻格之间越像硬折。
4×4 摊在 749px 宽的裙子上每格近 **190px**，弯曲出来是一段段直线段，
用户会读成「有切割感 / 像拼上去的」——不是分层，也不是色差。

```python
COLS, ROWS = 10, 10      # 每格 ≈ 75px，弯曲成为连续弧线
```

- 加密后 `offsets` 长度是 `(cols+1)*(rows+1)*2`（10×10 ⇒ 242），别沿用旧常数。
- 密度上去后曲线指数可以放缓（`curve` 2.0 → 1.6），否则底部会集中成一圈突变。
- 代价只是 JSON 变大（242 个数/ keyform），引擎每帧成本可忽略。

---

## 校验：部署前必须先跑 parse，不要靠浏览器试

warp 格式错误在播放器里**只表现为模型加载不出来**（`window.model` 是 `undefined`、
画布空白），没有任何可读错误信息 —— 会误判成「引擎不支持 warp」。

```js
// validate_iki.mjs  放在 iki 仓库根目录
import { readFileSync } from "node:fs";
const { parseIkiModel } = await import("./packages/format/dist/index.js");
const raw = JSON.parse(readFileSync(process.argv[2], "utf8"));
try {
  const m = parseIkiModel(raw);
  console.log("OK 模型:", m.name, " parts:", m.parts.length, " deformers:", (m.deformers||[]).length);
} catch (e) {
  console.log("FAIL:", e.constructor.name);
  console.log(String(e.message).slice(0, 1500));
}
```

```bash
node validate_iki.mjs "D:/proj/out/model.iki"
```

⚠ **MSYS 下不要把脚本写到 `/tmp`**：`node /tmp/x.mjs` 会被解析成 `D:\tmp\x.mjs`，报 `MODULE_NOT_FOUND`。
写进工程目录（或 `$TMPDIR`）。

---

## 诊断：warp 生效了，但外轮廓不动

**症状**：内部花纹 / 饰带明显变形（实测 26,000px 变化），但裙摆**逐行边缘位置全部 +0**。

**根因**：更上层残留同一块内容，order 更高、不挂 warp，把变形层盖住了。
实测 `topwear` 在裙子区残留 **20,543px**，order 7 压着 `bottomwear`（order 6，已换成 warp）。

**注意：只归并「两层重叠」的部分不够。** 非重叠残留照样盖住，必须归并整个几何区域，
再用其他条件排除不该动的部件（如袖带：`_mv &= ~sleeve_band`）。

⚠ **但排除 mask 本身也是坑：带子给大就会把目标保护起来。**
实测袖带 `y` 上限写到 820，而手腕其实在 `y≈589` ⇒ 整段裙摆落在带内被 `~_mask` 排掉，
`topwear` 在裙子区每 25px 行残留 6,000–7,600px，屏幕上看就是「轮廓一点不动」。
判断方法：归并后**回数目标区残留**（每 25px 一行打印该层像素数），归并区应接近 0；
非 0 就是 mask 吃掉了，先把 mask 范围按解剖锚点（颈底 / 腕线 / 腰线）收回来。

**测量方法（这里很容易量错）**：

- ✅ 优先做法：**两帧差分热图**。参数推到满量程各拍一帧，把 `abs(a-b).max(2)` 渲染成彩色热图，
  看红色落在**哪个部位**。真弯曲的特征是红利集中于裙摆外轮廓 / 荷叶边并呈弧形梯度；
  整块均匀位移则是刚体。这种读法不需要猜“哪一行属于谁”。
- ⚠ 在该 part 的**实际 bbox** 内逐行取最左/最右非白像素，**只有当该 zone 没有别的层占多数时可信**。
  实测裙区 `back hair` 82,287px、`bottomwear` 43,231px：最左/最右量到的是头发，
  于是“逐行边缘全部 +0”与“区域内 19,456px 变化”同时成立，很容易误判成 warp 没生效。
- ❌ 「整行非白像素的列重心」——旁边部件（腿、脚、尾巴）会把重心拉走。
  实测重心给出 −10.9px 位移，而同一帧边缘全部 +0。
- ❌ 在 part bbox 之外取行 —— 裙子在画布上的可见范围由它自己的 bbox 决定
  （`bottomwear` y[108,918]），y>900 量到的是腿和脚，不是裙子。

**分层修复后的验收**：把参数推到**满量程**（不是 ±1，先读 min/max）再比两帧，
并确认「顶部不动 + 底部动」的梯度成立，才叫真弯曲。
