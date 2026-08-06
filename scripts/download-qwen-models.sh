#!/usr/bin/env bash
# Download the requested Qwen3 checkpoints from ModelScope into this repository.
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
model_root="$repo_root/model"
python_bin="${PYTHON_BIN:-python3}"

# A local mihomo HTTP proxy is running on this host.  Do not override a proxy
# explicitly supplied by the caller.
export HTTP_PROXY="${HTTP_PROXY:-http://127.0.0.1:7890}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7890}"
export ALL_PROXY="${ALL_PROXY:-http://127.0.0.1:7890}"
export NO_PROXY="${NO_PROXY:-127.0.0.1,localhost}"

mkdir -p "$model_root"

if ! "$python_bin" -c 'import modelscope' >/dev/null 2>&1; then
  "$python_bin" -m pip install --user --upgrade 'modelscope>=1.25.0'
fi

"$python_bin" - "$model_root" <<'PY'
import sys
from pathlib import Path
from modelscope import snapshot_download

target_root = Path(sys.argv[1])
models = (
    ("Qwen/Qwen3-0.6B", "Qwen3-0.6B"),
    ("Qwen/Qwen3-1.7B", "Qwen3-1.7B"),
    ("Qwen/Qwen3-4B", "Qwen3-4B"),
    ("Qwen/Qwen3-8B", "Qwen3-8B"),
)
for repo_id, folder in models:
    destination = target_root / folder
    print(f"\n=== Downloading {repo_id} -> {destination} ===", flush=True)
    snapshot_download(repo_id, local_dir=str(destination))
    print(f"=== Finished {repo_id} ===", flush=True)
PY
