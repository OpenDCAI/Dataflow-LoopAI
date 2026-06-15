#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER_DIR="${ROOT_DIR}/codex-chat-router"

export CODEX_UPSTREAM_BASE_URL="${CODEX_UPSTREAM_BASE_URL:-${CODEX_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}}"
export CODEX_MODEL="${CODEX_MODEL:-${DEEPSEEK_MODEL:-deepseek-chat}}"
export CODEX_LOCAL_PROXY_HOST="${CODEX_LOCAL_PROXY_HOST:-127.0.0.1}"
export CODEX_LOCAL_PROXY_PORT="${CODEX_LOCAL_PROXY_PORT:-15721}"
export CARGO_HOME="${CARGO_HOME:-/home/xuebinrui/.cargo}"
export RUSTUP_HOME="${RUSTUP_HOME:-/home/xuebinrui/.rustup}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/data/xuebinrui/cargo-target/loopai-codex-chat-router}"
export TMPDIR="${TMPDIR:-/data/xuebinrui/tmp}"
export PATH="${CARGO_HOME}/bin:${PATH}"

if [[ -z "${CODEX_API_KEY:-${DEEPSEEK_API_KEY:-}}" ]]; then
    echo "Missing CODEX_API_KEY or DEEPSEEK_API_KEY." >&2
    exit 1
fi

echo "Starting Codex Rust Chat Completions router..."
echo "Local base URL: http://${CODEX_LOCAL_PROXY_HOST}:${CODEX_LOCAL_PROXY_PORT}/v1"
echo "Upstream base URL: ${CODEX_UPSTREAM_BASE_URL}"
echo "Model: ${CODEX_MODEL}"
echo "Cargo target: ${CARGO_TARGET_DIR}"

cd "${ROUTER_DIR}"
exec cargo run --release -- \
    --host "${CODEX_LOCAL_PROXY_HOST}" \
    --port "${CODEX_LOCAL_PROXY_PORT}" \
    --upstream-base-url "${CODEX_UPSTREAM_BASE_URL}" \
    --model "${CODEX_MODEL}"
