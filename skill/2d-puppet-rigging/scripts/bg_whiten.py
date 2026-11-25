#!/usr/bin/env python3
"""把生成图的背景压到真正纯白 (255,255,255)。

为什么需要：SDXL 的 "pure white background" 实际出来是冷白偏蓝（233,242,252）
或暖米（237,212,188），按 >=250 判定纯白会得 0.2–4% 的荒谬结果。

为什么不能全图阈值压白：白围裙、白袜、眼睛高光也是白的，一刀切会丢细节。
做法：从画布四边 flood fill，**只把与边缘连通的背景**设为纯白；角色内部的白被深色描边包住，不被波及。

阈值经验：
    冷白偏蓝 (231–237, 240–252)     215 / 40
    暖米     (237,212,188)          180 / 60
    更暗暖米 (229,204,178)          175 / 65
    深卡其   (196,170,126)          压不白 —— 别硬压，回去把提示词里的 ivory/beige 换掉

用法:
    python bg_whiten.py <src.png> [dst.png] [thr] [sat]
不传 dst 时输出 <src>_white.png。压完会自动验四角 + 轮廓外 3px 环。
"""
import sys
from collections import deque

import numpy as np
from PIL import Image


def whiten(src, dst=None, thr=205, sat=40):
    a = np.asarray(Image.open(src).convert("RGB")).astype(np.uint8)
    H, W, _ = a.shape
    out = a.copy()

    mx = a.max(axis=2).astype(np.int16)
    mn = a.min(axis=2).astype(np.int16)
    bg_like = (mn >= thr) & ((mx - mn) <= sat)

    visited = np.zeros((H, W), dtype=bool)
    q = deque()
    for x in range(W):
        for y in (0, H - 1):
            if bg_like[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))
    for y in range(H):
        for x in (0, W - 1):
            if bg_like[y, x] and not visited[y, x]:
                visited[y, x] = True
                q.append((y, x))

    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and bg_like[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = True
                q.append((ny, nx))

    out[visited] = 255
    if dst is None:
        dst = src.rsplit(".", 1)[0] + "_white.png"
    Image.fromarray(out).save(dst)
    return visited.mean() * 100, dst


def verify(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    H, W, _ = a.shape
    corners = [
        int(a[y:y + 40, x:x + 40].min())
        for y, x in ((0, 0), (0, W - 40), (H - 40, 0), (H - 40, W - 40))
    ]
    pure = float(((a[:, :, 0] == 255) & (a[:, :, 1] == 255) & (a[:, :, 2] == 255)).mean() * 100)

    # 关键：四角干净不等于边缘干净。米色底压完后常在角色轮廓外留一圈暖晕，
    # 只验四角会把这种情况判成通过。
    body = ~((a[:, :, 0] == 255) & (a[:, :, 1] == 255) & (a[:, :, 2] == 255))
    if body.any():
        from scipy.ndimage import binary_dilation
        ring = binary_dilation(body, iterations=3) & ~body
        r = a[ring]
        warm = float((r[:, 0] - r[:, 2]).mean()) if len(r) else 0.0
    else:
        warm = 0.0

    return {
        "pure_pct": round(pure, 1),
        "corners": corners,
        "corners_white": all(c == 255 for c in corners),
        "ring_warm_bias": round(warm, 1),
    }


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else None
    thr = int(sys.argv[3]) if len(sys.argv) > 3 else 205
    sat = int(sys.argv[4]) if len(sys.argv) > 4 else 40

    pct, path = whiten(src, dst, thr, sat)
    v = verify(path)
    print(f"{path}\n  压白 {pct:.1f}% 画布 | 纯白 {v['pure_pct']}% | 四角最小 {v['corners']} | "
          f"四角全白={v['corners_white']} | 轮廓外3px环偏暖 R-B={v['ring_warm_bias']}")
    if not v["corners_white"]:
        print("  → 四角未全白：换更宽的 thr/sat（215/40 冷白、180/60 暖米、175/65 更暗暖米），"
              "或这本身就是深卡其底，应回去改提示词")
    elif abs(v["ring_warm_bias"]) > 1.0:
        print("  → 轮廓外仍有暖边残留，阈值偏窄，再放宽一档")
