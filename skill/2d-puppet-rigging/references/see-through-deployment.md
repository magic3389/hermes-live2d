# See-through：单图 → 带补画的分层 PSD

## 什么时候用它

单图切层（`@ikijs/mcp` 的 cut 管线）解决不了「**该有的内容根本不存在**」：被刘海遮住的额头、
眼睑下面的眼白、侧脸内耳。See-through 是生成式拆件（SDXL LayerDiff 3D + 动漫微调 Marigold 深度），
一张立绘直接出 17–23 个语义层 PSD，**被遮挡区域会补画**，层序按深度排好。
代价是重绘：它不是像素搬运，细节会损失（见「保真度」）。

## 部署（本机已验证的形态）

独立 venv，**绝不装进 C:\ComfyUI 的 venv**（那套环境有别的节点依赖，冲突就整片崩）：

```bash
git clone --depth 1 https://github.com/shitagaki-lab/see-through.git
uv python install 3.12
uv venv venv --python 3.12
# uv venv 默认没有 pip：要么 python -m ensurepip，要么全程 uv pip
uv pip install --python venv/Scripts/python.exe \
  torch==2.8.0+cu128 torchvision==0.23.0+cu128 torchaudio==2.8.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128        # 需 HTTPS_PROXY 指向本地代理
uv pip install --python venv/Scripts/python.exe -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

装完必须先验：`torch.cuda.is_available()` 为 True，且 24 个关键包逐个 import 通过
（torch / diffusers / transformers / accelerate / safetensors / psd_tools / cv2 / sklearn /
scipy / kornia / timm / omegaconf / simple_parsing…）。**只有 import 全过才叫装好了**，
uv 最后那行「Installed 4 packages」是增量，不代表依赖齐了。

官方还要求仓库根建 `assets` 符号链接（`ln -sf common/assets assets`）。主推理传绝对路径时用不到，
但 `ui/ui/launch.py` 缺了会 **FileNotFoundError 崩在启动**；默认参数 `--srcp assets/test_image.png`
也依赖它。

## 模型

| 用途 | 仓库 | 大小 |
|---|---|---|
| 分层生成（主体是 8.14GB 的 unet） | `layerdifforg/seethroughv0.0.2_layerdiff3d` | 10.16 GB |
| 动漫微调深度估计 | `layerdifforg/seethroughv0.0.1_marigold` | 3.1 GB |
| SAM 身体分割（**主推理不需要**） | `24yearsold/l2d_sam_iter2` | — |

第三个只被 `inference/scripts/parse_live2d.py`（数据管线 / Qt6 标注 UI）用，`inference_psd.py`
不加载它 —— 没下也不影响出图，但要用 UI 打标就得补。

三条硬规则（每条都让人卡过十几分钟）：

1. **深度模型仓库实际在 `layerdifforg/` 下。** 写 `24yearsold/seethroughv0.0.1_marigold` 会 307
   跳到过去，部分下载器跟随后速度掉到 0.1MB/s，表现成「卡住没进度」。先
   `curl -sI <url> | grep -i location` 确认最终仓库名再下。
2. **Python urllib 多线程分段在 hf-mirror 上可能长期 0 字节，而 curl 单连接能跑几十 MB/s。**
   同一个 URL、同一台机器。别死磕线程数（24 线程、16 线程、snapshot_download 单连接都试过），
   直接 `curl -sL -o <file>` 顺序下最快；小文件（tokenizer/ 下的 json）也要单独测，
   别只测主权重就下结论。
3. **curl 是原生程序：目标路径必须是 `D:/...`。** 写 `/d/...` 会被当成 `D:\d\...`，
   表现是 `exit 23` 写入失败、目录一片空。同理从 HF API 取文件列表时，Windows 下 Python 的
   `print` 带 `\r`，循环前必须 `tr -d '\r'`，否则每个 URL 末尾多一个回车 → 全部 404。

下完必须逐个 `safetensors.safe_open` 打开、报张量数才算完整（下载器只保证字节数，不保证内容）。
参考量级：unet 2452 张量 / text_encoder_2 517 / vae 248。

## 跑一张

```bash
cd see-through && source ../venv/Scripts/activate
python inference/scripts/inference_psd.py \
  --srcp "D:/abs/path/to/image.png" \
  --save_dir ../out/layerdiff_output \
  --repo_id_layerdiff D:/models/seethrough/layerdiff3d \
  --repo_id_depth D:/models/seethrough/marigold \
  --resolution 1280 --inference_steps 30 --save_to_psd --tblr_split --group_offload
```

- **`--srcp` 必须绝对路径**：脚本内部会换到自身目录，相对路径直接 FileNotFoundError
  （模型加载完才炸，白等两分钟）。
- `--repo_id_*` 传本地目录即可（diffusers 的 `from_pretrained` 认路径），不再依赖 HF cache 结构。
- **`--resolution` 一律给 1280**，别为了贴合原图尺寸去降。模型按 1280 训练，降到 1024 反而更差
  （同一张 1024 输入实测：1280 推理角色区 SSIM 0.9382，1024 推理 0.9073）。
- **`--tblr_split` 做 Live2D 必开**：把 `eyewhite` / `irides` / `eyelash` / `handwear` 这类成对部件
  自动拆成左右。不开就是左右眼挤在同一层里，后面还得自己切。
- `--resolution_depth` 默认 768，正是深度模型的训练分辨率，别跟着 `--resolution` 一起调高。
- `--srcp` 传目录会批量跑（`find_all_imgs`），一批图共用一次模型加载。
- **要拿去绑定就先把输入 pad 成正方形**：下游消费端（Anime2.5DRig）只收 768–2048 正方画布，
  非正方的竖版立绘（如 896×1152）会被拒或拉伸。做法：白底居中 `canvas.paste` 到 1280×1280
  （**不要拉伸**），pad 完检查角色四边边距 ≥40px，再喂 See-through。
- **腾显存正解**：跑之前
  `curl -s --noproxy '*' -X POST http://127.0.0.1:8188/free -H "Content-Type: application/json" -d '{"unload_models":true,"free_memory":true}'`，
  几秒内从 15.9 GB 掉到 0.3 GB；不要为此重启 ComfyUI（下次出图又要重新加载模型）。
- 显存：1280 / bf16 峰值 **18.5 GB / 20.5 GB**（且此时 ComfyUI 还常驻约 6.9 GB）。
  `--group_offload` 当保险常开，别等 OOM 再加；真不够再考虑停 ComfyUI，不要直接跳 NF4 量化。
- 耗时：本机 RTX 3080 实测 30 步 **4.5 分钟**（layerdiff 4.55 s/步 = 2:16，marigold 3.92 s/步 = 1:57），
  比早先记录的 11–12 s/步 快一倍多；排期按 5 分钟算，慢得离谱时先查是不是落到了 CPU 或半精度降级。

## 输出与验收

17 层，层名是拆件标准名，层序 = 深度序。**具体集合会随角色内容变，不要写死清单**：
同一台、同一套参数下，捧饭碗那张输出 `objects`（道具），鲸娘那张输出 `tail`（尾巴），
而且 `eyebrow / eyewhite / irides` 三层的先后顺序也会换。每次都重新枚举层名，
而不是照上一次的清单去找文件。基础集合大致是：
`back hair / legwear / footwear / neck / bottomwear / topwear / headwear / handwear /
[objects | tail] / face / nose / mouth / eyewhite / irides / eyebrow / eyelash / front hair`。

验收要拿像素说话，别只看合成图顺眼：

- 用 `psd_tools` 遍历 `psd.descendants()`，打印每层名字/尺寸/alpha 覆盖率，逐层导出 PNG 拼成
  清单图。**缩略图不能当证据**：17 格拼图里 `face` 看着像整块脸皮，放大就看见按刘海形状挖的缺口。
- **补画是「部分」不是「全补」**，要拿 alpha 反查量：用 `front hair` 的 alpha 去查 `face` 层，
  实测只有 **54.1%** 的像素在脸层上有皮肤（45.9% 仍是挖空的）；`eyelash` 覆盖区里 `eyewhite`
  只补到 **36.0%**。官方宣传 fully inpainted，实际对 Q 版/非常规比例更差。
  这个数字直接决定「转头会不会露缺口」，报告里必须和层清单一起给。
- 合成图与原图算 SSIM 有陷阱：**PSD 没有背景层**，`composite()` 把透明处画成黑，全图 SSIM 会被
  背景拖垮（同一张图全图 0.7599 vs 角色区内 0.9382）。比较保真度必须先估背景色、在角色 mask 内算
  （腐蚀几像素避开描边），否则会把好结果误判成不及格。

## 已知边界（对用户主动说）

- 官方自述：复杂配饰/道具会分错层，非标准姿势容易翻车；裙/裤腿前后关系常错，可用
  `inference/scripts/heuristic_partseg.py seg_wdepth` 按深度后处理。
- 生成式拆件必然抖细节。已验证可行的组合是「用它的 alpha 形状 + 从原图取纹理贴回」，
  但**必须按像素来源分区贴**：原图本来就能看到该层的位置用原图色，被遮住的补画区必须用生成色。
  整层贴原图会把别部位的颜色糊进来（实测 face 层里 60.75% 的像素变成头发色，脸和前发就「长在一起」）。
  写法与量化回验（修前 85648 个头发色像素 → 修后 0）见 `references/layer-gap-quality.md`。
- 拿到 PSD 之后怎么变成会动的模型、哪些现成工具直接吃这个 PSD、美工要按什么层名交件，
  见 `references/psd-consumer-tools.md`。
- 层名到 Iki 槽位不是一对一：要写映射，同槽多层按深度合并，再走 `autoRigFromLayers`
  与洋红透底量化。
- 官方还提供 ComfyUI 节点（`jtydhr88/ComfyUI-See-through`）与在线 demo（ModelScope 国内直连、
  HF ZeroGPU 每天 1–2 次），要快速验证效果可以先走在线，再决定本地装。
