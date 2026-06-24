#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTER_DIR="${ROOT_DIR}/codex-chat-router"
CACHE_DIR="${ROOT_DIR}/.cache_codex"

mkdir -p \
    "${CACHE_DIR}/target" \
    "${CACHE_DIR}/tmp"

export LOOPAI_CODEX_PROXY_UPSTREAM_BASE_URL="${LOOPAI_CODEX_PROXY_UPSTREAM_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"
export LOOPAI_CODEX_PROXY_MODEL="${LOOPAI_CODEX_PROXY_MODEL:-${DEEPSEEK_MODEL:-deepseek-chat}}"
export LOOPAI_CODEX_PROXY_HOST="${LOOPAI_CODEX_PROXY_HOST:-127.0.0.1}"
export LOOPAI_CODEX_PROXY_PORT="${LOOPAI_CODEX_PROXY_PORT:-15721}"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${CACHE_DIR}/target/codex-chat-router}"
export TMPDIR="${TMPDIR:-${CACHE_DIR}/tmp}"

if [[ -z "${LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY:-${DEEPSEEK_API_KEY:-}}" ]]; then
    echo "Missing LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY or DEEPSEEK_API_KEY." >&2
    exit 1
fi

if ! CARGO_BIN="$(command -v cargo 2>/dev/null)"; then
    echo "Missing cargo in PATH. Please install Rust via rustup first." >&2
    exit 1
fi

if [[ -z "${CARGO_HOME:-}" ]]; then
    export CARGO_HOME
    CARGO_HOME="$(cd "$(dirname "${CARGO_BIN}")/.." && pwd)"
fi

if [[ -z "${RUSTUP_HOME:-}" ]] && command -v rustup >/dev/null 2>&1; then
    RUSTUP_HOME_CANDIDATE="$(rustup show home 2>/dev/null || true)"
    if [[ -n "${RUSTUP_HOME_CANDIDATE}" ]]; then
        export RUSTUP_HOME="${RUSTUP_HOME_CANDIDATE}"
    fi
fi

export PATH="${CARGO_HOME}/bin:${PATH}"

echo "Starting Codex Rust Chat Completions router..."
echo "Local base URL: http://${LOOPAI_CODEX_PROXY_HOST}:${LOOPAI_CODEX_PROXY_PORT}/v1"
echo "Upstream base URL: ${LOOPAI_CODEX_PROXY_UPSTREAM_BASE_URL}"
echo "Model: ${LOOPAI_CODEX_PROXY_MODEL}"
echo "Cargo target: ${CARGO_TARGET_DIR}"
echo "Cargo home: ${CARGO_HOME}"
if [[ -n "${RUSTUP_HOME:-}" ]]; then
    echo "Rustup home: ${RUSTUP_HOME}"
fi

cd "${ROUTER_DIR}"
exec cargo run --release -- \
    --host "${LOOPAI_CODEX_PROXY_HOST}" \
    --port "${LOOPAI_CODEX_PROXY_PORT}" \
    --upstream-base-url "${LOOPAI_CODEX_PROXY_UPSTREAM_BASE_URL}" \
    --model "${LOOPAI_CODEX_PROXY_MODEL}"
