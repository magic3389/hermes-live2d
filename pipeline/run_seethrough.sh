#!/usr/bin/env bash
# 用本地已下载的 See-through 权重跑拆层（不走 HF 下载）
#
# 用法:
#   bash run_seethrough.sh --src ../../input/subject_1280.png
#
# 环境变量（都有默认值，可覆盖）:
#   ST_DIR      See-through 仓库根（默认 ./see-through）
#   ST_VENV     独立 venv 的 activate 脚本（默认 ./venv）
#   ST_LAYERDIFF layerdiff3d 权重目录
#   ST_DEPTH    marigold 深度模型目录
#   ST_OUT      输出目录（默认 ./out/layerdiff_output）
set -u

ST_DIR="${ST_DIR:-./see-through}"
ST_VENV="${ST_VENV:-./venv}"
ST_LAYERDIFF="${ST_LAYERDIFF:-D:/models/seethrough/layerdiff3d}"
ST_DEPTH="${ST_DEPTH:-D:/models/seethrough/marigold}"
ST_OUT="${ST_OUT:-./out/layerdiff_output}"

SRC=""; RES=1280; STEPS=30; OFFLOAD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --src)      SRC="$2"; shift 2 ;;
    --res)      RES="$2"; shift 2 ;;
    --steps)    STEPS="$2"; shift 2 ;;
    --out)      ST_OUT="$2"; shift 2 ;;
    --offload)  OFFLOAD="--group_offload"; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

[ -z "$SRC" ] && { echo "必须给 --src <图>"; exit 1; }
[ -d "$ST_DIR" ] || { echo "找不到 See-through 仓库: $ST_DIR（先 clone 见 docs/03_setup.md）"; exit 1; }
[ -d "$ST_LAYERDIFF" ] || { echo "找不到 layerdiff3d 权重: $ST_LAYERDIFF"; exit 1; }

mkdir -p "$ST_OUT"
set -x
cd "$ST_DIR" || exit 1
# Windows(git-bash): venv 的 activate 在 Scripts/；Linux/mac 在 bin/
if [ -f "../$ST_VENV/Scripts/activate" ]; then
  source "../$ST_VENV/Scripts/activate"
elif [ -f "../$ST_VENV/bin/activate" ]; then
  source "../$ST_VENV/bin/activate"
fi

python inference/scripts/inference_psd.py \
  --srcp "$SRC" \
  --save_dir "../$ST_OUT" \
  --repo_id_layerdiff "$ST_LAYERDIFF" \
  --repo_id_depth "$ST_DEPTH" \
  --resolution "$RES" \
  --inference_steps "$STEPS" \
  --save_to_psd \
  $OFFLOAD

echo "=== 完成，PSD 在 $ST_OUT ==="
echo "下一步: python postprocess.py <psd> <原图png> <输出psd>"
