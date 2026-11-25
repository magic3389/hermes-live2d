"""自动拆分双腿：从 legs 层的像素结构自动推导所有参数，不再手测。

用法：
    python auto_split_legs.py <split.psd> <原图png> <输出前缀>

输出：<前缀>_legmask_L.npy / _R.npy（float alpha）、<前缀>_vis.png（对照图）

推导过程（全部实测，不写死任何素材的数字）：
  1) 逐行测「段数」→ 找分叉点 fork_y（连续 5 行都是 2 段的第一行）
  2) 分叉点是一个范围还是根本没有合并区，决定走哪条路：
     · 有合并区 → 上段沿前腿实测宽度切，下段按连通域
     · 无合并区（腿一上来就是两条）→ 直接按连通域
  3) 前腿宽度 = 分叉点附近单段宽度的中位数
  4) 边界放到描边外侧（逐行找亮度突变）
  5) 重叠带 = max(18, 前腿宽度*0.35)，保证摆动不露缝
  6) 输出保留原始抗锯齿 alpha（不做 0/255 硬切，不做羽化 —— 羽化会让两层颜色掺混、
     视觉上融合成一条腿）

注意：命名 leg_L / leg_R 指**画面左 / 画面右**，不是角色左右。
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage

MIN_SEG_W = 12         # 一段至少要这么宽才算「一条腿」，滤掉描边碎点
FORK_RUN = 5           # 连续 5 行都是 2 段才认定分叉
STROKE_LUM = 110       # 亮度低于这个视为描边


def row_segments(mask_row, min_w=MIN_SEG_W):
    """返回一维 bool 行里的连续段 [(start, end), ...]（end 含）。"""
    xs = np.nonzero(mask_row)[0]
    if len(xs) == 0:
        return []
    segs, st, prev = [], xs[0], xs[0]
    for x in xs[1:]:
        if x > prev + 1:
            if prev - st + 1 >= min_w:
                segs.append((int(st), int(prev)))
            st = x
        prev = x
    if prev - st + 1 >= min_w:
        segs.append((int(st), int(prev)))
    return segs


def pick_two(segs, cx):
    """从多段里挑出属于两条腿的两段（避开散落的装饰件）。"""
    if len(segs) == 1:
        return segs
    # 取最宽的两段
    two = sorted(sorted(segs, key=lambda s: -(s[1] - s[0]))[:2], key=lambda s: s[0])
    return two


def main(psd_path, ref_path, out_prefix):
    psd = PSDImage.open(psd_path)
    W, H = psd.width, psd.height
    legs = None
    for l in psd.descendants():
        if l.is_group():
            continue
        if l.name.strip().lower() in ("legs", "leg"):
            im = l.topil()
            if im is None:
                continue
            im = im.convert("RGBA")
            ox, oy = l.offset
            if im.size != (W, H) or ox or oy:
                full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                full.paste(im, (ox, oy))
                im = full
            legs = np.asarray(im)
            break
    if legs is None:
        raise SystemExit("PSD 里没有 legs 层")

    al = legs[:, :, 3]
    m = al > 60
    rgb = legs[:, :, :3].astype(np.float32)
    lum = rgb.mean(axis=2)
    ref = np.asarray(Image.open(ref_path).convert("RGB")).astype(np.float32)

    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        raise SystemExit("legs 层是空的")
    top, bot = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    cx = (left + right) // 2
    print(f"legs: {int(m.sum())}px  x[{left},{right}] y[{top},{bot}]  中线 x={cx}")

    # ---- 1) 逐行段数 ----
    segs_by_row = {}
    for y in range(top, bot + 1):
        segs_by_row[y] = pick_two(row_segments(m[y]), cx)

    counts = {y: len(s) for y, s in segs_by_row.items()}
    print(f"段数分布: 1段 {sum(1 for v in counts.values() if v==1)} 行, "
          f"2段 {sum(1 for v in counts.values() if v==2)} 行, "
          f"其他 {sum(1 for v in counts.values() if v>=3)} 行")

    # ---- 2) 分叉点 ----
    fork_y = None
    run = 0
    for y in range(top, bot + 1):
        if counts[y] == 2:
            run += 1
            if run >= FORK_RUN:
                fork_y = y - FORK_RUN + 1
                break
        else:
            run = 0

    merged = (fork_y is None) or (fork_y - top < 40)
    print(f"分叉点 fork_y={fork_y}  →  {'无合并区（直接按连通域）' if merged else '有合并区（上段沿前腿宽度切）'}")

    # ---- 3) 分叉处的两条腿 ----
    probe_y = fork_y if fork_y is not None else top + 5
    segs = segs_by_row.get(probe_y) or []
    while len(segs) < 2 and probe_y < bot - 2:
        probe_y += 1
        segs = segs_by_row.get(probe_y) or []
    if len(segs) < 2:
        raise SystemExit(f"找不到两条腿（probe_y={probe_y} segs={segs}）")
    (la, lb), (ra, rb) = segs[0], segs[1]
    print(f"分叉处 y={probe_y}: 左段 x[{la},{lb}] 宽{lb-la+1} | 右段 x[{ra},{rb}] 宽{rb-ra+1}")

    # 前腿宽度：优先用**分叉区左段**的宽度中位数（那是腿体，最可靠）。
    # 不能用「单段行」的宽度 —— 单段行往往只剩鞋底那几行，实测 c07 只有 3 行、
    # 中位数算出 37px，而真实腿宽是 76px，导致底部合并处切错、鞋被整块划给一条腿。
    lw = []
    for y in range(top, bot + 1):
        s = segs_by_row.get(y) or []
        if len(s) >= 2:
            lw.append(s[0][1] - s[0][0] + 1)
    if lw:
        FRONT_W = int(np.median(lw))
        src_desc = f"分叉区左段 {len(lw)} 行中位数"
    else:
        widths = []
        for y in range(top, bot + 1):
            s = segs_by_row.get(y) or []
            if len(s) == 1:
                widths.append(s[0][1] - s[0][0] + 1)
        FRONT_W = int(np.median(widths)) if widths else (lb - la + 1)
        src_desc = f"单段行 {len(widths)} 行中位数"
    print(f"前腿宽度 FRONT_W={FRONT_W}（{src_desc}）")

    OVERLAP = max(18, int(FRONT_W * 0.35))
    print(f"重叠带 OVERLAP={OVERLAP}px")

    # ---- 4) 逐行定边界 ----
    split_x = np.zeros(H, dtype=np.int32)
    for y in range(top, bot + 1):
        s = segs_by_row.get(y) or []
        if len(s) == 0:
            split_x[y] = cx
            continue
        if len(s) == 1:
            bl, br = s[0]
            mid = (bl + br) / 2.0
            # 单段有两种情况，必须分开处理：
            #   ① 这块明显偏左/偏右 ⇒ 只是其中一条腿存在（另一条还没出现/已结束），整块归它
            #   ② 这块横跨中线且够宽 ⇒ 两条腿在画面里连成一片（鞋靠在一起），按 FRONT_W 切
            if abs(mid - cx) > FRONT_W * 0.5:
                split_x[y] = (br + 1) if mid < cx else bl
            else:
                split_x[y] = min(bl + FRONT_W, br + 1)
        else:
            (la2, lb2), (ra2, rb2) = s[0], s[1]
            # 两段：边界放在左段右端与右段左端之间。放到左段描边之外。
            edge = lb2 + 1
            for x in range(lb2, min(ra2, lb2 + 30)):
                if lum[y, x] < STROKE_LUM:
                    edge = x + 1
            split_x[y] = max(edge, lb2 + 1)

    # ---- 5) 生成两个 mask（保留原始 alpha）----
    xx = np.arange(W)[None, :]
    yy = np.arange(H)[:, None]
    ins = yy >= top
    sx = split_x[:, None]
    a01 = al.astype(np.float32) / 255.0

    m_front = m & ins & (xx <= sx)
    m_back = m & ins & (xx >= sx)

    # 下段（有合并区时）按连通域重分，贴住真实腿形
    if not merged and fork_y is not None:
        from scipy import ndimage as ndi
        low = np.zeros_like(m)
        low[fork_y:] = m[fork_y:]
        lbl, n = ndi.label(low)
        cand = []
        for i in range(1, n + 1):
            blk = lbl == i
            sz = int(blk.sum())
            if sz < 200:
                continue
            yx = np.nonzero(blk)[0]
            cand.append((sz, i, int(yx.mean())))
        if len(cand) >= 2:
            cand.sort(key=lambda t: -t[0])
            two = sorted(cand[:2], key=lambda t: t[2])
            l_ids, r_ids = [two[0][1]], [two[1][1]]
            for sz, i, cy in cand[2:]:
                (l_ids if cy < two[0][2] else r_ids).append(i)
            lowL = np.isin(lbl, l_ids)
            lowR = np.isin(lbl, r_ids)
            m_front = (m & (yy < fork_y)) | (lowL & ins) | (m_front & (yy >= fork_y))
            m_back = (m_back & (yy < fork_y)) | (lowR & ins)
            print(f"  下段按连通域重分: 左 {int(lowL.sum())}px / 右 {int(lowR.sum())}px")

    front = np.clip(a01 * m_front.astype(np.float32), 0, 1)
    back = np.clip(a01 * m_back.astype(np.float32), 0, 1)

    # 后腿向左补 OVERLAP（被前腿遮挡的部分），只补 alpha、内容取原图
    fill = np.zeros_like(m)
    for y in range(top, bot + 1):
        s = segs_by_row.get(y) or []
        if not s:
            continue
        edge = int(split_x[y])
        xl = max(0, edge - OVERLAP)
        for x in range(xl, edge):
            if not m[y, x] and ref[y, x].min() < 245:
                fill[y, x] = True
    back[fill] = 1.0
    m_back = m_back | fill
    print(f"  后腿向左补色 {int(fill.sum())}px")

    front = np.where(m_front, front, 0).astype(np.float32)
    back = np.where(m_back, back, 0).astype(np.float32)

    np.save(out_prefix + "_legmask_L.npy", front)   # 画面左
    np.save(out_prefix + "_legmask_R.npy", back)    # 画面右

    print(f"\n画面左腿 {int((front>0).sum())}px（半透明 {int(((front>0)&(front<0.99)).sum())}）")
    print(f"画面右腿 {int((back>0).sum())}px（半透明 {int(((back>0)&(back<0.99)).sum())}）")
    ov = int(((front > 0) & (back > 0)).sum())
    print(f"重叠 {ov}px")
    tot = np.clip(front + back, 0, 1)
    gap = (tot < 0.99) & (al > 60)
    print(f"两腿叠加未覆盖（会露底）{int(gap.sum())}px")

    # ---- 6) 对照图 ----
    vis = np.zeros((H, W, 3), np.uint8)
    vis[:, :, 0] = (front * 255).astype(np.uint8)
    vis[:, :, 1] = (back * 255).astype(np.uint8)
    base = Image.fromarray(vis, "RGB")
    dr = ImageDraw.Draw(base)
    for y in range(top, bot, 20):
        dr.line([(int(split_x[y]), y), (int(split_x[y]), y + 4)], fill=(255, 255, 0), width=1)
    base.crop((max(0, left - 30), max(0, top - 20), min(W, right + 30), min(H, bot + 20))).save(out_prefix + "_vis.png")
    print(f"对照图 {out_prefix}_vis.png（红=画面左腿 / 绿=画面右腿 / 黄线=边界）")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2], sys.argv[3])
