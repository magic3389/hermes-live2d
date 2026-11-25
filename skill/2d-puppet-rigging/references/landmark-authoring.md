# 单张立绘标 landmark：像素扫描法

切层的 quality 全压在 landmarks 上，而 landmarks 只能靠**像素事实**定，不能靠 VLM 报坐标。
本文是 `cutLayersFromIllustration` 之前那一步的做法，实测能把一张 1024² 的 Q 版全身图从零做到
`passed: true`（2–3 轮），单张约 20–30 分钟。

## 为什么不用 VLM 报坐标

同一张嘴在三张不同的 crop 上给出三个互相矛盾的框；其中一次的「嘴框」像素统计是
`white 0.90 / skin 0.86` —— 那是空白脸皮。VLM 能看懂**画的是什么**（发色、姿势、手有没有端碗），
但读不了像素级坐标。用法固定：**VLM 描述内容，像素扫描定坐标，再让 VLM 复核结果图**。

## 第一步：把图归一到画布尺度

切层在 `1100` 宽画布上工作（`viewIllustrationGrid` 报的 canvas），所有 landmark 坐标都是画布像素。
先用 `viewIllustrationGrid({zoom:1, step:50})` 出一张整图网格图确认构图，之后所有像素统计都在
**同一尺度**上做（`Image.resize((1100, round(h*1100/w)), LANCZOS)`），否则坐标对不上。

## 第二步：颜色分类 + 游程扫描（定结构的骨架）

分类要在**背景色已知**的前提下做（取 `a[5,5]` 当 backdrop）。这套阈值在「蓝发 + 浅肤色 + 白围裙」
的画面上验证过：

| 类 | 判据 |
|---|---|
| `BG` | 与背景色每通道差 < 20 |
| `DARK` | `max(rgb) < 90`（睫毛/眼线/描边/深阴影） |
| `WHITE` | `min(rgb) > 205` 且 `max-min < 20` |
| `BLU` | `b > r+40` 且 `b > g+15`（头发和虹膜**都是**这一类） |
| `SKIN` | `r>195, g>165, b>140, r>b+8, max-min<75` |

然后沿竖直/水平线打游程（run-length），只打印长度 ≥8 的段。**这是最有用的一步**：
它能直接告诉你「x=407 这一列从 y419 起是 DARK(睫毛) → WHITE/BLU(眼内) → SKIN(脸)」，
比任何看图估计都稳。扫描线取法：

- 竖线：穿过一只眼中心、两眼之间、脸中线、脸外两侧（各一条）
- 横线：眼行、眉行、嘴行、下巴行、胸口行、裙摆行

## 第三步：从游程反推每个 landmark

- **眼框**：竖直穿过眼睛的列上，`DARK → (WHITE|BLU) → DARK` 的夹层就是眼睛的上下界；
  横向眼行上 `WHITE|BLU` 的连续段就是左右界。DARK 段是睫毛，要**算进** box（含睫毛）。
- **虹膜**：眼行上 `BLU` 游程的中点与半宽 ≈ `cx, rx`。估小了没关系，`iris.*` 的 hint 会直接报
  「the disc found spans x 537..598 (rx about 31.0)」，照抄。
- **嘴**：眼下方、脸中线附近，找 `WHITE|DARK` 混杂的短游程簇（张嘴/露齿的画法）；Q 版小嘴只有 10–20px 高，
  框**要贴紧**嘴线本身 —— 框放宽会把口水、汗滴一起算进 mouth 层，张嘴时被一起拉伸，看起来像撕裂。
- **头发/肤色采样**：直接打印候选点的 `rgb` 值确认：蓝色头发 ≈ (78,97,157)；浅肤色 ≈ (250,231,221)。
  纯黑点（rgb≈(13,4,2)）是轮廓线，不能当 `skin.shadow`。没有可靠阴影点时**把整个字段删掉**——
  留成空数组 `shadow: []` 会被拒（`landmarks.samples.hair.shadow must have 1 or more point, got 0`），
  `highlight` 同理。
- **frontHair**：沿「头发/额头交界」走折线（扫描线上 BLU→SKIN 的切换 y），再向上兜到头顶。
- **skull**：脸的椭圆外扩一点（不是整头头发的外轮廓，别把垂下的长发算进去）。太大 `fills.face.forehead` 会飙高。
- **shellLower**：后发最下沿，从左到右取点，x 严格递增。
- **skinSeeds**：脸颊（`SKIN` 占比 > 0.9 的位置一定安全）。
- **bodySeeds**：**避开蓝色衣服**（会被读成头发而 FAIL），用白围裙/白袜那类非蓝漆。
- **neckTopY**：下巴下沿往下十几像素。

## 第四步：候选框做像素复核（接受/否决）

对任何一个「看起来像」的框，先算框内的类占比再决定信不信：

- 眼框：`white 0.17–0.24 / blue 0.33–0.37` 合理；`white 0.9` 说明框在空白处。
- 虹膜圆：`blue 0.5–0.7` 合理（中心有暗色瞳孔，不会满）。
- 嘴框：`dark(亮度<140) 0.15–0.2` 说明框里有画；`< 0.05` 说明框压在空白脸皮上。

`scripts/probe_landmarks.py` 就是这套：给图 + 若干 `--v x` / `--h y` / `--box name,x0,y0,x1,y1`，
直接打印游程和占比。

## landmark JSON 本身是严格 schema（先避开这两个白跑）

- 顶层只认 `samples / eyes / brows / nose / mouth / frontHair / sideHair / skull / shellLower /
  skinSeeds / bodySeeds / backgroundSeeds / paintSeeds / neckTopY / bodyOnly / customParts`。
  加一个 `_note` 备注就直接 REFUSED 并把允许键全列出来 —— 要写注释放在调用脚本里，别放进 JSON。
- `samples.*.shadow` / `highlight` 存在但为空数组会被拒（见上）。
- 改这个文件用 `python -c "json.load → 改字段 → json.dump"`：`write_file` 在读过（尤其分页读过）之后
  会以「modified since last read」拒绝覆盖，读一遍写一遍来回耗时间。

## 第五步：按 checks 迭代

1. 跑 `cut`，只挑 `pass:false && level:"fail"` 的看（`seeds`、`opening.*`）。
2. hint 会点名「移动哪个 landmark、往哪边、挪多少」，照着改**那一个字段**，别的别动。
3. 常见的三条修法：
   - `seeds` → 换 `bodySeeds` 到非蓝漆上。
   - `opening.L/R` → 按 hint 改 `box` 的那一条边（例如 `y1` 下移 7）。
   - `iris.*` → 照 hint 报的「disc found spans x … rx about …」重设椭圆。
4. `passed:true` 后剩下的 warn 不是要修完的，是要**讲清楚**的：
   `frontHairWidth>1` 会 clamp 转头、`fills.face.forehead` 高说明额头是补出来的、
   `pockets` 是发丝缝隙被当背景键掉。

## 标点的现实感

`viewIllustrationGrid` 的 zoom 上限 8、step 下限 5。眼睛这种 70×70 的区域用 `crop + zoom 4 + step 10`
最清楚；嘴用 `zoom 5–6 + step 5`。**不要一次看全图估坐标**，全图只用来看构图。
