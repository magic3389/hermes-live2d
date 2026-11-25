#!/usr/bin/env bash
# See-through 独立环境安装
#
# 铁律：绝不装进 ComfyUI 的 venv —— 那套环境有别的节点依赖（torch/xformers 版本互相咬），
# 冲突后果是 ComfyUI 整片起不来，而且很难定位到是这里改的。
#
# 用法: bash install_env.sh
# 环境变量: PROXY（默认 http://127.0.0.1:7897，不需要就设空）
set -eu

PROXY="${PROXY-http://127.0.0.1:7897}"
if [ -n "$PROXY" ]; then
  export HTTPS_PROXY="$PROXY"; export HTTP_PROXY="$PROXY"
  export ALL_PROXY="$PROXY";   export UV_HTTP_TIMEOUT=120
  echo "== 走代理 $PROXY =="
fi

MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"

echo "=== [1/4] python 3.12 ==="
uv python install 3.12 || { echo "FAIL python install"; exit 2; }

echo "=== [2/4] venv ==="
uv venv venv --python 3.12 || { echo "FAIL venv"; exit 3; }
if [ -f venv/Scripts/activate ]; then source venv/Scripts/activate; else source venv/bin/activate; fi
python -V
# uv venv 默认不带 pip；torch 官方源要用 pip 才能正确走代理
python -m ensurepip --upgrade || { echo "FAIL ensurepip"; exit 3; }

echo "=== [3/4] torch cu128 (~2.5GB，约 8–20 分钟) ==="
python -m pip install --upgrade pip $MIRROR >/dev/null 2>&1
python -m pip install torch==2.8.0+cu128 torchvision==0.23.0+cu128 torchaudio==2.8.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128 || { echo "FAIL torch"; exit 4; }
python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"

echo "=== [4/4] requirements ==="
cd see-through || { echo "FAIL: 当前目录下没有 see-through/ 子目录"; exit 5; }
python -m pip install -r requirements.txt $MIRROR || { echo "FAIL requirements"; exit 6; }

echo "=== DONE ==="
python -c "import torch,diffusers,transformers;print('torch',torch.__version__);print('diffusers',diffusers.__version__);print('transformers',transformers.__version__)"
