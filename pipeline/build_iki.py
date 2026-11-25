"""通用 PSD → Iki 绑定器：枢轴全按 bbox 自动算，不写死任何素材的像素坐标。

用法：
    python build_iki_generic.py <输入.psd> <输出.iki> [模型名]

与 build_iki2.py 的区别：
  · ORDER 按语义表自动排（表外层按 alpha 面积降序补在末尾）
  · PIVOT 全部由各层 bbox 推导（发根/耳内端/裙腰/腿根/尾根）
  · 层名归一化（下划线/空格、大小写、单复数都吃掉）
  · 缺层只是警告，不中断

格式依据 packages/format/src/types.ts：
  IkiPart.deformer  → 只能挂一个 deformer
  IkiMatrixDeformer → {kind:"matrix", id, parent?, pivot:{x,y}, bindings:[{parameter,channel,from,to}]}
                      pivot 用 model 空间（原点=画布中心，+y 向上）
  IkiPhysics        → 单弹簧，output.parameter 收 scale*(x - 目标)
"""
import base64
import io
import json
import os
import re
import sys

import numpy as np
from PIL import Image
from psd_tools import PSDImage

PAD = 2
ATLAS_MAX_W = 1400
GRID = 6

# ---------- 层名归一化 ----------
_ALIAS = {
    "back_hair": "back hair", "backhair": "back hair", "hair_back": "back hair",
    "front_hair": "front hair", "fronthair": "front hair", "hair_front": "front hair",
    "head_wear": "headwear", "head_accessory": "headwear", "headdress": "headwear",
    "ear": "ears", "ear_l": "ear_L", "ear_r": "ear_R",
    "eye_white": "eyewhite", "sclera": "eyewhite",
    "iris": "irides", "eyelashes": "eyelash",
    "top": "topwear", "shirt": "topwear", "upper_wear": "topwear",
    "bottom": "bottomwear", "skirt": "bottomwear", "lower_wear": "bottomwear",
    "apron": "apron_top",
    "hand": "handwear", "sleeve": "handwear", "arms": "handwear",
    "legwear": "legs", "footwear": "legs", "leg": "legs", "legs_l": "legs",
    "neckline": "neck",
    "eyebrows": "eyebrow", "mouths": "mouth",
}


def norm(name: str) -> str:
    k = re.sub(r"[\s\-]+", "_", name.strip().lower())
    # leg_L / leg_R / ear_L / ear_R 这类**左右成对**的层必须保住下划线，
    # 否则归一化会变成 "leg l"，匹配不到 deformer 表（实测腿整个没有动画）。
    _pair = {"leg_l": "leg_L", "leg_r": "leg_R", "ear_l": "ear_L", "ear_r": "ear_R",
             "arm_l": "arm_L", "arm_r": "arm_R", "eye_l": "eye_L", "eye_r": "eye_R",
             "hand_l": "hand_L", "hand_r": "hand_R"}
    if k in _pair:
        return _pair[k]
    if k in _ALIAS:
        return _ALIAS[k]
    return k.replace("_", " ")


# ---------- 画序（低→高）：后面（远）先画，前面（近）后画 ----------
ORDER = [
    "backdrop",          # 兜底层：See-through 漏掉的内容，必须画在所有人下面
    "back hair", "tail",
    "leg_L", "leg_R", "legs",
    "neck", "bottomwear", "handwear", "apron_top", "topwear",
    "headwear", "ear_L", "ear_R",
    "face", "nose", "mouth", "eyebrow", "eyewhite", "irides", "eyelash",
    "front hair",
]

# 挂哪个 deformer（表外的：脸类挂 Head，其余挂 Body）
PART_DEF = {
    "back hair": "def_BackHair",
    "front hair": "def_FrontHair",
    "ear_L": "def_EarL",
    "ear_R": "def_EarR",
    "bottomwear": "def_Skirt",
    "apron_top": "def_Skirt",
    "leg_L": "def_LegL",
    "leg_R": "def_LegR",
    "tail": "def_Tail",
}
HEAD_PARTS = {"face", "nose", "mouth", "eyebrow", "eyewhite", "irides", "eyelash", "headwear"}


def crop_to_alpha(im, soft=8):
    a = np.asarray(im)[:, :, 3]
    core = a > 128
    if core.sum() == 0:
        return None
    ys, xs = np.nonzero(core)
    x0 = max(0, int(xs.min()) - soft)
    y0 = max(0, int(ys.min()) - soft)
    x1 = min(im.width, int(xs.max()) + 1 + soft)
    y1 = min(im.height, int(ys.max()) + 1 + soft)
    return im.crop((x0, y0, x1, y1)), (x0, y0, x1, y1)


def split_ears(im):
    """耳朵按连通域分离成左右两组（不用 x 坐标切，蝴蝶结之类小件也要归位）。"""
    from scipy import ndimage as _nd
    arr = np.asarray(im)
    m = arr[:, :, 3] > 30
    lbl, n = _nd.label(m)
    sizes = [(int((lbl == i).sum()), i) for i in range(1, n + 1)]
    sizes = [t for t in sizes if t[0] > 80]
    if len(sizes) < 2:
        return {}
    ids = [i for _, i in sizes]
    cents = _nd.center_of_mass(m, lbl, ids)
    xs = sorted(c[1] for c in cents)
    midx = (xs[0] + xs[-1]) / 2.0
    groups = {
        "ear_L": [i for i, c in zip(ids, cents) if c[1] < midx],
        "ear_R": [i for i, c in zip(ids, cents) if c[1] >= midx],
    }
    out = {}
    for tag, gid in groups.items():
        if not gid:
            continue
        sel = np.isin(lbl, gid) & m
        sub = arr.copy()
        sub[:, :, 3] = np.where(sel, arr[:, :, 3], 0).astype(np.uint8)
        r = crop_to_alpha(Image.fromarray(sub, "RGBA"))
        if r:
            out[tag] = r
    return out


def build(src_psd, out_iki, model_name="立绘傀儡模型"):
    psd = PSDImage.open(src_psd)
    W, H = psd.width, psd.height
    raw = {}
    for l in psd.descendants():
        if l.is_group():
            continue
        im = l.topil()
        if im is None:
            continue
        raw[norm(l.name)] = im.convert("RGBA")
    print(f"PSD {W}x{H}  层 {len(raw)}: {sorted(raw)}")

    # ---------- 1) 每块图 ----------
    blocks = {}
    for name, im in raw.items():
        if name == "ears":
            got = split_ears(im)
            if got:
                blocks.update(got)
                print(f"  耳朵连通域分离: {sorted(got)}")
            else:
                r = crop_to_alpha(im)
                if r:
                    blocks["ear_L"] = r
                print("  !! 耳朵只有一块，记为 ear_L")
        elif name in ("legs", "leg_L", "leg_R"):
            r = crop_to_alpha(im)
            if r:
                blocks[name] = r
        else:
            r = crop_to_alpha(im)
            if r:
                blocks[name] = r

    # 层表外的层按面积降序追加（保证不漏）
    extra = [n for n in blocks if n not in ORDER]
    extra.sort(key=lambda n: -int((np.asarray(blocks[n][0])[:, :, 3] > 0).sum()))
    order = [n for n in ORDER if n in blocks] + extra
    if extra:
        print(f"  表外层（按面积降序补末尾）: {extra}")

    for n, (im, bb) in blocks.items():
        print(f"  {n:14} bbox={bb}  {im.width}x{im.height}")

    # ---------- 2) atlas ----------
    items = [(n, blocks[n][0].size) for n in order]
    items.sort(key=lambda t: -t[1][1])
    shelves, cur = [], {"y": PAD, "h": 0, "items": [], "w": PAD}
    for name, (w, h) in items:
        if cur["w"] + w + PAD > ATLAS_MAX_W and cur["items"]:
            shelves.append(cur)
            cur = {"y": cur["y"] + cur["h"] + PAD, "h": 0, "items": [], "w": PAD}
        cur["items"].append((name, cur["w"], cur["y"], w, h))
        cur["w"] += w + PAD
        cur["h"] = max(cur["h"], h)
    shelves.append(cur)
    atlas_w = max(s["w"] for s in shelves)
    atlas_h = shelves[-1]["y"] + shelves[-1]["h"] + PAD
    atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
    place = {}
    for s in shelves:
        for name, x, y, w, h in s["items"]:
            atlas.paste(blocks[name][0], (x, y))
            place[name] = (x, y, w, h)
    buf = io.BytesIO()
    atlas.save(buf, format="PNG", optimize=True)
    data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    print(f"\natlas {atlas_w}x{atlas_h}  base64 {len(data_uri)/1048576:.2f} MB")

    # ---------- 3) 参数 ----------
    params = [
        {"id": "ParamAngleX", "name": "Head Angle X", "min": -30, "max": 30, "default": 0},
        {"id": "ParamAngleY", "name": "Head Angle Y", "min": -30, "max": 30, "default": 0},
        {"id": "ParamAngleZ", "name": "Head Angle Z", "min": -30, "max": 30, "default": 0},
        {"id": "ParamBodyAngleX", "name": "Body Angle X", "min": -10, "max": 10, "default": 0},
        {"id": "ParamBodyAngleZ", "name": "Body Angle Z", "min": -10, "max": 10, "default": 0},
        {"id": "ParamEyeLOpen", "name": "Eye L Open", "min": 0, "max": 1, "default": 1},
        {"id": "ParamEyeROpen", "name": "Eye R Open", "min": 0, "max": 1, "default": 1},
        {"id": "ParamEyeBallX", "name": "Gaze X", "min": -1, "max": 1, "default": 0},
        {"id": "ParamEyeBallY", "name": "Gaze Y", "min": -1, "max": 1, "default": 0},
        {"id": "ParamMouthOpenY", "name": "Mouth Open", "min": 0, "max": 1, "default": 0},
        {"id": "ParamMouthForm", "name": "Mouth Form", "min": -1, "max": 1, "default": 0},
        {"id": "ParamBreath", "name": "Breath", "min": 0, "max": 1, "default": 0},
        {"id": "ParamAmbientSway", "name": "Ambient Sway", "min": -1, "max": 1, "default": 0},
        {"id": "ParamHairFrontSway", "name": "Front Hair Sway", "min": -18, "max": 18, "default": 0},
        {"id": "ParamHairBackSway", "name": "Back Hair Sway", "min": -14, "max": 14, "default": 0},
        {"id": "ParamEarSwayL", "name": "Ear Sway L", "min": -14, "max": 14, "default": 0},
        {"id": "ParamEarSwayR", "name": "Ear Sway R", "min": -14, "max": 14, "default": 0},
        {"id": "ParamSkirtSway", "name": "Skirt Sway", "min": -8, "max": 8, "default": 0},
        {"id": "ParamLegSwayL", "name": "Leg Sway L", "min": -8, "max": 8, "default": 0},
        {"id": "ParamLegSwayR", "name": "Leg Sway R", "min": -8, "max": 8, "default": 0},
        {"id": "ParamTailSway", "name": "Tail Sway", "min": -30, "max": 30, "default": 0},
    ]

    # ---------- 4) 坐标辅助 ----------
    def to_model(bbox):
        x0, y0, x1, y1 = bbox
        return (x0 + x1) / 2 - W / 2, H / 2 - (y0 + y1) / 2, x1 - x0, y1 - y0

    def px2mx(px):
        return px - W / 2

    def px2my(py):
        return H / 2 - py

    def grid_mesh(cols, rows, uv):
        verts, uvs, idx = [], [], []
        for r in range(rows + 1):
            v = r / rows
            for c in range(cols + 1):
                u = c / cols
                verts += [u - 0.5, 0.5 - v]
                uvs += [uv["x"] + u * uv["width"], uv["y"] + v * uv["height"]]
        stride = cols + 1
        for r in range(rows):
            for c in range(cols):
                a = r * stride + c
                b, d = a + 1, a + stride
                e = d + 1
                idx += [a, d, b, b, d, e]
        return {"vertices": verts, "uvs": uvs, "indices": idx}

    # ---------- 5) 枢轴：全部由 bbox 推导 ----------
    def bb(name):
        return blocks[name][1] if name in blocks else None

    def pivot_top_center(name):
        """发根/裙腰/腿根/尾根：bbox 顶部中心"""
        b = bb(name)
        return (px2mx((b[0] + b[2]) / 2), px2my(b[1]))

    def pivot_ear(name, inner_side):
        """耳朵：贴着头那一侧（ear_L 在画面左，内端=右端；ear_R 内端=左端），高度取 bbox 上沿"""
        b = bb(name)
        x = b[2] if inner_side == "right" else b[0]
        return (px2mx(x), px2my(b[1]))

    PIVOT = {}
    if "back hair" in blocks:
        PIVOT["back hair"] = pivot_top_center("back hair")
    if "front hair" in blocks:
        PIVOT["front hair"] = pivot_top_center("front hair")
    if "ear_L" in blocks:
        PIVOT["ear_L"] = pivot_ear("ear_L", "right")
    if "ear_R" in blocks:
        PIVOT["ear_R"] = pivot_ear("ear_R", "left")
    if "bottomwear" in blocks:
        PIVOT["bottomwear"] = pivot_top_center("bottomwear")
    if "tail" in blocks:
        PIVOT["tail"] = pivot_top_center("tail")
    for lg in ("leg_L", "leg_R"):
        if lg in blocks:
            PIVOT[lg] = pivot_top_center(lg)

    print("\n自动枢轴（model 空间）:")
    for k, v in PIVOT.items():
        print(f"  {k:12} ({v[0]:7.1f}, {v[1]:7.1f})")

    # ---------- 6) parts ----------
    parts = []
    for i, name in enumerate(order):
        ax, ay, aw, ah = place[name]
        mx, my, mw, mh = to_model(blocks[name][1])
        uv = {"x": ax / atlas_w, "y": ay / atlas_h,
              "width": aw / atlas_w, "height": ah / atlas_h}
        p = {
            "id": name.replace(" ", "_"),
            "color": [1, 1, 1, 1],
            "width": float(mw),
            "height": float(mh),
            "transform": {"x": mx, "y": my},
            "order": i,
            "texture": {"index": 0, "uv": uv},
            "mesh": grid_mesh(GRID, GRID, uv),
        }
        if name in PART_DEF and name in PIVOT:
            p["deformer"] = PART_DEF[name]
        elif name in HEAD_PARTS:
            p["deformer"] = "def_Head"
        else:
            p["deformer"] = "def_Body"
        parts.append(p)

    # ---------- 7) deformers ----------
    def mat(did, pivot, param=None, rng=None, parent=None, flip=False, channel="rotate"):
        d = {"kind": "matrix", "id": did, "pivot": {"x": pivot[0], "y": pivot[1]}}
        if parent:
            d["parent"] = parent
        if param:
            f, t = -rng, rng
            if flip:      # 镜像枢轴 + 同向旋转 = 跷跷板，右件必须反号
                f, t = t, f
            d["bindings"] = [{"parameter": param, "channel": channel, "from": f, "to": t}]
        return d

    # head/body 两级总成：没有它们，转头时只有绑了角度的件在动，脸会被切开
    neck_y = bb("neck")[3] if "neck" in blocks else (bb("face")[3] if "face" in blocks else H * 0.35)
    body_y = bb("bottomwear")[1] if "bottomwear" in blocks else H * 0.65
    body_x = (bb("bottomwear")[0] + bb("bottomwear")[2]) / 2 if "bottomwear" in blocks else W / 2

    deformers = [
        {"kind": "matrix", "id": "def_Head",
         "pivot": {"x": px2mx(body_x if "bottomwear" not in blocks else (bb("neck")[0] + bb("neck")[2]) / 2
                              if "neck" in blocks else body_x), "y": px2my(neck_y)},
         "bindings": [
             {"parameter": "ParamAngleX", "channel": "rotate", "from": -11, "to": 11},
             {"parameter": "ParamAngleY", "channel": "translateY", "from": -10, "to": 10},
             {"parameter": "ParamAngleZ", "channel": "rotate", "from": -9, "to": 9},
         ]},
        {"kind": "matrix", "id": "def_Body",
         "pivot": {"x": px2mx(body_x), "y": px2my(body_y)},
         "bindings": [
             {"parameter": "ParamBodyAngleX", "channel": "translateX", "from": -16, "to": 16},
             {"parameter": "ParamBodyAngleZ", "channel": "rotate", "from": -4, "to": 4},
         ]},
    ]
    if "tail" in PIVOT:
        deformers.append(mat("def_Tail", PIVOT["tail"], "ParamTailSway", 24, parent="def_Body"))
    if "ear_L" in PIVOT:
        deformers.append(mat("def_EarL", PIVOT["ear_L"], "ParamEarSwayL", 20, parent="def_Head"))
    if "ear_R" in PIVOT:
        deformers.append(mat("def_EarR", PIVOT["ear_R"], "ParamEarSwayR", 20, parent="def_Head", flip=True))
    if "bottomwear" in PIVOT:
        deformers.append(mat("def_Skirt", PIVOT["bottomwear"], "ParamSkirtSway", 8, parent="def_Body"))
    if "leg_L" in PIVOT:
        # 腿用 **translateX** 而不是 rotate：腿很长（07 图 350px），绕腿根转 1.3° 就会让
        # 脚踝横移 8px，把本来只有 1px 的两腿间隙拉成 19px（实测 y=1120 +18px）。
        # 腿基本被裙子盖住、只露小腿，整体平移不会变形，也不会撑开缝。
        deformers.append(mat("def_LegL", PIVOT["leg_L"], "ParamLegSwayL", 3.0, parent="def_Body", channel="translateX"))
    if "leg_R" in PIVOT:
        deformers.append(mat("def_LegR", PIVOT["leg_R"], "ParamLegSwayR", 3.0, parent="def_Body", channel="translateX"))
    if "front hair" in PIVOT:
        deformers.append(mat("def_FrontHair", PIVOT["front hair"], "ParamHairFrontSway", 9, parent="def_Head"))
    if "back hair" in PIVOT:
        deformers.append(mat("def_BackHair", PIVOT["back hair"], "ParamHairBackSway", 7, parent="def_Head"))

    # ---------- 8) 物理 ----------
    # 输出 ≈ (x − L) × scale：振幅靠 scale，不靠 weight。
    # 左右对称件必须吃不同输入、各自翻转，否则就是跷跷板。
    physics = []
    if "tail" in PIVOT:
        physics.append({"id": "tail", "inputs": [
            {"parameter": "ParamAngleX", "weight": 1.0, "mode": "lag"},
            {"parameter": "ParamBodyAngleX", "weight": 0.9, "mode": "lag"},
            {"parameter": "ParamAngleZ", "weight": 0.6, "mode": "lag"}],
            "output": {"parameter": "ParamTailSway", "scale": 420},
            "mass": 1, "stiffness": 22, "damping": 2.2})
    if "front hair" in PIVOT:
        physics.append({"id": "frontHair", "inputs": [
            {"parameter": "ParamAngleX", "weight": 0.55, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 1.1, "mode": "lag"}],
            "output": {"parameter": "ParamHairFrontSway", "scale": 16},
            "mass": 1, "stiffness": 52, "damping": 6})
    if "back hair" in PIVOT:
        physics.append({"id": "backHair", "inputs": [
            {"parameter": "ParamAngleX", "weight": 0.5, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 1.0, "mode": "lag"}],
            "output": {"parameter": "ParamHairBackSway", "scale": 12},
            "mass": 1, "stiffness": 30, "damping": 4})
    if "ear_L" in PIVOT:
        physics.append({"id": "earL", "inputs": [
            {"parameter": "ParamAngleZ", "weight": 0.95, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.15, "mode": "lag"}],
            "output": {"parameter": "ParamEarSwayL", "scale": 420},
            "mass": 1.0, "stiffness": 92, "damping": 3.4})
    if "ear_R" in PIVOT:
        physics.append({"id": "earR", "inputs": [
            {"parameter": "ParamBodyAngleX", "weight": 0.95, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.15, "mode": "lag"}],
            "output": {"parameter": "ParamEarSwayR", "scale": 380},
            "mass": 1.3, "stiffness": 108, "damping": 2.7})
    if "bottomwear" in PIVOT:
        physics.append({"id": "skirt", "inputs": [
            {"parameter": "ParamBodyAngleX", "weight": 0.6, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.7, "mode": "lag"}],
            "output": {"parameter": "ParamSkirtSway", "scale": 16},
            "mass": 1, "stiffness": 38, "damping": 5})
    # 两腿异源输入 + 后腿幅度 60%：
    #   同源 → 同步摆，视觉上变一条腿（用户已否）
    #   等幅异源 → 缝被撑宽 7-11px
    #   异源 + 后腿 60% → 分开且缝只 +4px（采用）
    if "leg_L" in PIVOT:
        physics.append({"id": "legL", "inputs": [
            {"parameter": "ParamBodyAngleX", "weight": 0.85, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.25, "mode": "lag"}],
            "output": {"parameter": "ParamLegSwayL", "scale": 8},
            "mass": 1.0, "stiffness": 34, "damping": 4.6})
    if "leg_R" in PIVOT:
        physics.append({"id": "legR", "inputs": [
            {"parameter": "ParamAngleZ", "weight": 0.9, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.5, "mode": "lag"}],
            "output": {"parameter": "ParamLegSwayR", "scale": 8},
            "mass": 1.35, "stiffness": 56, "damping": 3.0})

    motions = {"Idle": [{
        "description": "缓慢呼吸式摇摆（9 秒循环）",
        "duration": 9.0, "fadeIn": 1.2, "fadeOut": 1.2,
        "curves": [
            {"parameter": "ParamAngleX", "keys": [[0, 0], [2.2, 3.6], [4.5, 0], [6.8, -3.6], [9, 0]]},
            {"parameter": "ParamAngleY", "keys": [[0, 0], [3.0, 1.8], [6.0, -1.4], [9, 0]]},
            {"parameter": "ParamAngleZ", "keys": [[0, 0], [2.6, 1.5], [5.4, -1.5], [9, 0]]},
            {"parameter": "ParamBodyAngleX", "keys": [[0, 0], [3.2, 2.2], [6.4, -2.2], [9, 0]]},
            {"parameter": "ParamBodyAngleZ", "keys": [[0, 0], [4.2, 1.0], [8.0, -1.0], [9, 0]]},
        ],
    }]}

    model = {
        "version": 1,
        "name": model_name,
        "canvas": {"width": W, "height": H},
        "parameters": params,
        "textures": [{"source": data_uri}],
        "parts": parts,
        "deformers": deformers,
        "physics": physics,
        "physicsChains": [],
        "motions": motions,
    }
    with open(out_iki, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n写出 {out_iki}  {os.path.getsize(out_iki)/1048576:.2f} MB")
    print(f"parts={len(parts)} deformers={len(deformers)} physics={len(physics)}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "立绘傀儡模型")
