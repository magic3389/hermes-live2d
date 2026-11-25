"""放大角色脸部 / 嘴部，用于逐张验收表情。

嘴只占整图千分之几，缩略图里看不出来 —— 批量交付前不逐张放大，用户一眼就能抓到「嘴还是在吃饭」。
不需要 VLM、不需要人脸模型：按「非纯白」求角色 bbox → 按 Q 版头身比截头区 → 放大拼条。

用法:
  python zoom_face.py out/g01.png out/g04.png --mode mouth --out mouth.png
  python zoom_face.py "out/g*.png" --mode face  --out faces.png

参数:
  --mode mouth  头区下半截（眼睛以下到下巴），看嘴型
  --mode face   整个头区，看发型与配饰
  --width N     每个图块放大后的宽（默认 520）
  --out PATH    输出拼图路径

判定口径: 闭合弧线 = 合格；能看到口腔内部的暗色块 = 张嘴，退回。
"""
import argparse
import glob
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 已标定的比例（相对角色 bbox 高度）
HEAD_BAND = 0.42      # Q 版头部约占角色高的 42%
FACE_HALF = 0.42      # face 模式半宽
MOUTH_HALF = 0.24     # mouth 模式半宽
MOUTH_TOP = 0.42      # mouth 模式起点（头区内比例）
MOUTH_BOT = 1.02
WHITE = 250           # >= 该值视为白，用于求角色 bbox


def body_bbox(im):
    a = np.asarray(im.convert("RGB")).astype(int)
    m = ~((a[:, :, 0] >= WHITE) & (a[:, :, 1] >= WHITE) & (a[:, :, 2] >= WHITE))
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        raise SystemExit("整张都是白的，读不到角色")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def crop(im, mode):
    W, H = im.size
    x0, y0, x1, y1 = body_bbox(im)
    bh = y1 - y0
    cx = (x0 + x1) // 2
    head_h = bh * HEAD_BAND
    if mode == "mouth":
        ty, by = y0 + head_h * MOUTH_TOP, y0 + head_h * MOUTH_BOT
        hw = bh * MOUTH_HALF
    else:
        ty, by = y0 - bh * 0.02, y0 + head_h
        hw = bh * FACE_HALF
    box = (max(0, int(cx - hw)), max(0, int(ty)), min(W, int(cx + hw)), min(H, int(by)))
    return im.crop(box), box


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+", help="图片路径或 glob")
    ap.add_argument("--mode", choices=["mouth", "face"], default="mouth")
    ap.add_argument("--width", type=int, default=520)
    ap.add_argument("--out", default="zoom_out.png")
    ap.add_argument("--label", default=True, action=argparse.BooleanOptionalAction)
    args = ap.parse_args()

    paths = []
    for p in args.images:
        paths.extend(sorted(glob.glob(p)) or ([p] if os.path.exists(p) else []))
    if not paths:
        raise SystemExit("没找到任何图片")

    tiles = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        c, box = crop(im, args.mode)
        sc = args.width / c.width
        c = c.resize((args.width, max(1, int(c.height * sc))), Image.LANCZOS)
        tiles.append((os.path.basename(p), c))
        print(f"{os.path.basename(p):28} src={im.size} crop={box} -> {c.size}")

    th = max(t[1].height for t in tiles)
    pad = 40 if args.label else 0
    out = Image.new("RGB", (args.width * len(tiles) + 10 * len(tiles), th + pad), (240, 242, 245))
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
    except OSError:
        font = None
    for i, (name, c) in enumerate(tiles):
        out.paste(c, (i * (args.width + 10) + 5, pad))
        if args.label and font:
            d.text((i * (args.width + 10) + 8, 8), name, fill=(0, 0, 0), font=font)
    out.save(args.out)
    print("拼图:", out.size, "->", args.out)


if __name__ == "__main__":
    main()
