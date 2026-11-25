#!/usr/bin/env python3
"""量「某部件到底动没动」：对连拍帧序列算按区域的平均运动量，并出热图。

为什么需要它：缩略拼图上看不出几像素的位移，问 VLM 会得到「没动」的错结论；
分区像素差能把「轻微摆动」和「真的静止」分开，而且能直接指出是哪一层没物理。

用法：
    python measure_motion.py <帧目录> [区域预设]
    python measure_motion.py seq/ --preset qversion --heat heat.png --gif out.gif

区域预设用相对比例（0..1）定义，自动按帧尺寸换算，不绑定具体分辨率。
阈值经验：均值 <0.5 = 真没动（去查那层的 alpha / 物理）；1–4 = 轻微跟随；>8 = 明显。

默认预设按 1280×1280 方图、正面站姿的 Q 版立绘切开，换构图时自己改 REGIONS。
"""
import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# (x0, y0, x1, y1) 用 0..1 相对比例，按帧尺寸换算
REGIONS = {
    "qversion": {
        "头部(呆毛/耳/前发)": (0.00, 0.00, 1.00, 0.47),
        "后发": (0.16, 0.16, 0.87, 0.69),
        "尾巴区": (0.06, 0.47, 0.56, 0.85),
        "裙/腿": (0.16, 0.61, 0.87, 1.00),
        "手/袖": (0.00, 0.32, 1.00, 0.76),
    }
}


def load_frames(pattern):
    files = sorted(glob.glob(pattern))
    if len(files) < 2:
        sys.exit(f"帧数不足：{pattern} 只匹配到 {len(files)} 个")
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames", help="帧 glob，例如 'seq/f*.png' 或目录（自动补 f*.png）")
    ap.add_argument("--preset", default="qversion", choices=sorted(REGIONS))
    ap.add_argument("--heat", help="输出运动热图路径")
    ap.add_argument("--gif", help="同时输出 gif")
    ap.add_argument("--gif-size", type=int, default=420)
    ap.add_argument("--delay", type=int, default=120, help="gif 帧间隔 ms")
    args = ap.parse_args()

    pattern = args.frames
    if os.path.isdir(pattern):
        pattern = os.path.join(pattern, "f*.png")
    files = load_frames(pattern)

    base = np.asarray(Image.open(files[0]).convert("L")).astype(int)
    h, w = base.shape
    acc = np.zeros_like(base, dtype=float)
    for p in files[1:]:
        acc += np.abs(np.asarray(Image.open(p).convert("L")).astype(int) - base)
    acc /= len(files) - 1

    print(f"帧数 {len(files)}  画布 {w}x{h}  峰值 {acc.max():.1f}  全图均值 {acc.mean():.2f}")
    worst = []
    for name, (x0, y0, x1, y1) in REGIONS[args.preset].items():
        box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
        sub = acc[box[1]:box[3], box[0]:box[2]]
        m, peak = sub.mean(), sub.max()
        tag = "静止?" if m < 0.5 else ("轻微" if m < 4 else "明显")
        print(f"  {name:16} 平均 {m:6.2f}  峰值 {peak:6.1f}   {tag}")
        if m < 0.5:
            worst.append(name)

    if worst:
        print("\n均值 <0.5 的区域：%s" % "、".join(worst))
        print("先查这几点，别先调参数：① PSD 层 alpha 是否丢失（每层覆盖 100% 就是坏了）"
              "② 该层有没有被识别成揺れ（诊断里层名后面是「揺れ」还是「跟随」）"
              "③ 驱动是否持续（idle 每帧覆盖，必须 16ms setInterval 反复写）")

    if args.heat:
        im = Image.open(files[0]).convert("RGB")
        ov = np.asarray(im).astype(float)
        heat = np.clip(acc / max(acc.max(), 1e-6), 0, 1)
        ov[:, :, 0] = np.clip(ov[:, :, 0] + heat * 215, 0, 255)
        ov[:, :, 1] = np.clip(ov[:, :, 1] - heat * 105, 0, 255)
        ov[:, :, 2] = np.clip(ov[:, :, 2] - heat * 105, 0, 255)
        Image.fromarray(ov.astype(np.uint8)).save(args.heat)
        print(f"热图 → {args.heat}")

    if args.gif:
        frames = [Image.open(p).convert("RGB").resize((args.gif_size, args.gif_size), Image.LANCZOS)
                  for p in files]
        frames[0].save(args.gif, save_all=True, append_images=frames[1:],
                       duration=args.delay, loop=0, optimize=True)
        print(f"gif → {args.gif} ({os.path.getsize(args.gif) // 1024} KB)")


if __name__ == "__main__":
    main()
