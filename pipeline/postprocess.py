"""通用后处理：See-through 17 层 → 可用 PSD。

用法：
    python postprocess_generic.py <seethrough.psd> <原图png> <输出.psd>

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

COLOR_TOL = 60
WRONG_TOL = 40     # 半透明像素的色差阈值：超过说明这层的 alpha/色都不可信      # 本层色与原图差异小于这个才换（避免把溢出内容烤进来）
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


def main(src_psd, src_img, out_psd, legmask_prefix=None, reclaim_box=None, sleeve_bands=None, skirt_zone_y0=None):
    psd = PSDImage.open(src_psd)
    W, H = psd.width, psd.height
    ref = np.asarray(Image.open(src_img).convert("RGB")).astype(np.int16)
    print(f"PSD {W}x{H}  原图 {ref.shape[1]}x{ref.shape[0]}")

    # --sleeve-band 参数是 [(x_sh,x_wr,y0,y1,half), ...]，在这里按画布尺寸生成 mask。
    # 按人体结构划带：肩部 x -> 腕部 x 线性插值，y0..y1，左右各 half 像素宽。
    _band_mask = np.zeros((H, W), dtype=bool)
    if sleeve_bands:
        for _x_sh, _x_wr, _y0, _y1, _half in sleeve_bands:
            for _y in range(max(0, _y0), min(H, _y1)):
                _t = (_y - _y0) / max(1, (_y1 - _y0))
                _cx = _x_sh + (_x_wr - _x_sh) * _t
                _band_mask[_y, max(0, int(_cx - _half)):min(W, int(_cx + _half))] = True
        print(f"  袖带 mask: {int(_band_mask.sum())}px")

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

    # ---- 2.6) 错色 + 低 alpha：alpha 也不可信 → 抬满 + 取原图 -------------
    # See-through 会给某层「低 alpha + 别人的色」。渲染时低 alpha 让深色被背景冲淡，
    # 表现为「那块内容变暗 / 消失」。实测 07 图腰左 x[302,466] y[613,832]：
    #   原图浅蓝发丝 (185,213,245)，bottomwear 只有 alpha=73、RGB(36,41,49)
    #   → 渲染成灰暗一团，发丝没了（用户判定：「左侧的头发缺失了，右边的头发还在」）。
    # 判据：本层色与原图差 >40 且原图非白 ⇒ 这块该显示的就是原图，本层的 alpha 不可信。
    # 只动「半透明 + 色差大」的像素；正常抗锯齿边缘的色差本来就小，不受影响。
    wrong_fixed = 0
    for name, arr in layers.items():
        a = arr[:, :, 3]
        semi = (a > 0) & (a < 250)
        if not semi.any():
            continue
        d = np.abs(arr[:, :, :3].astype(np.int16) - ref).max(axis=2)
        bad = semi & (d > WRONG_TOL) & (ref.min(axis=2) < 245)
        if bad.any():
            arr[bad, :3] = ref[bad].astype(np.uint8)
            arr[bad, 3] = 255
            wrong_fixed += int(bad.sum())
    if wrong_fixed:
        print(f"  错色低alpha修正: {wrong_fixed}px（差>{WRONG_TOL} 且原图非白 → 抬满+取原图）")

    # ---- 2.6b) 不透明但颜色完全对不上的像素 --------------------------------
    # §2.6 只管半透明。但 See-through 也会在 **alpha=255** 的地方带完全错误的色：
    # c10 实测 bottomwear/handwear 在原图浅色处带深蓝黑 (36,35,66)，这两层 order
    # 高于 legs，直接把腿和鞋尖盖成深色块（用户：「腿部边和鞋尖的杂色块」）。
    # 判据 = 「色差>90」+ **排除冷色暗部**。三次实测的权衡：
    #   差>90  → 错色块修得最干净，但裙摆暗部（冷蓝 B>R）被一起抹平
    #   差>120 → 块勉强，裙摆好转
    #   皮肤色 → 裙摆完美，块A 反而退步（118 vs 原图 166）
    # 结论：错色块都落在 R≥B 的暖色皮肤上，而裙摆暗部是 B>R 的冷蓝。
    # 用「不是冷色」做闸门，两个目标同时满足。
    opaque_bad = 0
    for name, arr in layers.items():
        a = arr[:, :, 3]
        full = a >= 250
        if not full.any():
            continue
        d = np.abs(arr[:, :, :3].astype(np.int16) - ref).max(axis=2)
        lum_layer = arr[:, :, :3].mean(axis=2)
        lum_ref = ref.mean(axis=2)
        warm = ref[:, :, 2] <= ref[:, :, 0] + 15          # 非冷色（排除裙子蓝）
        bad = full & (d > 90) & warm & (lum_ref > 110) & (lum_ref > lum_layer + 55)
        if bad.any():
            arr[bad, :3] = ref[bad].astype(np.uint8)
            arr[bad, 3] = 255
            opaque_bad += int(bad.sum())
    if opaque_bad:
        print(f"  不透明错色修正: {opaque_bad}px（alpha=255 但色差>90 且原图亮得多 → 取原图）")

    # ---- 2.7) 头发补回：被衣服层占用、但颜色不对的发色像素 ----------------
    # See-through 会把垂下的长发判给衣服层。实测 07 图 back hair 右侧 91,340px、
    # 左侧只有 18,938px（差 4.8 倍）；区域 x[455,650] y[240,710] 里 4,218px 是发色，
    # 却被 bottomwear/handwear/topwear 占着，且它们带的色与原图差 >40
    # （用户判定：「左侧的头发缺失了一部分，和右边不对称」）。
    # 判据三层：①在原图是发色 ②被衣服层覆盖 ③该衣服层在此处的色差 >WRONG_TOL
    # 动作：从衣服层挖掉、补进 back hair（RGB 取原图，跟头动）。
    if "back hair" in layers and reclaim_box:
        x0, y0, x1, y1 = reclaim_box
        zone = np.zeros((H, W), dtype=bool)
        zone[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
        hairish = ((ref[:, :, 2] > ref[:, :, 0] + 20) & (ref[:, :, 2] > 100)
                   & (ref.mean(axis=2) < 190))
        cand = zone & hairish
        reclaimed = 0
        for nm in ("bottomwear", "handwear", "topwear", "face", "neck"):
            if nm not in layers:
                continue
            la2 = layers[nm]
            aa = la2[:, :, 3]
            if not (aa > 0).any():
                continue
            dif = np.abs(la2[:, :, :3].astype(np.int16) - ref).max(axis=2)
            take = cand & (aa > 0) & (dif > WRONG_TOL)
            if take.any():
                la2[take, 3] = 0
                cand = cand & ~take
                reclaimed += int(take.sum())
        bh = layers["back hair"]
        add = cand & (bh[:, :, 3] == 0)
        if add.any():
            bh[add, :3] = ref[add].astype(np.uint8)
            bh[add, 3] = 255
        if reclaimed or add.any():
            print(f"  头发补回: 从衣服层挖 {reclaimed}px → back hair 补 {int(add.sum())}px")

    # ---- 2.7b) 头发层里的「非发色」像素：挖出交给上层 ----------------------
    # See-through 会把白色袖子 / 肤色错分给 back hair。back hair 在层序最底，
    # 于是这些像素被上层衣服盖住 —— 表现为「胳膊没了」（c05 实测 29,301px
    # 白色袖子 (245,241,246) 藏在 back hair 里）。
    # 判据：在该层内 + 原图是浅白/肤色 + 与该层主色差 >40。
    if "back hair" in layers:
        for _src in ("back hair", "front hair"):
            if _src not in layers:
                continue
            _bh = layers[_src]
            _a = _bh[:, :, 3]
            if not _a.any():
                continue
            _hair_ref = ref[(_a > 0) & (ref[:, :, 2] > ref[:, :, 0] + 15)]
            if _hair_ref.size <= 100:
                continue
            _hc = np.median(_hair_ref, axis=0)
            # ⚠ 必须用**原图**色判断「这块不是头发」：层自己的 RGB 已被
            # See-through 涂成发色（这正是要抓的错分），拿它做比较永远差 0。
            _dif = np.abs(ref.astype(np.int16) - _hc).max(axis=2)
            _light = ((ref.min(axis=2) > 195)
                      & (ref[:, :, 0] >= ref[:, :, 2] - 12))
            _mis = (_a > 0) & _light & (_dif > 40)
            if _mis.sum() <= 200:
                continue
            _bh[_mis, 3] = 0
            _moved = int(_mis.sum())
            for _tgt in ("topwear", "handwear", "bottomwear"):
                if _tgt not in layers:
                    continue
                _tw = layers[_tgt]
                _put = _mis & (_tw[:, :, 3] == 0)
                if _put.any():
                    _tw[_put, :3] = ref[_put].astype(np.uint8)
                    _tw[_put, 3] = 255
                # 目标层在那里只覆盖了半透明内容时也必须顶掉：否则 See-through
                # 留的半透明发色会与白袖子混合，渲染出来偏灰、边缘糊（实测如此）。
                _weak = _mis & (_tw[:, :, 3] > 0) & (_tw[:, :, 3] < 250)
                if _weak.any():
                    _tw[_weak, :3] = ref[_weak].astype(np.uint8)
                    _tw[_weak, 3] = 255
            print(f"  {_src} 里的非发色像素归位: {_moved}px → 上层（白袖子/肤色被错分给头发层）")

    # ---- 2.8) 兜底层 backdrop：补 See-through 漏掉的内容 ----
    # See-through 会漏掉一些区域（所有层 alpha 全 0，但原图那里是实体内容），
    # 实测 07 图 x[668,689] y[1100,1140] 整块裙子被漏掉，拆腿后露出白色背景。
    # 做法：原图非白 & 任何层都没覆盖 → 放进一张最底层的补底图。
    # 它画序最底，不会盖住已分好的层。
    covered = np.zeros((H, W), dtype=bool)
    for arr in layers.values():
        covered |= arr[:, :, 3] > 0
    hole = (~covered) & (ref.min(axis=2) < 245)

    # ---- 2.8b) 浅色投影/背景残留不补 --------------------------------------
    # 脚与脚之间、尾巴与裙子之间的**投影**在原图里是浅蓝（实测 (156,181,211) (192,221,249)），
    # 它既不是纯白（min<245 被当内容）、也没有任何语义层认领，于是被 backdrop 补进来，
    # 渲染成一块「背景残留」（用户：「双脚中间还保留了一部分原图背景」「尾巴和裙子中间有个三角」）。
    # 判据：亮度 >175 且 B>200 且 B−R>30 = 浅蓝投影/背景。**保护 y<200 的头顶发梢**（那片也是浅蓝，
    # 但是真头发，实测 485px (163,195,237)）。
    proj = hole & (ref.mean(axis=2) > 175) & (ref[:, :, 2] > 200) & \
           (ref[:, :, 2].astype(int) - ref[:, :, 0] > 30)
    proj[:200, :] = False
    if proj.any():
        n_proj = int(proj.sum())
        hole = hole & ~proj
        print(f"  剔除浅色投影/背景残留: {n_proj}px（不补进 backdrop）")

    # 同一批投影像素也可能被**语义层**认领 —— See-through 会把脚间投影并进 legs（实测
    # 脚间 leg_L 2547px + leg_R 3608px 就是投影），拆腿后照样渲染出来，看起来「裁切不干净」。
    # 这里改用**全图**判据、只对容易被投影污染的层生效：
    #   legs / bottomwear / tail / handwear / topwear
    # ⚠ 绝不动 back hair / front hair / headwear —— 头顶的浅蓝发梢 (163,195,237) 也满足
    #   同一个颜色条件，误伤就会缺一块头发。
    proj_full = (ref.mean(axis=2) > 175) & (ref[:, :, 2] > 200) & \
                (ref[:, :, 2].astype(int) - ref[:, :, 0] > 30)
    proj_full[:200, :] = False
    n_sem = 0
    for nm2 in ("legs", "leg_L", "leg_R", "bottomwear", "tail", "handwear", "topwear"):
        arr2 = layers.get(nm2)
        if arr2 is None:
            continue
        hit = proj_full & (arr2[:, :, 3] > 0)
        if hit.any():
            arr2[hit, 3] = 0
            n_sem += int(hit.sum())
    if n_sem:
        print(f"  语义层里的浅色投影清掉: {n_sem}px（legs/bottomwear/tail/handwear/topwear）")

    # ---- 2.9) 轮廓外的孤立深色碎片：抠图残留 ------------------------------
    # BiRefNet 在**深色背景**素材上会在人物轮廓外留下零散深色像素（c10 实测
    # 腿部 14,534px，表现为「膝盖下 / 小腿下缘 / 脚腕 / 鞋尖有没扣干净的深色」）。
    # 判据必须三重限定，否则会误删角色本身的深色小部件：
    #   ① 深色（min<130）② 邻域 4px 内有大片纯白（说明孤立在背景里）
    #   ③ 不与人物主体连通，且连通块 < 900px
    _white = ref.min(axis=2) > 238
    from scipy import ndimage as _ndi
    _whitenear = _ndi.binary_dilation(_white, np.ones((3, 3)), iterations=4)
    _dark = ref.min(axis=2) < 130
    _resid = _dark & _whitenear
    if _resid.any():
        _allcov = np.zeros((H, W), dtype=bool)
        for _v in layers.values():
            _allcov |= _v[:, :, 3] > 0
        _ll, _nn = _ndi.label(_allcov, np.ones((3, 3)))
        if _nn:
            _sizes = np.bincount(_ll.ravel())
            _sizes[0] = 0
            _main = _ll == int(_sizes.argmax())
            _outside = ~_ndi.binary_dilation(_main, np.ones((3, 3)), iterations=2)
            _cand = _resid & _outside
            if _cand.any():
                _l2, _n2 = _ndi.label(_cand, np.ones((3, 3)))
                _sz2 = np.bincount(_l2.ravel())
                _sz2[0] = 0
                _drop_ids = [i for i in range(1, _n2 + 1) if _sz2[i] < 900]
                if _drop_ids:
                    _drop = np.isin(_l2, _drop_ids)
                    for _v in layers.values():
                        _v[_drop, 3] = 0
                    print(f"  清掉轮廓外孤立深色碎片: {int(_drop.sum())}px（抠图残留）")

    # ---- 2.10) 袖带里 bottomwear 的袖子像素必须挖掉 ------------------------
    # bottomwear 挂 def_Skirt（裙子物理 ±2.7°）。若袖子像素留在里面，动态时
    # 袖子会跟着裙子一起摆 —— 肉眼判定为「胳膊是透明的 / 飘」（c05 实测袖带内
    # bottomwear 有 30,348px 浅色像素，而 topwear/handwear 已各有 33k/28k 完整覆盖）。
    # 只在 topwear 或 handwear 同位置也有不透明内容时才挖，避免露底。
    if _band_mask.any():
        _bw = layers.get("bottomwear")
        if _bw is not None:
            _cov = np.zeros((H, W), dtype=bool)
            for _t in ("topwear", "handwear"):
                if _t in layers:
                    _cov |= layers[_t][:, :, 3] > 250
            _m = (_band_mask & (_bw[:, :, 3] > 0)
                  & (ref.min(axis=2) > 200) & _cov)
            if _m.any():
                _bw[_m, 3] = 0
                print(f"  袖带内 bottomwear 的袖子像素挖掉: {int(_m.sum())}px（防止跟裙子物理一起摆）")

        # 袖带内的**深色描边**必须补进 topwear。See-through 常把这些勾线漏掉，
        # 挖完 bottomwear 后就露出后面的头发/背景 —— 肉眼判定为「袖子是透明的」
        # （c05 实测 x[526,529]y[571,591] 原图 (68,75,122) 渲染成发色 (126,137,197)）。
        _tw = layers.get("topwear")
        if _tw is not None:
            _darkline = (ref.min(axis=2) < 145)
            _miss = _band_mask & _darkline & (_tw[:, :, 3] < 250)
            if _miss.sum() > 50:
                _tw[_miss, :3] = ref[_miss].astype(np.uint8)
                _tw[_miss, 3] = 255
                print(f"  袖带内深色描边补回 topwear: {int(_miss.sum())}px（否则露出后面头发→看着透明）")

    # ---- 2.11) 裙子区内容归并到 bottomwear（修「裙子分层/一块不动」）----------
    # See-through 把同一条裙子劈给了 topwear 和 bottomwear（c05 实测 y>820 区
    # topwear 20,163px / bottomwear 20,168px，几乎五五开）。绑定器按层名挂物理，
    # 于是同一块布料一半跟身体(不摆)、一半跟裙子(摆) ⇒ 动起来明显分层。
    # 修法：裙摆几何范围内的 topwear 内容并回 bottomwear，让它整块跟 def_Skirt。
    if skirt_zone_y0 is not None:
        _tw = layers.get("topwear")
        _bw = layers.get("bottomwear")
        if _tw is not None and _bw is not None:
            _zone = np.zeros((H, W), dtype=bool)
            _zone[max(0, skirt_zone_y0):, :] = True
            # 归并 **整个裙子区** 的 topwear 内容，但排除袖带（袖子属于上衣，被搬进
            # 裙子层会跟着裙子物理摆 —— 那是问题 8）。
            # 曾只归并「两层重叠」部分，导致 topwear 独占的裙子残留（c05 实测
            # 裙子区仍有 20,543px），它 order 更高又不跟 warp，盖住变形的 bottomwear
            # ⇒ 裙摆外轮廓完全不动，只有内部饰带被拉伸（用户看到的就是「分层」）。
            _mv = _zone & (_tw[:, :, 3] > 0)
            if _band_mask.any():
                _mv &= ~_band_mask
            if _mv.sum() > 500:
                _need = _mv & ~(_bw[:, :, 3] > 250)
                _bw[_need, :3] = ref[_need].astype(np.uint8)
                _bw[_need, 3] = 255
                _tw[_mv, 3] = 0
                print(f"  裙子区 topwear 内容并回 bottomwear: {int(_mv.sum())}px（避免一半摆一半不摆）")

    # ---- 2.12) tail 里混的腿部像素挖掉（修「尾巴上有腿的痕迹」）-------------
    # See-through 把腿部像素也归给 tail（c05 实测 tail∩legs = 21,427px）。
    # tail 挂 def_Tail、腿挂 def_LegL/R，两组动法不同 ⇒ 腿的影像跟着尾巴跑。
    # 只挖「原图是暖色皮肤」的部分，尾巴本身的蓝色像素不动。
    if "tail" in layers:
        _tl = layers["tail"]
        _legm = np.zeros((H, W), dtype=bool)
        for _n in ("legs", "leg_L", "leg_R"):
            if _n in layers:
                _legm |= layers[_n][:, :, 3] > 0
        if _legm.any() and (_tl[:, :, 3] > 0).any():
            _skinref = (ref[:, :, 0] >= ref[:, :, 2] + 10) & (ref.mean(axis=2) > 120)
            _hit = (_tl[:, :, 3] > 0) & _legm & _skinref
            if _hit.sum() > 500:
                _tl[_hit, 3] = 0
                print(f"  tail 里混的腿部像素挖掉: {int(_hit.sum())}px（否则腿痕跟着尾巴摆）")

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
        print("      --reclaim-hair x0,y0,x1,y1                 把被衣服层吃掉的头发补回 back hair")
        print("      --sleeve-band x_sh,x_wr,y0,y1,half         （可重复）袖带内 bottomwear 的袖子像素挖掉")
        print("                                                y1 取腕线，别拖到裙摆区（否则裙子被当成袖子保护住）")
        print("      --skirt-zone y0                            裙子几何区：区内 topwear 内容并回 bottomwear")
        sys.exit(1)
    _legs = None
    if "--legs" in sys.argv:
        _i = sys.argv.index("--legs")
        _legs = sys.argv[_i + 1]
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]
    _rec = None
    if "--reclaim-hair" in sys.argv:
        _i = sys.argv.index("--reclaim-hair")
        _rec = tuple(int(v) for v in sys.argv[_i + 1].split(","))
        assert len(_rec) == 4, "--reclaim-hair 需要 x0,y0,x1,y1"
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]

    # --sleeve-band x_sh,x_wr,y0,y1,half  可重复
    # 按人体结构划袖带：肩部 x -> 腕部 x 线性插值，从 y0 到 y1，左右各 half 宽
    _bands = []
    while "--sleeve-band" in sys.argv:
        _i = sys.argv.index("--sleeve-band")
        _v = tuple(int(x) for x in sys.argv[_i + 1].split(","))
        assert len(_v) == 5, "--sleeve-band 需要 x_sh,x_wr,y0,y1,half"
        _bands.append(_v)
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]

    _sky0 = None
    if "--skirt-zone" in sys.argv:
        _i = sys.argv.index("--skirt-zone")
        _sky0 = int(sys.argv[_i + 1])
        sys.argv = sys.argv[:_i] + sys.argv[_i + 2:]

    main(sys.argv[1], sys.argv[2], sys.argv[3], _legs, _rec, _bands or None, _sky0)
