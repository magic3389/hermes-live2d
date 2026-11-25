#!/usr/bin/env python3
"""A-pose 几何判定（不靠 VLM）。

原理：A-pose 时手臂与躯干之间有背景贯通，躯干中段每一横行的角色像素会分成 3 段
（左臂 / 躯干 / 右臂）；叉腰、手收身前、手贴身体时只剩 1 段。

阈值标定（人工确认过的样本）：
    真 A-pose        三段 82–93%   单段 1–13%
    叉腰 / 手在前    三段 35–67%   单段 18–36%
=> 三段 >= 75% 判 A-POSE；<75% 且单段 >=25% 判 NOT-A-POSE；中间判 AMBIGUOUS 需人工复核。

用法:
    python apose_check.py <png> [png ...]
    python apose_check.py --batch "<ComfyUI>/output/apose_*.png"
"""
import glob
import sys

import numpy as np
from PIL import Image
from scipy.ndimage import label


def _body_mask(a, thresh_pure=0.2):
    """返回角色 mask（True=角色）。压过纯白底的图直接按 ==255 判定；否则用亮度+饱和度。"""
    pure = (a[:, :, 0] == 255) & (a[:, :, 1] == 255) & (a[:, :, 2] == 255)
    if pure.mean() >= thresh_pure:
        return ~pure
    mx, mn = a.max(2), a.min(2)
    return ~((mn >= 205) & ((mx - mn) <= 45))


def analyze(path, band=(0.40, 0.62), min_seg_px=3):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    H, W, _ = a.shape
    body = _body_mask(a)

    ys, xs = np.nonzero(body)
    if len(ys) == 0:
        return None
    y0, y1 = ys.min(), ys.max()
    top = int(y0 + (y1 - y0) * band[0])
    bot = int(y0 + (y1 - y0) * band[1])

    three = one = total = 0
    for y in range(top, bot):
        row = body[y]
        if row.sum() < 5:
            continue
        lab, n = label(row)
        segs = sum(1 for k in range(1, n + 1) if (lab == k).sum() >= min_seg_px)
        total += 1
        if segs >= 3:
            three += 1
        elif segs == 1:
            one += 1

    if total == 0:
        return None
    r3, r1 = three / total * 100, one / total * 100
    if r3 >= 75:
        verdict = "A-POSE"
    elif r1 >= 25:
        verdict = "NOT-A-POSE"
    else:
        verdict = "AMBIGUOUS"

    margin = {
        "L": int(xs.min()),
        "R": int(W - 1 - xs.max()),
        "T": int(ys.min()),
        "B": int(H - 1 - ys.max()),
    }
    corners = [
        int(a[y:y + 40, x:x + 40].min())
        for y, x in ((0, 0), (0, W - 40), (H - 40, 0), (H - 40, W - 40))
    ]
    return {
        "file": path.replace("\\", "/").split("/")[-1],
        "seg3": round(r3, 1),
        "seg1": round(r1, 1),
        "rows": total,
        "verdict": verdict,
        "margin_min": min(margin.values()),
        "corners_white": all(c == 255 for c in corners),
        "size": [W, H],
    }


def fmt(r):
    ok = r["verdict"] == "A-POSE" and r["corners_white"] and r["margin_min"] > 8
    return (f"{'PASS' if ok else '    '} {r['file']:26} 三段{r['seg3']:5.1f}% 单段{r['seg1']:5.1f}% "
            f"四角{'白' if r['corners_white'] else 'NG'} 最小留白{r['margin_min']:3}px => {r['verdict']}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--batch":
        paths = sorted(glob.glob(args[1]))
        passed = 0
        for p in paths:
            r = analyze(p)
            if r is None:
                print(f"      {p}: 读不到角色")
                continue
            print(fmt(r))
            if r["verdict"] == "A-POSE" and r["corners_white"] and r["margin_min"] > 8:
                passed += 1
        print(f"\n{len(paths)} 张中全部条件通过 {passed} 张")
        return
    for p in args:
        r = analyze(p)
        print(fmt(r) if r else f"{p}: 读不到角色")


if __name__ == "__main__":
    main()
