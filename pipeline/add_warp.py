# -*- coding: utf-8 -*-
"""给 iki 模型加 **warp（网格变形）deformer**，替换原来的 matrix 刚体绑定。

为什么必须用 warp：matrix deformer 只能 translate/rotate/scale 整个 part，
布料的弯曲、尾巴的弧形摆动、头发的飘逸全都做不出来 —— 裙子会像木板一样整块转，
尾巴像棍子一样转（一转就露出被遮挡的空洞）。warp 用控制点网格逐点偏移，
底部多偏、顶部不偏，才是布料该有的形变。

格式要点（packages/format/src/types.ts）：
  · grid.points 是 MODEL space，row-major，(cols+1)*(rows+1) 个点
  · rest grid 必须规则轴对齐：col 0 的 x 最小；**row 0 的 y 最大（顶部，+y 向上）**
  · keyforms[].offsets 是逐点 delta，长度与 grid.points 相同，加到 rest grid
  · warp deformer 的 parent 必须是 matrix deformer
  · 子 part 通过 part.deformer 引用它，且 part 必须有 mesh

用法：
    python add_warp.py <in.iki> <out.iki> --target skirt
"""
import json
import sys

import numpy as np


def grid_points(x0, y0, x1, y1, cols, rows):
    """规则轴对齐网格：row 0 在顶部（最大 y），col 0 在左侧。"""
    pts = []
    for r in range(rows + 1):
        y = y1 - (y1 - y0) * r / rows
        for c in range(cols + 1):
            x = x0 + (x1 - x0) * c / cols
            pts += [round(x, 2), round(y, 2)]
    return pts


def bend_offsets(cols, rows, amp_x, curve=1.6, lean=0.0,
                 grid_y_top=None, grid_y_bot=None, bend_y_top=None, bend_y_bot=None):
    """一次「弯曲」的逐点偏移。

    amp_x: 最底行的 x 位移（正=向右）
    curve: 指数，越大则顶部越不动、底部越明显（布料上端钉在腰上）
    lean:  整体 y 方向的抬升/下沉比例（0=纯横向弯）

    grid_y_top / grid_y_bot: 网格覆盖的 model-y 上/下界
    bend_y_top / bend_y_bot: **只在 [bend_y_bot, bend_y_top] 内产生位移**

    为什么需要 bend zone：层的 bbox 往往远大于真正要弯的布料。
    实测一张立绘的 `bottomwear` bbox 是 canvas y[24,719]，但粉色裙子实际只在
    y[500,699]，上半段是混进来的粉领结。网格必须覆盖整个 part（否则上半段
    不参与变形、和下层叠成「一层动一层不动」），但**弯曲只能作用在裙子那一段**——
    否则按整条高度算曲线，裙子只吃到 3–4% 的位移，看起来就是整块平移。
    """
    off = []
    for r in range(rows + 1):
        if grid_y_top is not None and grid_y_bot is not None:
            y = grid_y_top - (grid_y_top - grid_y_bot) * r / rows
        else:
            y = None

        if bend_y_top is not None and y is not None:
            if y > bend_y_top:
                k = 0.0                      # 弯曲区之上：不动
            elif y < bend_y_bot:
                k = 1.0                      # 弯曲区之下：满位移
            else:
                t = (bend_y_top - y) / max(1e-6, bend_y_top - bend_y_bot)
                k = t ** curve
        else:
            k = (r / rows) ** curve

        dx = amp_x * k
        dy = lean * k
        for c in range(cols + 1):
            mid = 1.0 - abs((c / cols) - 0.5) * 2.0     # 中间鼓、两侧收
            off += [round(dx * (0.55 + 0.45 * mid), 2), round(dy * mid, 2)]
    return off


def part_bbox(part):
    """part 在 model space 的包围盒（不含 deformer 形变）。"""
    tw, th = part["width"], part["height"]
    tx, ty = part["transform"]["x"], part["transform"]["y"]
    return (tx - tw / 2, ty - th / 2, tx + tw / 2, ty + th / 2)


def add_skirt_warp(model, top_ratio=1.0, bend_zone=None, canvas_h=1280, amp_override=None):
    """把 bottomwear 从 matrix 刚体换成 warp 网格弯曲。

    top_ratio **必须是 1.0（覆盖整个 part）**。曾用 0.55 只覆盖下摆，
    上半段裙子不在网格里 → 不跟变形 → 和下半段叠加成「一层动一层不动」
    （用户实测：「有一层不动的裙子和动的裙子叠加了」）。
    上端不弯靠 curve 指数实现，不靠缩小网格范围。
    """
    parts = {p["id"]: p for p in model["parts"]}
    bw = parts.get("bottomwear")
    if bw is None:
        raise SystemExit("没有 bottomwear 层")

    x0, y0, x1, y1 = part_bbox(bw)
    # 只取下半部分做网格：上端钉在腰上，下摆才弯
    gy1 = y0 + (y1 - y0) * top_ratio
    # 网格密度决定"切割感"：4x4 每格近 190px，线性插值在格边折出硬棱，
    # 肉眼看就是「切割感」。10x10 每格约 75px，弯曲变成平滑弧线。
    COLS, ROWS = 10, 10

    wid = x1 - x0
    amp = max(10.0, min(34.0, wid * 0.045))     # 摆幅按裙子宽度取，别太夸张
    if amp_override is not None:
        # 按 part 宽度的 4.5% 算常常偏小（一张 492px 宽的裙子只得到 23px，约 4.7%，
        # 用户会读成「整块平移」）。给 --amp 直接指定，经验值：裙子宽度的 8–12%。
        amp = float(amp_override)

    # bend_zone = (canvas_y0, canvas_y1) 给的是**裙子真正所在的画布区间**。
    # 没有它就只能按整条 bbox 算曲线，裙子会被稀释成看不出弯曲。
    by_top = by_bot = None
    if bend_zone:
        cy0, cy1 = bend_zone
        by_top = canvas_h / 2 - cy0          # canvas → model（+y 向上）
        by_bot = canvas_h / 2 - cy1
    print(f"  弯曲区 model y[{by_bot if by_bot is not None else 'N/A'},"
          f"{by_top if by_top is not None else 'N/A'}]（canvas {bend_zone}）")

    # ⚠ keyform 的 value 必须是**参数的 own-range 值**（NOT 归一化），
    # 而且要与参数声明的 min/max 对齐。曾写死 -1/0/1，而 ParamSkirtSway 的
    # 范围是 -8..8 ⇒ 参数只推到 1/8 就到顶，实测位移几乎为 0。
    _pm = {p["id"]: p for p in model.get("parameters", [])}
    _pr = _pm.get("ParamSkirtSway", {"min": -1.0, "max": 1.0})
    _vmin, _vmax = float(_pr.get("min", -1.0)), float(_pr.get("max", 1.0))

    warp = {
        "kind": "warp",
        "id": "def_SkirtWarp",
        "parent": "def_Body",
        "grid": {
            "cols": COLS,
            "rows": ROWS,
            # ⚠ 参数顺序是 (x0, y0, x1, y1)，且 **y1 必须是较大的值（顶部）**：
            # 校验器要求 row 0 的 y 最大（+y 向上）。曾把 gy1/y0 传反导致
            # "grid.points must be a regular axis-aligned grid" 校验失败。
            "points": grid_points(x0, y0, x1, gy1, COLS, ROWS),
        },
        "warps": [{
            "parameter": "ParamSkirtSway",
            "keyforms": [
                {"value": _vmin, "offsets": bend_offsets(COLS, ROWS, -amp, grid_y_top=gy1,
                    grid_y_bot=y0, bend_y_top=by_top, bend_y_bot=by_bot)},
                {"value": 0.0, "offsets": [0.0] * ((COLS + 1) * (ROWS + 1) * 2)},
                {"value": _vmax, "offsets": bend_offsets(COLS, ROWS, amp, grid_y_top=gy1,
                    grid_y_bot=y0, bend_y_top=by_top, bend_y_bot=by_bot)},
            ],
        }],
    }

    # 替换旧的 def_Skirt（matrix）→ 所有引用它的 part 改指 warp
    model["deformers"] = [d for d in model.get("deformers", []) if d["id"] != "def_Skirt"]
    model["deformers"].append(warp)

    switched = []
    for p in model["parts"]:
        if p.get("deformer") == "def_Skirt":
            p["deformer"] = "def_SkirtWarp"
            switched.append(p["id"])

    # apron_top 跟着裙子一起弯（它已经被挂在 def_Skirt 上）
    print(f"  def_Skirt → def_SkirtWarp（{COLS}x{ROWS} 网格 / {(_vmin,_vmax)} 量程 / 摆幅 ±{amp:.0f}px）")
    print(f"  改挂的 part: {switched}")
    return warp


def main(src, dst, targets=("skirt",)):
    model = json.load(open(src, encoding="utf-8"))
    if "skirt" in targets:
        add_skirt_warp(model)
    json.dump(model, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"写出 {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    tgt = ["skirt"]
    if "--target" in sys.argv:
        tgt = sys.argv[sys.argv.index("--target") + 1].split(",")

    # --bend-zone canvas_y0,canvas_y1  裙子真正的画布纵向范围（强烈建议给）
    bz = None
    if "--bend-zone" in sys.argv:
        bz = tuple(int(v) for v in sys.argv[sys.argv.index("--bend-zone") + 1].split(","))
        assert len(bz) == 2, "--bend-zone 需要 canvas_y0,canvas_y1"

    ch = 1280
    if "--canvas-h" in sys.argv:
        ch = int(sys.argv[sys.argv.index("--canvas-h") + 1])

    amp = None
    if "--amp" in sys.argv:
        amp = float(sys.argv[sys.argv.index("--amp") + 1])

    model = json.load(open(sys.argv[1], encoding="utf-8"))
    if "skirt" in tgt:
        add_skirt_warp(model, bend_zone=bz, canvas_h=ch, amp_override=amp)
    json.dump(model, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
    print(f"写出 {sys.argv[2]}")
