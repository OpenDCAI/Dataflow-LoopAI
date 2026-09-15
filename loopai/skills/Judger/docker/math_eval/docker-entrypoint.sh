#!/bin/sh
set -eu

exec python3 /app/eval/evaluate_math.py "$@"
