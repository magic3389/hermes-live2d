# -*- coding: utf-8 -*-
"""用抠图 mask 裁出人物，居中放进正方形白底画布（See-through 的标准输入格式）。

为什么必须居中放进正方形：See-through 按 1280 画布推理，输入比例怪会拉伸；
人物贴边会让拆出的零件被画框裁掉。

用法:
    python extract_subject.py --src 原图.png --mask mask.png --out input/subject_1280.png
    python extract_subject.py --src a.png --mask a_mask.png --out b.png --size 1280 --min-blob 3000

--min-blob 是剔除碎片的连通域面积阈值。RMBG 常在背景留零散白点，
太小会当成人物一部分带进来，太大会咬掉发丝末梢——实测 3000px 对 1800px 宽的立绘合适。
"""
import argparse

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

p = argparse.ArgumentParser()
p.add_argument("--src", required=True, help="原始立绘")
p.add_argument("--mask", required=True, help="抠图 mask（白=人物），支持灰度或带 alpha 的图")
p.add_argument("--out", required=True, help="输出路径")
p.add_argument("--size", type=int, default=1280, help="输出正方形边长")
p.add_argument("--pad", type=int, default=20, help="四周留白像素")
p.add_argument("--min-blob", type=int, default=3000, help="小于该面积的连通域视为碎片并剔除")
a = p.parse_args()

src = Image.open(a.src).convert("RGB")
mimg = Image.open(a.mask)
if mimg.mode == "RGBA":
    m = np.asarray(mimg)[:, :, 3]
elif mimg.mode in ("LA", "P"):
    m = np.asarray(mimg.convert("L"))
else:
    m = np.asarray(mimg.convert("L"))
print(f"原图 {src.size}  mask {m.shape}")

fg = m > 128
lbl, n = ndi.label(fg)
sizes = [(int((lbl == i).sum()), i) for i in range(1, n + 1)]
keep_ids = [i for s, i in sizes if s >= a.min_blob]
drop = [(s, i) for s, i in sizes if s < a.min_blob]
print(f"连通域 {n} 个：保留 {len(keep_ids)}（≥{a.min_blob}px），"
      f"剔除 {len(drop)} 块碎片（共 {sum(s for s, _ in drop)}px）")

keep = np.isin(lbl, keep_ids)
if not keep.any():
    print("!! 全被剔除，回退为最大块")
    keep = lbl == max(sizes)[1]

ys, xs = np.nonzero(keep)
x0, x1 = int(xs.min()), int(xs.max()) + 1
y0, y1 = int(ys.min()), int(ys.max()) + 1
print(f"人物 bbox x[{x0},{x1}) y[{y0},{y1})  宽{x1-x0} 高{y1-y0}")

# 软 mask 保留边缘抗锯齿，不要二值化
alpha = np.where(keep, m, 0).astype(np.uint8)
rgba = np.dstack([np.asarray(src), alpha])
person = Image.fromarray(rgba, "RGBA").crop((x0, y0, x1, y1))
print(f"抠出 {person.size}")

H = a.size
h = H - a.pad * 2
w = int(person.width * h / person.height)
if w > H - a.pad * 2:                     # 宽图改成按宽适配，避免超出画布
    w = H - a.pad * 2
    h = int(person.height * w / person.width)
p2 = person.resize((w, h), Image.LANCZOS)
canvas = Image.new("RGB", (H, H), (255, 255, 255))
canvas.paste(p2, ((H - w) // 2, (H - h) // 2), p2)
canvas.save(a.out)
print(f"写出 {a.out}  {canvas.size}  人物 {w}x{h}  偏移 ({(H-w)//2},{(H-h)//2})")

arr = np.asarray(canvas).astype(int)
nb = arr.min(2) < 245
print(f"非白像素 {int(nb.sum())}px ({nb.mean()*100:.1f}%)")
ys2, xs2 = np.nonzero(nb)
print(f"bbox x[{xs2.min()},{xs2.max()}] y[{ys2.min()},{ys2.max()}]")
