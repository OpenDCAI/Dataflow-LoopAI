#!/usr/bin/env bash
# Manage the local Qwen3-14B-FP8 OpenAI-compatible vLLM service.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VLLM_BIN="${QWEN_VLLM_BIN:-/workspace/caiwenchen/vllm/.venv/bin/vllm}"
MODEL_DIR="${QWEN_MODEL_DIR:-/workspace/caiwenchen/models/Qwen--Qwen3-14B-FP8/snapshots/master}"
MODEL_NAME="${QWEN_SERVED_MODEL_NAME:-qwen3-14b-fp8}"
HOST="${QWEN_VLLM_HOST:-0.0.0.0}"
PORT="${QWEN_VLLM_PORT:-8000}"
CUDA_VISIBLE_DEVICES_VALUE="${QWEN_CUDA_VISIBLE_DEVICES:-1,2,3,4}"
TENSOR_PARALLEL_SIZE="${QWEN_TENSOR_PARALLEL_SIZE:-4}"
GPU_MEMORY_UTILIZATION="${QWEN_GPU_MEMORY_UTILIZATION:-0.8}"
MAX_MODEL_LEN="${QWEN_MAX_MODEL_LEN:-32768}"
PID_FILE="$ROOT_DIR/envs/.loopai/qwen-vllm.pid"
LOG_FILE="$ROOT_DIR/envs/.loopai/qwen-vllm.log"

require_installation() {
    [[ -x "$VLLM_BIN" ]] || { echo "Missing vLLM executable: $VLLM_BIN" >&2; exit 1; }
    [[ -f "$MODEL_DIR/config.json" ]] || { echo "Missing Qwen model: $MODEL_DIR" >&2; exit 1; }
}

is_running() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
        return 0
    fi
    local detected_pid
    detected_pid="$(pgrep -f "$VLLM_BIN serve $MODEL_DIR.*--port $PORT" | head -n 1 || true)"
    if [[ -n "$detected_pid" ]]; then
        printf '%s\n' "$detected_pid" >"$PID_FILE"
        return 0
    fi
    return 1
}

case "${1:-start}" in
    start)
        require_installation
        if is_running; then
            echo "Qwen vLLM is already running (PID $(<"$PID_FILE"), port $PORT)."
            exit 0
        fi
        rm -f "$PID_FILE"
        (
            cd "$ROOT_DIR"
            nohup setsid env \
                CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES_VALUE" \
                PYTHONUNBUFFERED=1 \
                VLLM_WORKER_MULTIPROC_METHOD=spawn \
                "$VLLM_BIN" serve "$MODEL_DIR" \
                --host "$HOST" \
                --port "$PORT" \
                --served-model-name "$MODEL_NAME" \
                --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
                --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
                --max-model-len "$MAX_MODEL_LEN" \
                >"$LOG_FILE" 2>&1 &
            echo $! >"$PID_FILE"
        )
        echo "Qwen vLLM is starting (PID $(<"$PID_FILE"), GPUs $CUDA_VISIBLE_DEVICES_VALUE). Logs: $LOG_FILE"
        ;;
    stop)
        if is_running; then
            service_pid="$(<"$PID_FILE")"
            service_pgid="$(ps -o pgid= -p "$service_pid" | tr -d '[:space:]')"
            if [[ "$service_pgid" == "$service_pid" ]]; then
                kill -- "-$service_pgid"
            else
                kill "$service_pid"
            fi
            echo "Qwen vLLM stopped."
        else
            echo "Qwen vLLM is not running."
        fi
        rm -f "$PID_FILE"
        ;;
    status)
        if is_running; then
            echo "Qwen vLLM is running (PID $(<"$PID_FILE"), GPUs $CUDA_VISIBLE_DEVICES_VALUE, http://127.0.0.1:$PORT/v1/models)."
        else
            echo "Qwen vLLM is not running."
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 2
        ;;
esac
