#!/bin/bash

# Test script for WebPage Collect Node
# Usage: ./run_webpage_collect.sh [query]

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default configuration (will be overridden by YAML config)
TEST_QUERY="${1:-我需要构建一个专门用于 Python 基础语法修复与类型增强的 SFT 数据集，请收集相关资料}"
OBTAINER_MODEL_PATH="${OBTAINER_MODEL_PATH:-gpt-4o}"
OBTAINER_BASE_URL="${OBTAINER_BASE_URL:-http://123.129.219.111:3000/v1v1}"
OBTAINER_TEMPERATURE="${OBTAINER_TEMPERATURE:-0.7}"
OBTAINER_MAX_EXPLORATION_DEPTH="${OBTAINER_MAX_EXPLORATION_DEPTH:-5}"
OBTAINER_MAX_JINA_URLS="${OBTAINER_MAX_JINA_URLS:-50}"
OBTAINER_DEBUG="${OBTAINER_DEBUG:-false}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/output/webpage_collect_outputs}"

# Proxy configuration (for Clash, etc.)
# Set HTTP_PROXY, HTTPS_PROXY, or ALL_PROXY environment variable
# Example: export HTTP_PROXY="http://127.0.0.1:7890"
# Or set OBTAINER_PROXY in state/config
OBTAINER_PROXY="${OBTAINER_PROXY:-${HTTP_PROXY:-${HTTPS_PROXY:-${ALL_PROXY:-}}}}"

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

# Check if Tavily API key file exists
TAVILY_API_KEY_FILE="$SCRIPT_DIR/tavily_api_key.txt"
if [ -f "$TAVILY_API_KEY_FILE" ]; then
    export TAVILY_API_KEY="$(cat $TAVILY_API_KEY_FILE | tr -d '\n' | tr -d '\r')"
    echo "Using Tavily API key from $TAVILY_API_KEY_FILE"
elif [ -n "$TAVILY_API_KEY" ]; then
    echo "Using Tavily API key from environment variable"
else
    echo "Warning: No Tavily API key found. Set TAVILY_API_KEY environment variable or create $TAVILY_API_KEY_FILE"
    echo "Will fallback to DuckDuckGo if Tavily is selected as search engine"
fi

# Print configuration
echo "============================================================"
echo "WebPage Collect Node - Independent Test"
echo "============================================================"
echo "Test Query: $TEST_QUERY"
echo "Model: $OBTAINER_MODEL_PATH"
echo "Base URL: $OBTAINER_BASE_URL"
echo "Temperature: $OBTAINER_TEMPERATURE"
echo "Max Exploration Depth: $OBTAINER_MAX_EXPLORATION_DEPTH"
echo "Max Jina URLs: $OBTAINER_MAX_JINA_URLS"
echo "Debug Mode: $OBTAINER_DEBUG"
echo "Output Dir: $OUTPUT_DIR"
echo "============================================================"
echo ""
echo "Note: Configuration will be loaded from examples/config/starter.yaml"
echo "      Environment variables can override YAML settings"
echo ""

# Export environment variables (for override)
export TEST_QUERY="$TEST_QUERY"
export OBTAINER_MODEL_PATH="$OBTAINER_MODEL_PATH"
export OBTAINER_BASE_URL="$OBTAINER_BASE_URL"
export OBTAINER_TEMPERATURE="$OBTAINER_TEMPERATURE"
export OBTAINER_MAX_EXPLORATION_DEPTH="$OBTAINER_MAX_EXPLORATION_DEPTH"
export OBTAINER_MAX_JINA_URLS="$OBTAINER_MAX_JINA_URLS"
export OBTAINER_DEBUG="$OBTAINER_DEBUG"
export OUTPUT_DIR="$OUTPUT_DIR"
export OBTAINER_PROXY="$OBTAINER_PROXY"
# Also export standard proxy env vars for httpx
export HTTP_PROXY="${HTTP_PROXY:-$OBTAINER_PROXY}"
export HTTPS_PROXY="${HTTPS_PROXY:-$OBTAINER_PROXY}"
export ALL_PROXY="${ALL_PROXY:-$OBTAINER_PROXY}"

# Change to project root
cd "$PROJECT_ROOT"

# Add project root to PYTHONPATH so Python can find the loopai module
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Run the test script
python "$SCRIPT_DIR/run_webpage_collect.py" "$TEST_QUERY"

echo ""
echo "Test completed!"

