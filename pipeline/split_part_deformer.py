# -*- coding: utf-8 -*-
"""给单个 part 换一个独立 matrix deformer，限制它自己的位移幅度。

为什么需要：不拆腿时 `legs` 会挂在 `def_Body` 上，跟着身体一起 translateX ±16。
腿是画序很靠底的层，背后只有空 backdrop —— 平移 16px 就露出 5 万像素背景，
用户看到的是「腿一动就露白边」。

给它一个独立、幅度小的 deformer 就能把这类露洞压到最低，而身体其余部分照常动。

用法：
    python split_part_deformer.py <in.iki> <out.iki> --part legs --id def_Legs \
        --param ParamLegSway --channel translateX --from -5 --to 5 --parent def_Body
"""
import json
import sys


def ensure_param(model, pid, name, pmin, pmax, default=0.0):
    for p in model.setdefault("parameters", []):
        if p["id"] == pid:
            return
    model["parameters"].append({
        "id": pid, "name": name, "min": pmin, "max": pmax, "default": default,
    })


def main():
    a = sys.argv
    src, dst = a[1], a[2]

    def arg(k, d=None):
        return a[a.index(k) + 1] if k in a else d

    part_id = arg("--part", "legs")
    def_id = arg("--id", "def_Legs")
    param = arg("--param", "ParamLegSway")
    channel = arg("--channel", "translateX")
    frm = float(arg("--from", "-5"))
    to = float(arg("--to", "5"))
    parent = arg("--parent", "def_Body")
    scale = float(arg("--scale", "30"))

    model = json.load(open(src, encoding="utf-8"))

    part = next((p for p in model["parts"] if p["id"] == part_id), None)
    if part is None:
        raise SystemExit(f"没有 part: {part_id}")

    # 参数：幅度用对称范围，物理 scale 决定参数能走多远
    ensure_param(model, param, f"{part_id} sway", -1.0, 1.0)

    # pivot 取该 part 的中心（model 空间）
    pivot = {"x": part["transform"]["x"], "y": part["transform"]["y"]}

    new_def = {
        "kind": "matrix",
        "id": def_id,
        "parent": parent,
        "pivot": pivot,
        "bindings": [{"channel": channel, "parameter": param, "from": frm, "to": to}],
    }
    model["deformers"] = [d for d in model.get("deformers", []) if d["id"] != def_id]
    model["deformers"].append(new_def)

    old = part.get("deformer")
    part["deformer"] = def_id

    # 物理：让待机时它自己轻轻摆
    # ⚠ inputs 必须是**非空数组**，缺了会让 parseIkiModel 报
    #    "physics[N].inputs must be a non-empty array"，播放器里只表现成模型加载不出来。
    # 同时 mass/stiffness/damping 三个也不能省。
    model.setdefault("physics", [])
    model["physics"] = [p for p in model["physics"] if p["id"] != f"{part_id}_sway"]
    model["physics"].append({
        "id": f"{part_id}_sway",
        "inputs": [
            {"parameter": "ParamBodyAngleX", "weight": 0.30, "mode": "lag"},
            {"parameter": "ParamAmbientSway", "weight": 0.55, "mode": "lag"},
        ],
        "output": {"parameter": param, "scale": scale},
        "mass": 1,
        "stiffness": 42,
        "damping": 5,
    })

    json.dump(model, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  {part_id}: {old} → {def_id}（{channel} [{frm:g},{to:g}]，param {param}，physics scale {scale:g}）")
    print(f"写出 {dst}")


if __name__ == "__main__":
    main()
