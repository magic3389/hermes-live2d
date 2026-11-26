# 使用手册 —— 从零到会动的模型（含 Agent 自动安装）

这份文档有三个读者：**想手工跑一遍的人**、**想让 Hermes Agent 全自动装好的人**、
**Agent 自己**（第 4 节是可直接执行的步骤，不是给人看的教程）。

---

## 0. 这套东西是什么

把**一张立绘**变成**会动的 2D 傀儡模型**（`.iki`，Live2D 的开源替代）：

```
立绘 PNG/JPG
  → 抠图（BiRefNet）          ← 背景有干扰时才做
  → See-through 语义拆层       → 17 层 PSD
  → 后处理（清底膜/归位/几何归并）
  → 自动拆腿（可选）
  → 绑定 → .iki
  → warp 网格变形（裙子/布料/尾巴）
  → 网页播放器 / Android App
```

成品是 JSON（`.iki`），播放器 80KB，**不依赖 Cubism 授权**。

**硬边界先说清**（详见 `04_known_issues.md`）：

| 做不到 | 为什么 |
|---|---|
| 被头发/腿遮住的部位补全 | 原图那里没有像素 |
| 整条裙子自动分前后裙摆 | 一个图层物理上不能同时在腿前又在腿后 |
| 坐姿/交叠腿自动拆 | 没有「分叉区」结构 |
| 单图生成真口腔、侧脸内耳 | 内容根本不存在 |

---

## 1. 三种使用方式

### 1.1 让 Agent 自动装（推荐，一句话）

把下面这段**整段**发给你的 Hermes Agent：

```
从这个仓库安装 live2d 技能：
https://github.com/magic3389/hermes-live2d

步骤：
1. git clone 到临时目录
2. 把 skill/2d-puppet-rigging/ 复制到 <HERMES_HOME>/skills/game-design/
3. 用 skill_view(name='2d-puppet-rigging') 确认能读到 SKILL.md
4. 报告：技能版本号、references 篇数、scripts 个数
不要跑生成，只装技能。
```

Agent 会自己 clone、装好、回读校验。之后你说「把这张图做成 live2d」它就知道怎么做了。

### 1.2 手工导入技能

```bash
git clone https://github.com/magic3389/hermes-live2d.git
cp -r hermes-live2d/skill/2d-puppet-rigging \
      "$LOCALAPPDATA/hermes/skills/game-design/"
```

（Linux/macOS 是 `~/.local/share/hermes/skills/game-design/`，以你的 `HERMES_HOME` 为准。）

装完在 Hermes 里说「live2d」或「2d 傀儡」就会自动加载技能。

### 1.3 只用脚本，不用 Agent

见第 2、3 节，纯命令行。

---

## 2. 全流程需求

### 2.1 硬件与系统

| 项 | 最低 | 本文实测环境 |
|---|---|---|
| GPU | 8GB 显存 | RTX 3080 20GB |
| 内存 | 16GB | 32GB（页面文件给大点） |
| 磁盘 | 20GB 空闲 | See-through 权重 + 中间件约 15GB |
| 系统 | Windows / Linux | Windows 10 |

### 2.2 软件依赖

| 组件 | 用途 | 装法 |
|---|---|---|
| Python 3.12 | 全部脚本 | `bash pipeline/install_env.sh`（独立 venv）|
| See-through | 语义拆层 | 单独 clone，`ST_DIR` 指过去 |
| ComfyUI + LayerStyle | 抠图（可选）| 只在背景干扰时用；BiRefNet 也可以纯 Python 跑 |
| Node 18+ | 播放器 / `.iki` 校验 | 系统装 |

**不要装进 ComfyUI 的 venv**——torch 版本会打架。`install_env.sh` 会建独立环境。

### 2.3 素材要求（决定成败）

| 项 | 好 | 差（会返工） |
|---|---|---|
| 背景 | 纯白 / 纯色 / 简单渐变 | 复杂场景、装饰外框、其他人物 |
| 姿势 | 正面站姿、双腿分开 | 坐姿、交叠腿、大幅动态姿势 |
| 分辨率 | ≥ 1000px 短边 | < 800px（发丝会糊）|
| 风格 | 平涂 / 赛璐璐 | 厚涂写实（语义分割会乱）|
| 遮挡 | 头发不超过身体 1/3 | 长发盖住半个身子（遮挡区域补不回来）|

**A-pose 是最理想的输入**：双臂离开躯干（袖子能分出来）、双腿分开（能拆）、
头发自然垂落不交叉。

### 2.4 输入格式

- PNG / JPG 单张立绘
- **也可以是别人做好的分层 PSD** —— 直接跳到第 3 节「绑定」

---

## 3. 手工全流程（命令逐条）

```bash
git clone https://github.com/magic3389/hermes-live2d.git && cd hermes-live2d

# ① 环境
bash pipeline/install_env.sh

# ② 抠图（背景干净可跳过）
python pipeline/matting/rmbg_cut.py --image my.png --prefix mychar
python pipeline/matting/extract_subject.py \
    --src my.png --mask <ComfyUI>/output/mychar_rmbg_mask_00001_.png \
    --out input/mychar_1280.png

#    复杂背景（场景/外框/深色）必须加 --no-fill，否则补漏会把背景吃进人物：
python pipeline/matting/extract_subject.py ... --out input/mychar_1280.png --no-fill

# ③ 语义拆层（约 7 分钟）
ST_DIR=/path/to/see-through bash pipeline/run_seethrough.sh --src input/mychar_1280.png

# ④ 后处理
python pipeline/postprocess.py \
    out/layerdiff_output/mychar_1280.psd input/mychar_1280.png \
    out/mychar_split.psd

# ⑤ 拆腿（可选，先验条件！）
python pipeline/auto_split_legs.py out/mychar_split.psd input/mychar_1280.png out/pose/mychar
python pipeline/postprocess.py \
    out/layerdiff_output/mychar_1280.psd input/mychar_1280.png \
    out/mychar_split.psd --legs out/pose/mychar

# ⑥ 绑定
python pipeline/build_iki.py out/mychar_split.psd out/mychar.iki "my character"

# ⑦ 裙子换成 warp 网格变形
python pipeline/add_warp.py out/mychar.iki out/mychar_warp.iki

# ⑧ 校验（warp 格式错在播放器里只表现成画布空白，没有任何报错）
node validate_iki.mjs out/mychar_warp.iki

# ⑨ 放进播放器
cp out/mychar_warp.iki <iki>/examples/player/public/model.iki
```

### 3.1 拆腿之前必须验的三个数

```python
# 判据（跑之前量，别跑完再后悔）
左右像素比 > 1.5        → 不拆
legs bbox 宽 > 画布 50% → 不拆
没有 legs 层（只有 legwear）→ 无从拆
```

### 3.2 后处理的两个几何参数（只在对应问题出现时用）

| 参数 | 什么时候用 | 怎么定 |
|---|---|---|
| `--skirt-zone y0` | 裙子被劈进两层，一半摆一半不摆 | 腿 bbox 上缘 / 腰线 |
| `--sleeve-band x_sh,x_wr,y0,y1,half` | 袖子像素留在裙子层，跟着裙子物理摆（用户读成"胳膊透明"）| 肩 x → 腕 x 线性插值，**y 上限取腕线** |

---

## 4. Agent 操作手册（Agent 自己读这一节）

> 这一节是给 Agent 的指令，不是给人看的。

### 4.1 装技能

```
SKILL_SRC = <repo>/skill/2d-puppet-rigging
DEST = <HERMES_HOME>/skills/game-design/2d-puppet-rigging
复制整目录（含 references/ scripts/ templates/）
校验：skill_view(name='2d-puppet-rigging') 能读到 SKILL.md
回读：version 号、references 篇数、scripts 个数，报给用户
```

### 4.2 接到「把这张图做成 live2d」时的执行顺序

**不要一上手就跑全部**，按下面顺序，每步出可验证数字：

```
1. 读素材，量三个前置条件
   - 背景复杂吗？（采样四角，色差 >6 且不是渐变 ⇒ 复杂）
   - 有 legs 层吗？bbox 宽占画布多少？
   - 长发遮挡面积占人物多少？

2. 抠图（复杂背景 --no-fill）
   验收：backdrop < 5000px；非白像素占比 40%–55%

3. 拆层 7 分钟
   验收：层数 ≥ 15；backdrop 上万的回去查抠图

4. 后处理
   验收：合成差 < 8（越小越好，实测 4.7–5.5 是好的）

5. 拆腿（先验 3.1 的三个数）
   验收：(L&R).sum() 重叠 < 200px

6. 绑定
   验收：parts / deformers / physics 数量报给用户

7. warp（裙子/尾巴/长发）
   验收：parseIkiModel 通过 + 参数推到满量程两帧差 > 10,000px

8. 交付
   给：.iki 绝对路径 + 层目录 + 参数清单 + 固有限制
```

### 4.3 验收铁律（违反必返工）

1. **静态像素差不能证明「修好了」**。必须给**驱动到满量程后的两帧差** + 「外轮廓到底动没动」。
2. **判归属用原图色，不用层自己的颜色**（错分区域已被涂成目标层色，自比恒为 0）。
3. **语义层名 ≠ 几何分组**（`topwear` 里可能有裙子，`back hair` 里可能有白袖子）。
4. **物理幅度 ≤ 遮挡余量**（被遮 W 像素宽，位移不能超过 W）。
5. **归并后回数目标区残留**（该层在区域内应接近 0，非 0 就是排除 mask 吃掉了目标）。
6. **VLM 只用来提示「该看哪儿」**，结论回到像素数与色差。

### 4.4 用户描述 → 技术根因 对照表

| 用户说 | 先查 |
|---|---|
| 「分层了 / 有一层不动」 | 同一条裙子被劈进两层（`topwear` + `bottomwear`），归并整个几何区 |
| 「有切割感 / 像拼上去的」 | warp 网格太粗（4×4 → 10×10）|
| 「透明 / 胳膊没了」 | 四种成因：层半透明 / 层不透明但错色 / 挂错 deformer / 描边被挖掉 |
| 「有腿的痕迹 / 残缺」 | 遮挡露洞，先量重叠区颜色族（冷色是本体，暖色才是错分）|
| 「腿脚没动」 | deformer ±3px + physics scale 8 ⇒ 实际 0.5px，要 ±9 + 38 |
| 「边缘有杂色块」 | 层在**不透明**处带错色，不是抠图残留（查渲染图不要查输入图）|
| 「杯子悬空 / 手在围裙下」 | 画序：`objects`/`handwear` 必须在 `topwear` 之后 |

---

## 5. 常见问题

**Q: 装完技能 Agent 没反应？**
A: 技能靠 description 匹配触发。说「live2d」「2d 傀儡」「做成会动的模型」都会命中。
   若仍不加载，`skill_view(name='2d-puppet-rigging')` 手动确认内容在。

**Q: 拆层这步要 GPU 吗？**
A: 要。See-through 需要 8GB 以上显存，30 步约 2 分钟 + marigold 9 秒。

**Q: 能不能不用 See-through，直接给分层 PSD？**
A: 能，而且更好。美工按 `docs/02_psd_spec.md` 的层名规范分层，从第 3 节「绑定」开始。

**Q: warp 一定要做吗？**
A: 只在有裙子/尾巴/长发时需要。只要整块平移旋转的部位，matrix 就够。

**Q: 生成出来的模型怎么用？**
A: `.iki` 是 JSON，任何实现 iki 格式的播放器都能读。本仓库配了网页播放器
   （`examples/player`），也能嵌进 App、桌宠、vtuber 推流。

---

## 6. 版本与更新

```bash
# 更新技能（保留本地改动就先 stash）
cd hermes-live2d && git pull
cp -r skill/2d-puppet-rigging "$LOCALAPPDATA/hermes/skills/game-design/"
```

关注 `docs/04_known_issues.md` 的变更——那里记录的是「哪些问题已能自动修 / 哪些是方法学边界」，
比 README 更贴近实战。
