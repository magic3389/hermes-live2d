# 02 环境部署

> 从零到能跑：三套环境（拆层 / 绑定预览 / 素材生成）+ 模型与端口。
> 所有数字为 2026-11-23 实测。

---

## 1. 三套环境的分工

| 环境 | 位置 | 作用 | 是否碰 `<ComfyUI>` |
|---|---|---|---|
| See-through venv | `./venv` | 单张立绘 → 17 层语义 PSD | ❌ 完全隔离 |
| Iki（Node） | `<iki>` | 手写 `.iki` 模型 + 网页播放/编辑 | ❌ 无关 |
| ComfyUI | `<ComfyUI>` | Qwen-Image-2.1 生成 A-pose 素材 | ✅ 就是它 |

**隔离是硬约束**：See-through 要装 torch 2.8 + diffusers 0.37 + transformers 5.0，
和 ComfyUI 现有依赖共存会打架，所以单独开 venv，不装进 ComfyUI 环境。

---

## 2. See-through 环境（实测版本）

| 项 | 版本 / 值 |
|---|---|
| Python | 3.12.11（uv 管理的独立 venv） |
| torch | 2.8.0+cu128 |
| diffusers | 0.37.0 |
| transformers | 5.0.0 |
| psd_tools | 1.14.2 |
| Pillow | 12.1.1 |
| numpy | 2.2.6 |
| scipy | 1.15.3 |
| CUDA | 可用，`NVIDIA GeForce RTX 3080`（20GB） |
| 源码 | `see-through` @ `--depth 1`，18MB |

依赖完整性当时验过 24 个关键包 import 全通过：torch / diffusers / transformers / accelerate /
safetensors / numpy / PIL / cv2 / sklearn / scipy / einops / kornia / omegaconf / psd_tools /
timm / huggingface_hub / simple_parsing / natsort / termcolor / yaml / tqdm / matplotlib / pandas。

---

## 3. 拆层模型（两个）

| 模型 | 用途 | 磁盘 | 路径 |
|---|---|---|---|
| `layerdifforg/seethroughv0.0.2_layerdiff3d` | SDXL 分层生成（unet 8.14GB） | 9.8 GB | `D:\models\seethrough\layerdiff3d` |
| `24yearsold/seethroughv0.0.1_marigold` | 动漫微调伪深度估计（unet 2.43GB） | 3.1 GB | `D:\models\seethrough\marigold` |

合计 12.9 GB。下载时踩的坑：`hf` CLI 在 hf-mirror 会假死（进程在、0 字节），
单连接 Range 请求 TLS 握手超时；最终用分段并发（24 线程 × 16MB）稳定到 40MB/s。

### 3.1 跑一次拆层的耗时与显存

单张 1024×1024：约 **4–6 分钟**（含深度估计 + 分层生成 + PSD 写出），显存峰值约 14GB。
输出 17 层 PSD 约 38MB。

---

## 4. Iki（开源 Live2D 替代）

位置：`<iki>`

| 子包 | 作用 |
|---|---|
| `packages/format` | `.iki` 格式定义（`types.ts` 是权威） |
| `packages/engine` | 播放引擎（含 `IkiMotion` 物理） |
| `packages/editor` | 可视化编辑器（上游汉化 fork） |
| `packages/mcp` | 切层 / 自动绑定（**本项目没用**，见第 6 节） |

### 4.1 起服务

```bash
# 播放器（常驻 5200）
cd refs/iki/examples/player
node node_modules/vite/bin/vite.js --port 5200 --host 0.0.0.0

# 编辑器（5174）
cd refs/iki/examples/editor
node node_modules/vite/bin/vite.js --port 5174 --host 127.0.0.1
```

不走 `pnpm dev` —— bash 下 pnpm 是 `.cmd` shim，node 直调最稳。

验活（本地必须 `--noproxy`）：

```bash
curl -s --noproxy '*' http://127.0.0.1:5200/ | grep -o '<title>[^<]*</title>'
```

### 4.2 播放页改造（本项目做的）

`examples/player/standalone.html` + `src/standalone.ts`：

1. 接入 `IkiMotion.update()`，每帧先写外部驱动、再 `motion.update(now)`
2. 白底（原图是白底，黑底会让边缘抗锯齿的浅色显成一圈白毛边）
3. 右上角加了「背景：白 ⇄ 深」切换按钮
4. `?nodrive=1` 调试开关，跳过驱动与 `motion.update()`，用于精确像素对比

---

## 5. 端口表

| 端口 | 是什么 | 备注 |
|---|---|---|
| **5200** | Iki 播放器（**交付入口**） | 打开就在播，白底 |
| 5174 | Iki 编辑器 | 只给汉化调参，不交付 |
| 8020 | GIF 播放页 | 静态连拍帧 |
| 8188 | ComfyUI | 生成 A-pose 素材 |
| 8765 | 大肥鱼分身服务端 `brain.py` | 语音通道 |
| 8010 | Anime2.5DRig（日文调参工具） | **只能拖滑杆，不能播放/导出**，不是交付 |

⚠️ 用户会同时开好几个标签，交付时必须说清哪个是哪个。

---

## 6. 为什么不用「单图自动切层」

`@ikijs/mcp` 的 cut 管线按颜色 + 形态学硬切，稳定只出 14 层左右：

```
face / nose / mouth / eye_L / eye_R / iris_L / iris_R /
lash_* / lash_lower_* / hair_front / hair_back / body
```

它**不认识四肢、不认识尾巴**；头发只有前/后两层；眼白/虹膜/睫毛虽分层但没有独立的
闭眼/闭口差分；而且每层是像素硬切、互不重叠，部件一动就露底。

用户当时判定：「头发没有分层、眼睛 嘴 四肢 尾巴 都没做分层、又把撕裂的坑踩了一次」，
「还不如上一个 live2d」。

**所以路线改成 See-through 出 17 层语义 PSD，再手写 `.iki`。**

即使走 `@ikijs/mcp` 的自动绑定（`autoRigFromLayers`），枢轴也是它按连通域自己算的：
尾巴横向伸出时钉在尾鳍尖、`legwear` 没有摇摆属性、`mouth` 会自己缩放。
要独立枢轴只能自己写 `.iki`。

---

## 7. 素材生成环境（ComfyUI + Qwen-Image-2.1）

A-pose 素材 10 张走 ComfyUI 本地 API（端口 8188）：

```
UnetLoaderGGUF(qwen-image-2.1-UC-Q8_0.gguf)
  → CLIPLoader(type='qwen_image', qwen3vl_8b_int8_convrot.safetensors)
  → VAELoader(qwen_image_2.1_vae_bf16.safetensors)
  → TextEncodeQwenImage21
  → EmptyLatentImage(896×1152)
  → KSampler(steps=30, cfg=1, euler, simple)
  → VAEDecode
```

约 **48 秒/张**。

**为什么必须用 Qwen-Image**：用户明确拒绝 SDXL/WAI 的「肥胖侏儒」体型，
且必须以 03 号原图为参考（体型/画风/配色继承）。SDXL + COCO-18 骨架那条路已知两头翻车：
不加体型词 → 细长腿；补 `chubby` → 肥胖侏儒。

**参考图会搬表情和道具**：原图在偷吃米饭，生成的 A-pose 仍然是那个表情。
指令里必须显式写目标表情并逐项否定原表情（`cfg=1` 下负面提示不生效，只能往正向写）。

---

## 8. 磁盘与硬件

| 项 | 值 |
|---|---|
| GPU | RTX 3080 20GB（魔改卡，见下） |
| C: | 931G 总 / 200G 可用 |
| D: | 746G 总 / 134G 可用 |
| 拆层模型 | 12.9 GB |
| 单个 `.iki` | 2.5–3.1 MB |
| 单张 17 层 PSD | 38 MB |

**C 盘保留 ≥30GB** 是用户硬约束，任何大文件下载前先查。

⚠️ 这块 3080 是**魔改卡**，本身有 TDR（驱动超时重置）问题，不是驱动 bug——
连带 USB 断链和 Parsec 断连。跑长任务时如果黑屏重启，先怀疑它。

---

## 9. 已知环境坑（都会浪费一整轮）

| 坑 | 症状 | 解法 |
|---|---|---|
| 后台标签页节流 | rAF 一秒不触发、900ms 的 interval 只跑 1 次，所有参数回读都是 0 | `cdp("Page.bringToFront")` + `Emulation.setFocusEmulationEnabled` |
| canvas 停在 300×150 | 截图是 300×150 强拉大的，1px 层缝被放大成 4px | 先 `Emulation.setDeviceMetricsOverride(1280,1280,1)`，确认 `canvas.width` 再截 |
| 页面缓存 | 文件 MD5 变了但 4 次截图字节完全相同 | `Network.setCacheDisabled`，URL 加时间戳（**且保留 `?nodrive=1`**） |
| `capture_screenshot()` 固定路径 | 连拍互相覆盖，最后只剩一张 | 每次立刻写不同文件名 |
| 缩放伪造差异 | 1100 截图放大到 1280 再 diff，凭空多出 10 万「假像素」 | 必须 1:1 原生尺寸比对 |
| 页面说明文字污染 | 左下角白字被算进「模型有、原图没有」，脚区差异 2068→27887 | 截图前 `display:none` 掉含该文案的元素 |
| `psd.composite()` 偏亮 | See-through 17 层自身合成比原图**平均差 24.29、整片偏亮 25** | 颜色一律从原图图像文件读，不用 composite |
| `keep_as_is` 覆盖派生层 | 脚本不报错、层数对，但改动完全没生效 | 清单只放没派生过的层 |
| under-paint 阈值判空 | `src=None` 时整段静默跳过，改了等于没改 | 判空必须打 `!! ` 警告 |

---

## 10. 一键复现清单

```bash
# 1) 拆层（venv）
cd <pipeline>
venv/Scripts/python.exe split_layers.py      # 17 层 PSD → c04_split.psd（18 层）
venv/Scripts/python.exe build_iki2.py        # → out/c04_hand.iki

# 2) 部署到播放器
cp out/c04_hand.iki \
   <iki>/examples/player/public/model_hand.iki

# 3) 打开（白底，打开就在播）
explorer.exe "http://127.0.0.1:5200/standalone.html"
```
