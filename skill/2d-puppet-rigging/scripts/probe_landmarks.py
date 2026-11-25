#!/usr/bin/env python3
"""单张立绘的 landmark 探针：颜色分类 + 游程扫描 + 候选框占比复核。

用途：切层前用像素事实定坐标（VLM 读不了像素级坐标）。

用法：
  python probe_landmarks.py 图.png \
      --v 300,407,490,620 \
      --h 420,470,520 \
      --box eyeL,340,414,440,492 --box mouth,447,513,513,529

坐标一律是 1100 宽画布像素（与 viewIllustrationGrid 报的 canvas 一致）；
脚本内部会把图缩放到 1100 宽再统计，所以直接喂原图即可。

输出：
  - 每条扫描线的颜色游程（长度 >= 8）
  - 每个候选框的 white / blue / skin / dark 占比（用来接受或否决一个框）
"""
import argparse

import numpy as np
from PIL import Image

BASE_W = 1100
MIN_RUN = 8


def classify(px, bg):
    r, g, b = int(px[0]), int(px[1]), int(px[2])
    mx, mn = max(r, g, b), min(r, g, b)
    if abs(r - bg[0]) < 20 and abs(g - bg[1]) < 20 and abs(b - bg[2]) < 20:
        return "BG"
    if mx < 90:
        return "DARK"
    if mn > 205 and mx - mn < 20:
        return "WHITE"
    if b > r + 40 and b > g + 15:
        return "BLU"
    if r > 195 and g > 165 and b > 140 and r > b + 8 and mx - mn < 75:
        return "SKIN"
    return "OTH"


def print_runs(pixels, coords, bg, label):
    """按类打游程；只打印长度 >= MIN_RUN 的段。"""
    out = []
    cur = None
    start = coords[0]
    n = 0
    for c, px in zip(coords, pixels):
        k = classify(px, bg)
        if k == cur:
            n += 1
            continue
        if cur is not None and n >= MIN_RUN:
            out.append(f"{cur}[{start}-{c - 1}]")
        cur, start, n = k, c, 1
    if cur is not None and n >= MIN_RUN:
        out.append(f"{cur}[{start}-{coords[-1]}]")
    print(f"{label}: " + " ".join(out))


def box_stats(a, bg, name, x0, y0, x1, y1, stride=3):
    sub = a[y0:y1:stride, x0:x1:stride]
    counts = {"WHITE": 0, "BLU": 0, "SKIN": 0, "DARK": 0}
    total = 0
    for px in sub.reshape(-1, 3):
        total += 1
        k = classify(px, bg)
        if k in counts:
            counts[k] += 1
    total = max(1, total)
    print(
        f"{name:10} [{x0},{y0},{x1},{y1}] "
        f"white={counts['WHITE']/total:.2f} blue={counts['BLU']/total:.2f} "
        f"skin={counts['SKIN']/total:.2f} dark={counts['DARK']/total:.2f}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--v", default="", help="竖扫描线的 x，逗号分隔")
    ap.add_argument("--h", default="", help="横扫描线的 y，逗号分隔")
    ap.add_argument("--box", action="append", default=[], help="name,x0,y0,x1,y1")
    args = ap.parse_args()

    im = Image.open(args.image).convert("RGB")
    h = round(im.height * BASE_W / im.width)
    im = im.resize((BASE_W, h), Image.LANCZOS)
    a = np.asarray(im).astype(int)
    bg = a[5, 5]
    print(f"canvas {BASE_W}x{h}  bg={bg.tolist()}")

    for xs in [s for s in args.v.split(",") if s.strip()]:
        x = int(xs)
        print_runs([a[y, x] for y in range(h)], list(range(h)), bg, f"V x={x}")
    for ys in [s for s in args.h.split(",") if s.strip()]:
        y = int(ys)
        print_runs([a[y, x] for x in range(BASE_W)], list(range(BASE_W)), bg, f"H y={y}")

    if args.box:
        print("--- box composition (white / blue / skin / dark) ---")
        for spec in args.box:
            parts = spec.split(",")
            name = parts[0]
            x0, y0, x1, y1 = (int(v) for v in parts[1:5])
            box_stats(a, bg, name, x0, y0, x1, y1)


if __name__ == "__main__":
    main()
