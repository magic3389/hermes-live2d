# 四段管线：单张立绘 → 会动的 .iki

> 本文件是技能内的精简版。完整手册（含每个坑的实测数字）见
> [hermes-live2d/docs/01_pipeline.md](https://github.com/magic3389/hermes-live2d/blob/main/docs/01_pipeline.md)。

## 管线图

```
单张立绘 (PNG/JPG，可带背景)
 ├─[0] 抠图（可选）   rmbg_cut.py + extract_subject.py   → 居中正方白底
 ├─[1] 语义拆层       run_seethrough.sh                  → 17 层 PSD（约 7 分钟）
 ├─[2] 后处理         postprocess.py                     → 合并/补洞/换色/层序
 ├─[2.5] 双腿拆分     auto_split_legs.py                 → leg_L / leg_R
 └─[3] 绑定导出       build_iki.py                       → model.iki
```

★ `postprocess.py` / `auto_split_legs.py` / `build_iki.py` 三个是**通用的**，换图不改代码。

## 各段的输入输出契约

### [0] 抠图（背景有渐变/文字/其他头像框时必须做）

- 节点：ComfyUI `LayerMask: RemBgUltra`（RMBG-1.4）
- ⚠ `process_detail=False`（True 会因 pymatting 版本报 `fix_trimap` 不存在）
- ⚠ `SaveImage` 存 RGB 丢 alpha → 必须接 `MaskToImage` 单独导 mask
- 剔碎片：连通域面积 < 3000px 丢掉（RMBG 会在背景留零散白点）
- 宁可不用 GroundingDino+SAM：prompt 宽泛时框把整画面选进去（实测前景 78.8%），
  且输出只有 3 通道，接 `ImageToMask(alpha)` 报 `index 3 is out of bounds`

### [1] 拆层

- 输入建议 1280 宽、纯白底、人物居中、四周留 20px
- 竖图等比缩放后居中补白，**不要直接拉伸**
- 输出 17 层语义 PSD（约 38–43MB）

### [2] 后处理（决定质量上限）

顺序不能乱：

1. `ALPHA_FLOOR=60` 清底膜（每层都有覆盖全画布的 1–60 alpha）
2. 分区换色：本层 RGB 与原图差 ≤60 才换成原图
3. 合并 `legwear+footwear → legs`；RGB 取原图
4. 限距补洞（`binary_dilation(core, iterations=8)`，不要全画布）
5. **backdrop 兜底层**：原图非白 & 所有层都没覆盖 → 补在最底
6. 语义画序写 PSD（先写 = 在下）
7. 可选 `--legs <前缀>`：读 mask 拆成 leg_L / leg_R

### [2.5] 双腿拆分

自动推导，不手测：

- 逐行测段数 → `fork_y`（连续 5 行 2 段的第一行）
- 有合并区：上段沿前腿实测宽度切；无合并区：直接按连通域
- `FRONT_W` = **分叉区左段宽度中位数**（不是单段行宽度）
- 单段行分两种：明显偏左/偏右 → 整块归那条腿；横跨中线 → 按 `FRONT_W` 切
- 边界放到描边外侧（亮度 <110 视为描边）
- 保留原抗锯齿 alpha，**不羽化**
- 输出 `.npy` mask，回到 [2] 用 `--legs` 一次成型（不要读回 PSD 重建，alpha 会损耗）

### [3] 绑定

- 层名归一化 → 语义 ORDER → bbox 推导 PIVOT → deformer 表 → physics 表
- 腿用 `translateX`（rotate 会因杠杆放大撑开缝隙）
- 左右对称件：`flip=True` + 两套独立弹簧 + 不同输入
- 输出 `.iki`（2.3–3.5MB）

## 完整命令链

```bash
# 0) 背景有干扰才需要
python pipeline/matting/rmbg_cut.py --image mychar.png --prefix mychar
python pipeline/matting/extract_subject.py \
    --src mychar.png --mask <ComfyUI>/output/mychar_rmbg_mask_00001_.png \
    --out input/mychar_1280.png

# 1) 拆层
ST_DIR=./see-through bash pipeline/run_seethrough.sh --src input/mychar_1280.png

# 2) 后处理
python pipeline/postprocess.py out/layerdiff_output/mychar_1280.psd \
    input/mychar_1280.png out/mychar_split.psd

# 2.5) 需要腿分开摆
python pipeline/auto_split_legs.py out/mychar_split.psd input/mychar_1280.png out/pose/mychar
python pipeline/postprocess.py out/layerdiff_output/mychar_1280.psd \
    input/mychar_1280.png out/mychar_split.psd --legs out/pose/mychar

# 3) 绑定 + 部署
python pipeline/build_iki.py out/mychar_split.psd out/mychar.iki "my character"
cp out/mychar.iki <iki>/examples/player/public/model.iki
```

## 三例实测对照

| | 案例 1 | 案例 2 | 案例 3 |
|---|---|---|---|
| 输入 | 1024 Q 版纯白底 | 750×1344 竖图 | 1858×3178 带背景 |
| 抠图 | 不需要 | 不需要 | RMBG，剔 84 块碎片 |
| 部件 | 20 | 16 | 18 |
| 合成差 | 4.76 | 5.30 | 5.30 |
| 静态渲染差 | 16499px / 3.31 | 22888px / 4.59 | 25525px / 5.09 |
| 双腿 | 手调 r=0.746 缝+4~6px | 未拆（腿不动）| 全自动 r=0.750 缝**+2px** |
| 人工改代码 | 60+ 版迭代 | 零 | 零 |

耗时基准：拆层约 7 分钟、后处理 40–50 秒、绑定数秒。

## 常见失败 → 定位

| 现象 | 先查 |
|---|---|
| 整图发白 | 底膜没清（`alpha<60` 应清零）|
| 裙子盖脸 / 头发盖眼 | 层序按面积排了 |
| 两腿间纯黑 | 合并层 `p/out_a` 除出黑，RGB 应取原图 |
| 拆腿后白洞 | See-through 漏层，要 backdrop |
| 某件上有条暗线 | 谁盖在它上面（overlap + 更高 order）|
| **某处颜色变浅 / 丢了一缕** | **语义错归或上层覆盖**——`handwear` 拿到正确深蓝、`topwear` 用浅紫盖住（三图实测 3879–6051px）；`front hair` 的 bbox 可能只到大半个头 |
| 腿完全没有动画 | 层名归一化把 `leg_L` 变成 `leg l` |
| 摆动时露出底色 | 两层颜色被 alpha 羽化掺混，或重叠带不够宽 |
| 静态对但动态有缝 | 量「最大连续空白宽度」与原图对比 |
