#!/usr/bin/env bash
set -euo pipefail

# Starts a vLLM OpenAI-compatible embedding server for ObtainerCLI.
#
# Required:
#   OBTAINERCLI_VLLM_IMAGE      Docker image that contains the custom torch/vLLM stack.
#
# Optional:
#   OBTAINERCLI_EMBED_MODEL_DIR   Host model dir.
#   OBTAINERCLI_EMBED_MODEL_NAME  Served model name used by /v1/embeddings.
#   OBTAINERCLI_EMBED_PORT        Host port.
#   OBTAINERCLI_DOCKER_GPU_ARGS   Device/runtime args for the local accelerator setup.
#   OBTAINERCLI_VLLM_CMD          Server command inside the container.

MODEL_DIR="${OBTAINERCLI_EMBED_MODEL_DIR:-/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5}"
MODEL_NAME="${OBTAINERCLI_EMBED_MODEL_NAME:-BAAI/bge-small-zh-v1.5}"
PORT="${OBTAINERCLI_EMBED_PORT:-8000}"
IMAGE="${OBTAINERCLI_VLLM_IMAGE:-}"
DOCKER_GPU_ARGS="${OBTAINERCLI_DOCKER_GPU_ARGS:---privileged}"
VLLM_CMD="${OBTAINERCLI_VLLM_CMD:-python -m vllm.entrypoints.openai.api_server}"

if [[ -z "${IMAGE}" ]]; then
  echo "OBTAINERCLI_VLLM_IMAGE is required. Set it to your custom torch/vLLM Docker image." >&2
  exit 2
fi

if [[ ! -d "${MODEL_DIR}" ]]; then
  echo "Model directory does not exist: ${MODEL_DIR}" >&2
  exit 2
fi

echo "Starting ObtainerCLI embedding server"
echo "  image:       ${IMAGE}"
echo "  model dir:   ${MODEL_DIR}"
echo "  model name:  ${MODEL_NAME}"
echo "  port:        ${PORT}"

# shellcheck disable=SC2086
exec docker run --rm --network host ${DOCKER_GPU_ARGS} \
  -v "${MODEL_DIR}:/models/embedding:ro" \
  "${IMAGE}" \
  bash -lc "${VLLM_CMD} \
    --host 0.0.0.0 \
    --port ${PORT} \
    --model /models/embedding \
    --served-model-name ${MODEL_NAME} \
    --task embed $*"
