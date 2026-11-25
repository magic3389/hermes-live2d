#!/usr/bin/env python3
"""量「转动时有没有露出皮肤 / 底层」—— 判拆层开缝的硬指标。

背景：把一整片头发切成几块、各挂不同枢轴时，静态合成和原图一模一样（所以合成图查不出问题），
一转起来切口错开，露出的是**下面的脸（肤色）**而不是透明背景，于是「透明底量缝」的脚本
（measure_gaps.py）也查不出来。用户看到的就是「前发和侧面开缝，露出额头底层」。

本脚本：在指定 bbox 内数肤色像素，逐帧与静止帧比。
  - 任何一帧显著高于静止帧 → 该处开缝露底
  - 数字基本持平或更低（转起来遮得更多）→ 没开缝

用法：
    python measure_underlayer_leak.py <帧目录或glob> [--bbox x,y,w,h] [--canvas 1280] [--json]

--bbox 用**设计画布坐标**（例：前发 481,38,826,487，画布 1280）。省略则查整个角色 bbox
（只能发现大面积漏底，小缝会被稀释，判头发请务必给 --bbox）。

输出：每帧肤色像素数 + 相对静止帧的最大增加值 + 判定。
退出码：0 = 无露底；1 = 有露底。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
from PIL import Image


def load_frames(pattern: str) -> list[str]:
    if os.path.isdir(pattern):
        files = sorted(glob.glob(os.path.join(pattern, "*.png")))
    else:
        files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"没有匹配到帧: {pattern}")
    return files


def character_bbox(img: Image.Image, thresh: int = 30) -> tuple[int, int, int, int]:
    """画面里非纯黑区域的外接框（用首帧估角色范围）。"""
    a = np.asarray(img.convert("RGB")).astype(int)
    mask = a.max(axis=2) > thresh
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        sys.exit("画面全黑，找不到角色")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def skin_mask(arr: np.ndarray) -> np.ndarray:
    """二次元肤色：偏亮、R>G>B、低饱和偏暖。阈值按渲染图实测标定。"""
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return ((r > 195) & (g > 160) & (b > 140)
            & (r - b > 18) & (r - b < 95) & (g - b > 5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", help="帧目录或 glob")
    ap.add_argument("--bbox", help="设计坐标 x,y,w,h（例：481,38,826,487 表示 x0,y0,x1,y1 也可）")
    ap.add_argument("--canvas", type=int, default=1280, help="设计画布边长，默认 1280")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    files = load_frames(args.pattern)
    first = Image.open(files[0]).convert("RGB")
    cx0, cy0, cx1, cy1 = character_bbox(first)

    if args.bbox:
        parts = [float(t) for t in args.bbox.split(",")]
        if len(parts) != 4:
            sys.exit("--bbox 需要 4 个数 x0,y0,x1,y1")
        # 设计坐标 → 截图像素：用角色 bbox 做仿射基准
        k = (cx1 - cx0) / args.canvas
        bx0 = int(cx0 + parts[0] * k)
        by0 = int(cy0 + parts[1] * k)
        bx1 = int(cx0 + parts[2] * k)
        by1 = int(cy0 + parts[3] * k)
        label = f"bbox(设计 {parts}) → 像素 {bx0},{by0},{bx1},{by1}"
    else:
        bx0, by0, bx1, by1 = cx0, cy0, cx1, cy1
        label = f"全角色 bbox {bx0},{by0},{bx1},{by1}"

    counts = []
    for p in files:
        arr = np.asarray(Image.open(p).convert("RGB")).astype(int)[by0:by1, bx0:bx1]
        counts.append(int(skin_mask(arr).sum()))

    base = counts[0]
    peak = max(counts)
    delta = peak - base
    # 允许 0.5% 抖动（抗锯齿 / 轻微遮挡变化），超过即判开缝
    tolerance = max(4, int(base * 0.005))
    leak = delta > tolerance

    result = {
        "frame_count": len(files),
        "region": label,
        "base_frame0": base,
        "min": min(counts),
        "max": peak,
        "median": int(np.median(counts)),
        "peak_delta_vs_frame0": delta,
        "tolerance": tolerance,
        "verdict": "LEAK（露底层）" if leak else "OK（无缝）",
        "counts": counts,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"帧数 {len(files)}  区域 {label}")
        print(f"  静止帧 = {base}")
        print(f"  最小 {min(counts)} / 中位 {result['median']} / 最大 {peak}")
        print(f"  相对静止帧最大增加 {delta} px（容差 {tolerance}）")
        print(f"  判定：{result['verdict']}")

    return 1 if leak else 0


if __name__ == "__main__":
    raise SystemExit(main())
