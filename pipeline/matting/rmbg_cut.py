# -*- coding: utf-8 -*-
"""用 ComfyUI 的 LayerMask: RemBgUltra（RMBG-1.4）抠出人物，导出 mask 灰度图。

为什么要单独导 mask：SaveImage 存 RGB 会丢 alpha，直接看输出图会以为“什么都没去掉”。

用法:
    python rmbg_cut.py --image my_input.png
    python rmbg_cut.py --image my_input.png --prefix maid --comfy http://127.0.0.1:8188

前置:
    · image 必须已经放在 ComfyUI 的 input/ 目录里（LoadImage 只认那里）
    · ComfyUI 已装 ComfyUI_LayerStyle（提供 LayerMask: RemBgUltra）
    · 权重 RMBG-1.4 已放在 models/rembg/ 或 models/background_removal/

输出:
    打印 ComfyUI 产出的文件名，mask 在 <ComfyUI>/output/<prefix>_rmbg_mask_00001_.png
"""
import argparse
import json
import time
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--image", required=True, help="ComfyUI input/ 下的文件名")
p.add_argument("--prefix", default="subject", help="输出文件名前缀")
p.add_argument("--comfy", default="http://127.0.0.1:8188")
p.add_argument("--detail-range", type=int, default=8)
p.add_argument("--timeout", type=int, default=900)
a = p.parse_args()


def api(path, data=None):
    url = a.comfy + path
    if data is None:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


wf = {
    "1": {"class_type": "LoadImage", "inputs": {"image": a.image}},
    # ⚠ process_detail 必须 False：True 会走 trimap 分支，
    # 而 LayerStyle 内置的 pymatting 版本不兼容，直接报
    # cannot import name 'fix_trimap' from 'pymatting'
    "2": {"class_type": "LayerMask: RemBgUltra",
          "inputs": {"image": ["1", 0], "detail_range": a.detail_range,
                     "black_point": 0.01, "white_point": 0.99,
                     "process_detail": False}},
    # mask → 灰度图，绕开 SaveImage 丢 alpha 的问题
    "3": {"class_type": "MaskToImage", "inputs": {"mask": ["2", 1]}},
    "4": {"class_type": "SaveImage",
          "inputs": {"images": ["3", 0], "filename_prefix": f"{a.prefix}_rmbg_mask"}},
}

pid = api("/prompt", {"prompt": wf})["prompt_id"]
print("prompt_id", pid)
t0 = time.time()
while time.time() - t0 < a.timeout:
    h = api(f"/history/{pid}")
    if pid in h:
        st = h[pid].get("status", {})
        print("状态", st.get("status_str"))
        if st.get("status_str") == "error":
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    print("错误:", m[1].get("exception_message", "")[:600])
        for node, o in h[pid].get("outputs", {}).items():
            for im in o.get("images", []):
                print(f"  node{node}: {im['filename']}   subfolder={im.get('subfolder','')!r}")
        print("\nmask 通常在 <ComfyUI>/output/%s_rmbg_mask_00001_.png" % a.prefix)
        break
    time.sleep(3)
else:
    print("!! 超时，任务可能仍在跑")
