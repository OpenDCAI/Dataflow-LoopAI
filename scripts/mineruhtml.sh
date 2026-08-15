#!/usr/bin/env bash
# Manage the local MinerU-HTML (Dripper v1.0) service.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$ROOT_DIR/envs/.mineruhtml"
PYTHON_BIN="$ENV_DIR/bin/python"
MODEL_DIR="$ROOT_DIR/model/mineru-html"
PORT="${MINERUHTML_PORT:-7986}"
HOST="${MINERUHTML_HOST:-0.0.0.0}"
CUDA_VISIBLE_DEVICES_VALUE="${MINERUHTML_CUDA_VISIBLE_DEVICES:-0}"
PID_FILE="$ENV_DIR/mineruhtml.pid"
LOG_FILE="$ENV_DIR/mineruhtml.log"
SOURCE_DIR="$ENV_DIR/src"
DEFAULT_MODEL_INIT_KWARGS='{"tensor_parallel_size": 1}'
MODEL_INIT_KWARGS="${MINERUHTML_MODEL_INIT_KWARGS:-$DEFAULT_MODEL_INIT_KWARGS}"

require_installation() {
    [[ -x "$PYTHON_BIN" ]] || { echo "Missing environment: $ENV_DIR" >&2; exit 1; }
    [[ -d "$SOURCE_DIR/dripper" ]] || { echo "Missing MinerU-HTML source: $SOURCE_DIR" >&2; exit 1; }
    [[ -f "$MODEL_DIR/config.json" ]] || { echo "Missing model files: $MODEL_DIR" >&2; exit 1; }
}

is_running() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
        return 0
    fi

    # Recover after a terminal/session restart when the service itself is
    # still running but its original PID file is stale.
    local detected_pid
    detected_pid="$(pgrep -f "$PYTHON_BIN -m dripper.server --port $PORT" | head -n 1 || true)"
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
            echo "MinerU-HTML is already running (PID $(<"$PID_FILE"), port $PORT)."
            exit 0
        fi
        rm -f "$PID_FILE"
        (
            cd "$SOURCE_DIR"
            nohup setsid env \
                CUDA_VISIBLE_DEVICES="$CUDA_VISIBLE_DEVICES_VALUE" \
                PYTHONUNBUFFERED=1 \
                DRIPPER_MODEL_PATH="$MODEL_DIR" \
                DRIPPER_PORT="$PORT" \
                INFERENCE_BACKEND=vllm \
                MODEL_INIT_KWARGS="$MODEL_INIT_KWARGS" \
                VLLM_USE_V1=0 \
                "$PYTHON_BIN" -m dripper.server --port "$PORT" \
                >"$LOG_FILE" 2>&1 &
            echo $! >"$PID_FILE"
        )
        echo "MinerU-HTML is starting (PID $(<"$PID_FILE"), physical GPU $CUDA_VISIBLE_DEVICES_VALUE). Logs: $LOG_FILE"
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
            echo "MinerU-HTML stopped."
        else
            echo "MinerU-HTML is not running."
        fi
        rm -f "$PID_FILE"
        ;;
    status)
        if is_running; then
            echo "MinerU-HTML is running (PID $(<"$PID_FILE"), physical GPU $CUDA_VISIBLE_DEVICES_VALUE, http://127.0.0.1:$PORT/health)."
        else
            echo "MinerU-HTML is not running."
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 2
        ;;
esac
