#!/bin/bash

# Test script for ObtainerAgent websearch node
# Usage: ./run_obtainer.sh [query]

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default configuration
TEST_QUERY="${1:-Find datasets about coding processing for llm SFT}"
OBTAINER_MODEL_PATH="${OBTAINER_MODEL_PATH:-gpt-4o}"
OBTAINER_BASE_URL="${OBTAINER_BASE_URL:-http://123.129.219.111:3000/v1}"
OBTAINER_SEARCH_ENGINE="${OBTAINER_SEARCH_ENGINE:-tavily}"
OBTAINER_MAX_URLS="${OBTAINER_MAX_URLS:-10}"
OBTAINER_MAX_DOWNLOAD_SUBTASKS="${OBTAINER_MAX_DOWNLOAD_SUBTASKS:-5}"
OBTAINER_TEMPERATURE="${OBTAINER_TEMPERATURE:-0.7}"
OBTAINER_RESET_RAG="${OBTAINER_RESET_RAG:-False}"
OBTAINER_CATEGORY="${OBTAINER_CATEGORY:-PT}"  # PT or SFT
OBTAINER_DEBUG="${OBTAINER_DEBUG:-False}"  # Enable debug mode (logs all levels and saves to file)
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/output/obtainer_outputs}"

# Enable download execution (set to False to skip download node)
ENABLE_DOWNLOAD="${ENABLE_DOWNLOAD:-True}"

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

# Check Kaggle credentials
if [ -z "$KAGGLE_USERNAME" ] && [ -z "$KAGGLE_KEY" ]; then
    echo "Warning: No Kaggle credentials found. Set KAGGLE_USERNAME and KAGGLE_KEY environment variables"
    echo "or configure ~/.kaggle/kaggle.json file for Kaggle download functionality"
else
    if [ -n "$KAGGLE_USERNAME" ] && [ -n "$KAGGLE_KEY" ]; then
        echo "Using Kaggle credentials from environment variables"
    else
        echo "Warning: Incomplete Kaggle credentials. Both KAGGLE_USERNAME and KAGGLE_KEY are required"
    fi
fi

# Print configuration
echo "============================================================"
echo "ObtainerAgent - Complete Workflow Test"
echo "============================================================"
echo "Test Query: $TEST_QUERY"
echo "Model: $OBTAINER_MODEL_PATH"
echo "Base URL: $OBTAINER_BASE_URL"
echo "Search Engine: $OBTAINER_SEARCH_ENGINE"
echo "Max URLs: $OBTAINER_MAX_URLS"
echo "Max Download Subtasks: $OBTAINER_MAX_DOWNLOAD_SUBTASKS"
echo "Temperature: $OBTAINER_TEMPERATURE"
echo "Reset RAG: $OBTAINER_RESET_RAG"
echo "Category: $OBTAINER_CATEGORY"  # PT or SFT
echo "Debug Mode: $OBTAINER_DEBUG"  # Debug mode
echo "Enable Download: $ENABLE_DOWNLOAD"
echo "Output Dir: $OUTPUT_DIR"
echo "============================================================"
echo ""

# Export environment variables
export TEST_QUERY="$TEST_QUERY"
export OBTAINER_MODEL_PATH="$OBTAINER_MODEL_PATH"
export OBTAINER_BASE_URL="$OBTAINER_BASE_URL"
export OBTAINER_SEARCH_ENGINE="$OBTAINER_SEARCH_ENGINE"
export OBTAINER_MAX_URLS="$OBTAINER_MAX_URLS"
export OBTAINER_MAX_DOWNLOAD_SUBTASKS="$OBTAINER_MAX_DOWNLOAD_SUBTASKS"
export OBTAINER_TEMPERATURE="$OBTAINER_TEMPERATURE"
export OBTAINER_RESET_RAG="$OBTAINER_RESET_RAG"
export OBTAINER_CATEGORY="$OBTAINER_CATEGORY"
export OBTAINER_DEBUG="$OBTAINER_DEBUG"
export OUTPUT_DIR="$OUTPUT_DIR"
export ENABLE_DOWNLOAD="$ENABLE_DOWNLOAD"
# Export Kaggle credentials if set
if [ -n "$KAGGLE_USERNAME" ]; then
    export KAGGLE_USERNAME="$KAGGLE_USERNAME"
fi
if [ -n "$KAGGLE_KEY" ]; then
    export KAGGLE_KEY="$KAGGLE_KEY"
fi

# Change to project root
cd "$PROJECT_ROOT"

# Add project root to PYTHONPATH so Python can find the loopai module
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Run the test script
python "$SCRIPT_DIR/run_obtainer.py"

echo ""
echo "Test completed!"

