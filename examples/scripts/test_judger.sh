#!/usr/bin/env bash
# Quick test for Judger standalone pipeline
# Config reads from Configer (DB_PATH + TASK_ID), no YAML needed.
# Usage:
#   bash examples/scripts/test_judger.sh                      # general_text
#   bash examples/scripts/test_judger.sh code                  # code
#   bash examples/scripts/test_judger.sh resume                # resume

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TASK_TYPE="${1:-general_text}"
RESUME="${2:-}"

# ---- config ----
export DB_PATH="api/db/db.sqlite3"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-5}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

case "$TASK_TYPE" in
    general_text)
        TASK_ID="test_judger_gt_${TIMESTAMP}"
        echo "=== Testing general_text (gsm8k, key2_qa) ==="
        echo "task_id: $TASK_ID"
        python examples/scripts/run_judger_standalone.py \
            --task-id "$TASK_ID" \
            --print-result \
            --print-events
        ;;

    code)
        TASK_ID="test_judger_code_${TIMESTAMP}"
        echo "=== Testing code (human-eval) ==="
        echo "task_id: $TASK_ID"
        python examples/scripts/run_judger_standalone.py \
            --task-id "$TASK_ID" \
            --print-result \
            --print-events
        ;;

    resume)
        echo "=== Testing --resume ==="
        echo "Enter task_id to resume:"
        read -r TASK_ID
        python examples/scripts/run_judger_standalone.py \
            --task-id "$TASK_ID" \
            --resume \
            --print-result
        ;;

    "")
        echo "Usage: bash examples/scripts/test_judger.sh [general_text|code]"
        exit 1
        ;;
esac

echo ""
echo "=== Done ==="
echo "Events:     outputs/${TASK_ID}/judger.pkl"
echo "Results:    outputs/${TASK_ID}/judger/"
echo ""
echo "# Read events:"
echo "  python -c \"from loopai.skills.Judger import load_events; events = load_events('${TASK_ID}'); [print(e['message']) for e in events]\""
echo ""
echo "# Read state from Configer:"
echo "  python -c \"from loopai.skills.Judger.runner import _load_task_state; s = _load_task_state('${TASK_ID}'); print(s.get('last_completed'))\""
