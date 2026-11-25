"""通用后处理：See-through 17 层 → 可用 PSD。

用法：
    python postprocess.py <seethrough.psd> <原图png> <输出.psd>

做三件事（都是 c04 上踩出来的必要处理）：
  1) 颜色换回原图 —— See-through 合成比原图整体偏亮约 25，直接用就是"蒙了一层白纱"。
     必须**分区**换（本层色 ≈ 原图才换），整张画布无条件换会把下层内容烤进本层。
  2) legwear + footwear 合并成 legs（鞋/袜/脚是一个整体，拆了会鞋盖袜或袜盖鞋）。
  3) 层名归一化。

不做的事（这些是特定素材的问题，要单独写补丁）：
  · 拆双腿 leg_L/leg_R（见 split_legs.py）
  · 挖 topwear 盖住的脖子
  · 挖 headwear 抢走的耳朵
  · 挖 bottomwear 盖住腿的溢出带
"""
import os
import sys

import numpy as np
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer

ALPHA_MERGE = (("legwear", "footwear", "legs"),)

COLOR_TOL = 60      # 本层色与原图差异小于这个才换（避免把溢出内容烤进来）
ALPHA_FLOOR = 60    # alpha 低于此值视为 See-through 的"底膜"，直接清零

# 为什么要有 ALPHA_FLOOR：
#   See-through 会给某些层输出一层覆盖几乎全画布的极低 alpha 底膜。
#   实测 c04：mouth 有 1,520,597px 落在 alpha 1–60，而真实嘴线只有 1,259px；
#            eyewhite 1,252,128px 落在 1–60，真实眼白 4,399px。
#   15 号图同样：eyewhite 1,130,543px、face 878,744px。
#   这层底膜叠上去会让整张画面发亮/发白（c04 曾实测偏亮 24.29），必须清掉。
#   真实内容的抗锯齿边缘通常 >100，砍 60 以下不会伤到轮廓。


def norm(name):
    k = name.strip().lower().replace("-", "_").replace(" ", "_")
    alias = {
        "back_hair": "back hair", "hair_back": "back hair",
        "front_hair": "front hair", "hair_front": "front hair",
        "eye_white": "eyewhite", "sclera": "eyewhite",
        "iris": "irides", "top": "topwear", "skirt": "bottomwear",
    }
    return alias.get(k, k.replace("_", " "))


def main(src_psd, src_img, out_psd, legmask_prefix=None):
    psd = PSDImage.open(src_psd)
    W, H = psd.width, psd.height
    ref = np.asarray(Image.open(src_img).convert("RGB")).astype(np.int16)
    print(f"PSD {W}x{H}  原图 {ref.shape[1]}x{ref.shape[0]}")

    layers = {}
    for l in psd.descendants():
        if l.is_group():
            continue
        im = l.topil()
        if im is None:
            continue
        im = im.convert("RGBA")
        # ⚠ 层的 topil() 尺寸不一定等于画布（PSD 层可以带 offset / 被裁切）。
        # 直接用它当画布尺寸会炸（实测 07 图某层是 1230x757，画布是 1280x1280）。
        ox, oy = l.offset
        if im.size != (W, H) or ox or oy:
            full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            full.paste(im, (ox, oy))
            im = full
        layers[norm(l.name)] = np.asarray(im).astype(np.uint8)
    print(f"读入 {len(layers)} 层: {sorted(layers)}")

    # ---- 0) 清掉底膜 ----
    # See-through 给某些层输出的极低 alpha 覆盖层，叠上去会让整张画面发亮。
    covered = 0
    for name, arr in layers.items():
        a = arr[:, :, 3]
        kill = (a > 0) & (a < ALPHA_FLOOR)
        if kill.any():
            arr[kill, 3] = 0
            covered += int(kill.sum())
    print(f"  清掉 alpha<{ALPHA_FLOOR} 的底膜: {covered}px")

    # ---- 1) 合并 legwear + footwear ----
    for a, b, dst in ALPHA_MERGE:
        if a in layers and b in layers:
            la, lb = layers.pop(a), layers.pop(b)
            aa, ab = la[:, :, 3].astype(np.float32) / 255, lb[:, :, 3].astype(np.float32) / 255
            out_a = aa + ab * (1 - aa)                     # over 合成，顺序：legwear 在下
            p = la[:, :, :3].astype(np.float32) * aa[:, :, None] + \
                lb[:, :, :3].astype(np.float32) * (ab * (1 - aa))[:, :, None]
            rgb = np.where(out_a[:, :, None] > 1e-4,
                           p / np.maximum(out_a[:, :, None], 1e-4), 0)
            merged = np.dstack([rgb, out_a * 255]).astype(np.uint8)
            # ⚠ 合并后 RGB 常有算坏的地方：out_a 很小的位置 p/out_a 会算出近黑 (0,0,0)，
            # 而后面「颜色换回原图」的容差 60 够不到这种差异（黑 vs 蓝白差 150+），
            # 就原样留在纹理里 —— 实测 07 图出现 2323px 的纯黑块（x[630,682] y[1079,1191]）。
            # 腿/袜/鞋的内容本来就是原图，直接全部取原图最稳。
            mrg = merged[:, :, 3] > 0
            merged[mrg, :3] = ref[mrg].astype(np.uint8)
            # 原图非白但 merged 没盖到的洞（两层边界）用原图补。
            # ⚠ 不能全画布补：那样会把头/身体/裙子全算进腿层（实测补出 374,055px，
            # 而人物总共才 425,781px）。只补「腿层附近」的洞。
            from scipy import ndimage as _ndi
            core = merged[:, :, 3] > 0
            near = _ndi.binary_dilation(core, np.ones((3, 3)), iterations=8)
            hole = (~core) & (ref.min(axis=2) < 245) & near
            if hole.any():
                merged[hole, :3] = ref[hole].astype(np.uint8)
                merged[hole, 3] = 255
            layers[dst] = merged
            print(f"  合并 {a}+{b} → {dst}  {int((merged[:,:,3]>0).sum())}px（补洞 {int(hole.sum())}px）")

    # ---- 2) 颜色换回原图（分区判据） ----
    fixed = 0
    dark_fixed = 0
    for name, arr in layers.items():
        a = arr[:, :, 3]
        m = a > 0
        if not m.any():
            continue
        rgb = arr[:, :, :3].astype(np.int16)
        d = np.abs(rgb - ref).max(axis=2)
        take = m & (d <= COLOR_TOL)          # 只看"本层颜色和原图本来就接近"的像素
        if take.any():
            arr[take, :3] = ref[take].astype(np.uint8)
            fixed += int(take.sum())
        # 兜底：本层是近黑(≤15)但原图不是黑的 —— 一定是算坏的（alpha 极低处除出黑），
        # 直接取原图。容差判据覆盖不到这种（黑 vs 彩色差 150+）。
        dark = m & (rgb.max(axis=2) <= 15) & (ref.max(axis=2) > 40)
        if dark.any():
            arr[dark, :3] = ref[dark].astype(np.uint8)
            dark_fixed += int(dark.sum())
        layers[name] = arr
    print(f"  颜色换回原图: {fixed}px（只换本层色与原图差 ≤{COLOR_TOL} 的像素）")
    if dark_fixed:
        print(f"  修正算坏的黑块: {dark_fixed}px（本层近黑但原图非黑 → 取原图）")

    # ---- 2.8) 兜底层 backdrop：补 See-through 漏掉的内容 ----
    # See-through 会漏掉一些区域（所有层 alpha 全 0，但原图那里是实体内容），
    # 实测 07 图 x[668,689] y[1100,1140] 整块裙子被漏掉，拆腿后露出白色背景。
    # 做法：原图非白 & 任何层都没覆盖 → 放进一张最底层的补底图。
    # 它画序最底，不会盖住已分好的层。
    covered = np.zeros((H, W), dtype=bool)
    for arr in layers.values():
        covered |= arr[:, :, 3] > 0
    hole = (~covered) & (ref.min(axis=2) < 245)
    if hole.any():
        from scipy import ndimage as _ndi
        # 去掉零星噪点（原图 JPEG 压缩在纯白背景上留下的脏点）
        lbl, n = _ndi.label(hole, np.ones((3, 3)))
        sizes = np.bincount(lbl.ravel())
        sizes[0] = 0
        keep = sizes >= 60
        hole = keep[lbl]
        if hole.any():
            bd = np.zeros((H, W, 4), dtype=np.uint8)
            bd[:, :, :3] = ref.astype(np.uint8)
            bd[hole, 3] = 255
            layers["backdrop"] = bd
            print(f"  兜底层 backdrop: {int(hole.sum())}px（See-through 漏掉的内容，画序最底）")

    # ---- 2.5) 双腿拆分（可选，给 --legs <mask前缀>）----
    # 必须在**写出 PSD 之前**做：读回已有 PSD 再改有 alpha 损耗（实测 topil→frompil 差 4.82），
    # composite() 更糟（会把下层烤进本层）。
    if legmask_prefix and "legs" in layers:
        mL_path = legmask_prefix + "_legmask_L.npy"
        mR_path = legmask_prefix + "_legmask_R.npy"
        if os.path.exists(mL_path) and os.path.exists(mR_path):
            mL = np.load(mL_path)
            mR = np.load(mR_path)
            # ⚠ RGB 必须取**原图**，不能取 legs 层：
            # 补色区在原 legs 里 alpha=0、RGB 是 (0,0,0)（见上方的除零问题），
            # 拿它当内容会在两腿之间画出一块纯黑（实测 07 图 2352px 黑块）。
            lrgb = ref.astype(np.uint8)
            for tag, mm in (("leg_L", mL), ("leg_R", mR)):
                arr = np.zeros((H, W, 4), dtype=np.uint8)
                arr[:, :, :3] = lrgb
                arr[:, :, 3] = np.round(np.clip(mm, 0, 1) * 255).astype(np.uint8)
                layers[tag] = arr
                semi = int(((arr[:, :, 3] > 0) & (arr[:, :, 3] < 255)).sum())
                print(f"  {tag}: {int((arr[:,:,3]>0).sum())}px（半透明 {semi}）")
            del layers["legs"]
            print(f"  双腿拆分完成（腿内容取 legs 层原像素，alpha 取 mask）")
        else:
            print(f"  !! 找不到 {mL_path} / {mR_path}，跳过拆腿")

    # ---- 3) 写出 ----
    out = PSDImage.new(mode="RGBA", size=(W, H))   # 必须 RGBA，RGB 会让每层 alpha 全 255
    # ⚠ 层顺序 = 画序：**先写的在下面**。必须按语义从后往前排，
    # 之前按「面积升序」写（想当然是小的在下面）会让裙子盖住脸、头发盖住眼睛，
    # 实测合成差从 9.89 劣化到 28.03。
    ORDER_WRITE = [
        "backdrop", "back hair", "tail", "legs", "leg_L", "leg_R",
        "neck", "bottomwear", "handwear", "apron_top", "topwear",
        "headwear", "ear_L", "ear_R", "ears",
        "face", "nose", "mouth", "eyebrow", "eyewhite", "irides", "eyelash",
        "front hair",
    ]
    seq = [n for n in ORDER_WRITE if n in layers]
    seq += [n for n in layers if n not in ORDER_WRITE]     # 表外层补在最上面
    for name in seq:
        arr = layers[name]
        if not (arr[:, :, 3] > 0).any():
            continue
        layer = PixelLayer.frompil(Image.fromarray(arr, "RGBA"), out, name)
        out.append(layer)
    out.save(out_psd)

    # ---- 自检 ----
    comp = np.asarray(out.composite().convert("RGB")).astype(np.int16)
    d = np.abs(comp - ref).max(axis=2)
    om = ref.min(axis=2) < 250
    print(f"\n写出 {out_psd}")
    print(f"  层数 {len(out)}")
    print(f"  合成 vs 原图: 平均差 {d[om].mean():.2f}  差异>30 {int((d>30)[om].sum())}px / {int(om.sum())}px")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        print("\n可选：--legs <mask前缀>   把 legs 拆成 leg_L/leg_R（前缀需有 _legmask_L.npy/_R.npy）")
        sys.exit(1)
    _legs = None
    if "--legs" in sys.argv:
        _i = sys.argv.index("--legs")
        _legs = sys.argv[_i + 1]
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]
    main(sys.argv[1], sys.argv[2], sys.argv[3], _legs)
