#!/bin/bash

# Test script for Post-process Node
# Usage: ./run_postprocess.sh [category] [download_dir]
#   category: PT or SFT (default: PT)
#   download_dir: Path to downloads directory (default: output/obtainer_outputs/downloads)

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default configuration
OBTAINER_CATEGORY="${1:-PT}"  # PT or SFT
if [ -n "$2" ]; then
    DOWNLOAD_DIR="$2"
else
    DOWNLOAD_DIR="${DOWNLOAD_DIR:-$PROJECT_ROOT/output/obtainer_outputs/downloads}"
fi
OBTAINER_MODEL_PATH="${OBTAINER_MODEL_PATH:-gpt-4o}"
OBTAINER_BASE_URL="${OBTAINER_BASE_URL:-http://123.129.219.111:3000/v1}"
OBTAINER_TEMPERATURE="${OBTAINER_TEMPERATURE:-0.0}"
OBTAINER_DEBUG="${OBTAINER_DEBUG:-False}"  # Enable debug mode (logs all levels and saves to file)
OUTPUT_DIR="$(dirname "$DOWNLOAD_DIR")"
USER_QUERY="${USER_QUERY:-}"

# Check if API key file exists
API_KEY_FILE="$SCRIPT_DIR/api_key.txt"
if [ -f "$API_KEY_FILE" ]; then
    echo "Using API key from $API_KEY_FILE"
    export API_KEY="$(cat $API_KEY_FILE | tr -d '\n' | tr -d '\r')"
else
    if [ -z "$API_KEY" ]; then
        echo "Warning: No API key found. Please set API_KEY environment variable or create $API_KEY_FILE"
        echo "Using default 'empty' API key"
        export API_KEY="empty"
    fi
fi

# Validate category
if [ "$OBTAINER_CATEGORY" != "PT" ] && [ "$OBTAINER_CATEGORY" != "SFT" ]; then
    echo "Error: Category must be PT or SFT, got: $OBTAINER_CATEGORY"
    exit 1
fi

# Check if download directory exists
if [ ! -d "$DOWNLOAD_DIR" ]; then
    echo "Error: Download directory does not exist: $DOWNLOAD_DIR"
    echo "Please ensure that downloads have been completed first."
    exit 1
fi

# Print configuration
echo "============================================================"
echo "Post-process Node - Convert Downloaded Datasets"
echo "============================================================"
echo "Category: $OBTAINER_CATEGORY"
echo "Model: $OBTAINER_MODEL_PATH"
echo "Base URL: $OBTAINER_BASE_URL"
echo "Temperature: $OBTAINER_TEMPERATURE"
echo "Debug Mode: $OBTAINER_DEBUG"
echo "Download Dir: $DOWNLOAD_DIR"
echo "Output Dir: $OUTPUT_DIR"
if [ -n "$USER_QUERY" ]; then
    echo "User Query: $USER_QUERY"
fi
echo "============================================================"
echo ""

# Export environment variables
export OBTAINER_MODEL_PATH="$OBTAINER_MODEL_PATH"
export OBTAINER_BASE_URL="$OBTAINER_BASE_URL"
export OBTAINER_TEMPERATURE="$OBTAINER_TEMPERATURE"
export OBTAINER_CATEGORY="$OBTAINER_CATEGORY"
export OBTAINER_DEBUG="$OBTAINER_DEBUG"
export OUTPUT_DIR="$OUTPUT_DIR"
export DOWNLOAD_DIR="$DOWNLOAD_DIR"
export USER_QUERY="$USER_QUERY"

# Change to project root
cd "$PROJECT_ROOT"

# Add project root to PYTHONPATH so Python can find the loopai module
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Run the post-process script
python "$SCRIPT_DIR/run_postprocess.py"

echo ""
echo "Post-process completed!"

