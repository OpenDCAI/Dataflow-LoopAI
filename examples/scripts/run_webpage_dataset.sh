#!/bin/bash

# Test script for WebPage Dataset Node
# Usage: ./run_webpage_dataset.sh [query] [webpage_data_path]

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default configuration (will be overridden by YAML config)
TEST_QUERY="${1:-我需要构建一个专门用于 Python 基础语法修复与类型增强的 SFT 数据集，请收集相关资料}"
WEBPAGE_DATA_PATH="${2:-}"
OBTAINER_MODEL_PATH="${OBTAINER_MODEL_PATH:-gpt-4o}"
OBTAINER_BASE_URL="${OBTAINER_BASE_URL:-http://123.129.219.111:3000/v1v1}"
OBTAINER_TEMPERATURE="${OBTAINER_TEMPERATURE:-0.7}"
OBTAINER_CATEGORY="${OBTAINER_CATEGORY:-SFT}"  # PT or SFT
OBTAINER_DEBUG="${OBTAINER_DEBUG:-false}"
MAX_RECORDS_PER_PAGE="${MAX_RECORDS_PER_PAGE:-20}"  # Maximum records per webpage (default: 20)
MIN_RELEVANCE_SCORE="${MIN_RELEVANCE_SCORE:-0.7}"  # Minimum relevance score (0.0-1.0)
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/output/webpage_dataset_outputs}"

# Check if API key file exists
API_KEY_FILE="$SCRIPT_DIR/api_key.txt"
if [ -f "$API_KEY_FILE" ]; then
    echo "Using API key from $API_KEY_FILE"
else
    if [ -z "$API_KEY" ]; then
        echo "Warning: No API key found. Please set API_KEY environment variable or create $API_KEY_FILE"
        echo "Using default 'empty' API key"
    fi
fi

# Print configuration
echo "============================================================"
echo "WebPage Dataset Node - Generate PT/SFT Dataset"
echo "============================================================"
echo "Test Query: $TEST_QUERY"
echo "Model: $OBTAINER_MODEL_PATH"
echo "Base URL: $OBTAINER_BASE_URL"
echo "Temperature: $OBTAINER_TEMPERATURE"
echo "Category: $OBTAINER_CATEGORY"
echo "Debug Mode: $OBTAINER_DEBUG"
echo "Max Records Per Page: $MAX_RECORDS_PER_PAGE"
echo "Min Relevance Score: $MIN_RELEVANCE_SCORE"
echo "Webpage Data Path: ${WEBPAGE_DATA_PATH:-Not provided (will try to find or fetch from URLs)}"
echo "Output Dir: $OUTPUT_DIR"
echo "============================================================"
echo ""
echo "Note: Configuration will be loaded from examples/config/starter.yaml"
echo "      Environment variables can override YAML settings"
echo ""
echo "Usage:"
echo "  1. Run webpage_collect_node first to collect webpage data"
echo "  2. Then run this script to generate dataset from collected webpages"
echo "  3. Or provide webpage URLs via WEBPAGE_URLS environment variable"
echo ""

# Export environment variables (for override)
export TEST_QUERY="$TEST_QUERY"
export WEBPAGE_DATA_PATH="$WEBPAGE_DATA_PATH"
export OBTAINER_MODEL_PATH="$OBTAINER_MODEL_PATH"
export OBTAINER_BASE_URL="$OBTAINER_BASE_URL"
export OBTAINER_TEMPERATURE="$OBTAINER_TEMPERATURE"
export OBTAINER_CATEGORY="$OBTAINER_CATEGORY"
export OBTAINER_DEBUG="$OBTAINER_DEBUG"
export MAX_RECORDS_PER_PAGE="$MAX_RECORDS_PER_PAGE"
export MIN_RELEVANCE_SCORE="$MIN_RELEVANCE_SCORE"
export OUTPUT_DIR="$OUTPUT_DIR"

# Change to project root
cd "$PROJECT_ROOT"

# Add project root to PYTHONPATH so Python can find the loopai module
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Run the test script
python "$SCRIPT_DIR/run_webpage_dataset.py" "$TEST_QUERY"

echo ""
echo "Test completed!"

