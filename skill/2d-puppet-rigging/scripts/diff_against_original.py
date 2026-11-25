#!/usr/bin/env python
"""把 .iki 的静止渲染和原图做 1:1 像素对比，定位层级 / 遮挡 / 错位问题。

截图侧（browser_exec, local=True）——尺寸必须是模型画布，否则放大 diff 会造假像素::

    cdp("Page.bringToFront"); cdp("Emulation.setFocusEmulationEnabled", enabled=True)
    cdp("Emulation.setDeviceMetricsOverride", width=1290, height=1290,
        deviceScaleFactor=1, mobile=False)
    cdp("Page.navigate", url="http://127.0.0.1:5200/standalone.html?nodrive=1")
    # ?nodrive=1 关掉外部驱动与 motion.update()，参数才停得住
    js("(() => { const m=window.model; (m.parameters||[]).forEach(p=>
        window.player.setParameter(p.id, p.default ?? 0)); })()")
    assert js("document.getElementById('c').width") == 1280
    box = js("(() => { const c=document.getElementById('c'); const r=c.getBoundingClientRect();
        return [r.x, r.y, r.width, r.height]; })()")
    res = cdp("Page.captureScreenshot", format="png",
              clip={"x": box[0], "y": box[1], "width": box[2], "height": box[3], "scale": 1})

用法::

    python diff_against_original.py --orig input/x_square1280.png \\
        --shot out/compare/model_native.png --out out/compare
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 这份 asset 的分区表；换角色时用 --regions 传自己的 JSON{"name":[x0,y0,x1,y1]}
DEFAULT_REGIONS = {
    "头顶/呆毛": (480, 30, 830, 200),
    "耳朵区": (370, 110, 940, 390),
    "脸/五官": (530, 140, 790, 440),
    "前发": (481, 38, 826, 487),
    "后发": (322, 150, 907, 685),
    "围裙上胸": (562, 422, 806, 821),
    "袖子": (381, 437, 877, 683),
    "手": (316, 435, 943, 692),
    "裙": (403, 587, 998, 906),
    "腿": (570, 757, 772, 1157),
    "鞋": (551, 991, 714, 1197),
    "尾巴": (155, 682, 632, 968),
}


def load_pair(orig_path: str, shot_path: str):
    o_img = Image.open(orig_path).convert("RGB")
    s_img = Image.open(shot_path).convert("RGB")
    if o_img.size != s_img.size:
        raise SystemExit(
            f"尺寸不一致 {o_img.size} vs {s_img.size} —— 不要缩放后再比，"
            "回去让 canvas.width 等于模型画布再截"
        )
    return o_img, s_img, np.asarray(o_img).astype(int), np.asarray(s_img).astype(int)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", required=True, help="原图（白底）")
    ap.add_argument("--shot", required=True, help="模型静止截图（canvas 原生尺寸）")
    ap.add_argument("--out", default="out/compare")
    ap.add_argument("--thr", type=int, default=25, help="判差异的阈值")
    ap.add_argument("--regions", help="分区表 JSON")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    o_img, s_img, o, s = load_pair(args.orig, args.shot)

    # 原图白底 → 非白即角色；模型黑底 → 非黑即角色
    om = o.min(2) < 245
    sm = s.max(2) > 35
    both = om & sm
    d = np.abs(o - s).max(2)

    print(f"尺寸 {o.shape[:2]}")
    print(f"原图角色 {int(om.sum())}px / 模型角色 {int(sm.sum())}px / 共同 {int(both.sum())}px")
    print(f"原图有模型没有（丢件/被盖）: {int((om & ~sm).sum())}px")
    print(f"模型有原图没有（多件/错位）: {int((sm & ~om).sum())}px")
    print(f"共同区 平均差 {d[both].mean():.2f}  >{args.thr} 的 {int(((d > args.thr) & both).sum())}px")

    regions = DEFAULT_REGIONS
    if args.regions:
        raw = json.load(open(args.regions, encoding="utf-8"))
        regions = {k: tuple(v) for k, v in raw.items()}

    bad = (d > args.thr) & both
    miss = om & ~sm
    extra = sm & ~om
    print()
    print(f"{'部位':10} {'差异':>8} {'占比':>7} {'丢件':>7} {'多件':>7}")
    for name, (x0, y0, x1, y1) in regions.items():
        tot = both[y0:y1, x0:x1].sum()
        print(
            f"{name:10} {int(bad[y0:y1, x0:x1].sum()):8} "
            f"{bad[y0:y1, x0:x1].sum() / max(tot, 1) * 100:6.1f}% "
            f"{int(miss[y0:y1, x0:x1].sum()):7} {int(extra[y0:y1, x0:x1].sum()):7}"
        )

    # 三色图：红=错 蓝=丢件 黄=多件
    h, w = d.shape
    heat = np.zeros((h, w, 3), np.uint8)
    heat[both] = [35, 35, 35]
    heat[bad] = [255, 50, 50]
    heat[miss] = [60, 160, 255]
    heat[extra] = [255, 200, 0]
    hp = os.path.join(args.out, "diff_three_color.png")
    Image.fromarray(heat).save(hp)

    f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", max(16, w // 58))
    cv = Image.new("RGB", (w * 3 + 20, h + 34), (250, 250, 252))
    dr = ImageDraw.Draw(cv)
    cv.paste(o_img, (5, 30))
    dr.text((5, 4), "原图", fill=(0, 0, 0), font=f)
    cv.paste(s_img, (w + 10, 30))
    dr.text((w + 10, 4), "模型（静止）", fill=(0, 0, 0), font=f)
    cv.paste(Image.open(hp), (w * 2 + 15, 30))
    dr.text((w * 2 + 15, 4), "红=错 蓝=丢件 黄=多件", fill=(0, 0, 0), font=f)
    sp = os.path.join(args.out, "side_by_side.png")
    scale = 3 if w > 1000 else 1
    cv.resize((cv.width // scale, cv.height // scale), Image.LANCZOS).save(sp)
    print(f"\n写出 {hp}\n写出 {sp}")


if __name__ == "__main__":
    main()
