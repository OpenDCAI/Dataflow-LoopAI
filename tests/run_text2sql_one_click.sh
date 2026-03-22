#!/usr/bin/env bash
set -euo pipefail

# ====== 按需修改 ======
CONDA_ENV="base"
INPUT_JSONL="/mnt/paper2any/xbr/commit/debug1205/data/outputs/downloads/processed_output/SFT_00001.jsonl"
SAMPLE_SIZE=20
MODEL_PATH="${TEXT2SQL_MODEL_PATH:-gpt-4o}"
BASE_URL="${TEXT2SQL_BASE_URL:-http://172.96.160.199:3000/v1}"
API_KEY="${TEXT2SQL_API_KEY:-sk-...}"
# ======================



cd /mnt/paper2any/xbr/commit/debug1205/Dataflow-LoopAI

python tests/test_text2sql_filter_tool.py \
  --input-jsonl "${INPUT_JSONL}" \
  --sample-size "${SAMPLE_SIZE}" \
  --model-path "${MODEL_PATH}" \
  --base-url "${BASE_URL}" \
  --api-key "${API_KEY}"
