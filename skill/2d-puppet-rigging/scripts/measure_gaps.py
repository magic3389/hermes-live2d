#!/usr/bin/env python3
"""量化「变形后新露出的空隙」—— 判据是播放页把背景刷成洋红后再截图。

用法::

    python measure_gaps.py rest.png pose1.png [pose2.png ...]

前提：截图时页面背景 = magenta（洋红），画布 clearColor 透明，所以洋红 = 没有层覆盖。
以 rest（静止）的实心区为基准，pose 里变成洋红的像素就是可动部件移动后露出来的缝。

为什么要腐蚀 rest：头部/身体整体位移会让角色轮廓外缘改变，不腐蚀会把「角色挪了一下」
误算成「新露出的洞」。
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image


def load_solid(path: str) -> np.ndarray:
    """True = 有内容（非洋红），False = 洋红（透底）。"""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    magenta = (a[:, :, 0] > 200) & (a[:, :, 1] < 80) & (a[:, :, 2] > 200)
    return ~magenta


def erode(mask: np.ndarray, r: int = 1) -> np.ndarray:
    """纯 numpy 腐蚀（本机不一定有 scipy）：四方向平移取交。"""
    out = mask.copy()
    for _ in range(r):
        out = (
            out
            & np.roll(out, 1, axis=0)
            & np.roll(out, -1, axis=0)
            & np.roll(out, 1, axis=1)
            & np.roll(out, -1, axis=1)
        )
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2

    rest_path, poses = argv[1], argv[2:]
    rest = load_solid(rest_path)
    inner = erode(rest, 1)
    total = int(inner.sum())
    if total == 0:
        print(f"{rest_path}: 没有实心区，检查背景是否真的是洋红")
        return 1

    print(f"基准 {rest_path}: 实心 {int(rest.sum())} px（腐蚀后 {total} px）")
    print(f"{'pose':<28}{'露底 px':>10}{'占身体':>10}   bbox")
    for p in poses:
        solid = load_solid(p)
        holes = inner & ~solid
        n = int(holes.sum())
        bbox = "-"
        if n:
            ys, xs = np.nonzero(holes)
            bbox = f"({xs.min()},{ys.min()})-({xs.max()},{ys.max()})"
            # 可视化：在原图上把这些像素涂绿
            vis = np.asarray(Image.open(rest_path).convert("RGB"))
            vis[holes] = [0, 255, 0]
            Image.fromarray(vis).save(p.replace(".png", "_holes.png"))
        print(f"{p:<28}{n:>10}{n / total * 100:>9.2f}%   {bbox}")

    print(
        "\n读法：转头 >2% 属明显；把 rigPlan 的 turn.amount / hair.sway 往下压通常能减半，"
        "但 eyes.blink 压太低会让眨眼合不上、反而更差。详见 references/layer-gap-quality.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
