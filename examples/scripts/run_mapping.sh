#!/bin/bash

# Test script for Mapping Node
# Usage: ./run_mapping.sh [intermediate_data_path]
#   intermediate_data_path: Path to intermediate format data directory (default: /mnt/DataFlow/lz/proj/agentgroup/binrui/postprocess_banchmark/processed_output)

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default configuration
if [ -n "$1" ]; then
    INTERMEDIATE_DATA_PATH="$1"
else
    INTERMEDIATE_DATA_PATH="${INTERMEDIATE_DATA_PATH:-/mnt/DataFlow/lz/proj/agentgroup/binrui/postprocess_banchmark/processed_output}"
fi
OBTAINER_MODEL_PATH="${OBTAINER_MODEL_PATH:-gpt-4o-mini}"
OBTAINER_BASE_URL="${OBTAINER_BASE_URL:-http://123.129.219.111:3000/v1v1}"
OBTAINER_TEMPERATURE="${OBTAINER_TEMPERATURE:-0.7}"
OBTAINER_DEBUG="${OBTAINER_DEBUG:-False}"  # Enable debug mode (logs all levels and saves to file)
OUTPUT_DIR="$(dirname "$INTERMEDIATE_DATA_PATH")"
OBTAINER_CATEGORY="${OBTAINER_CATEGORY:-PT}"  # PT or SFT
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

# Check if intermediate data directory exists
if [ ! -d "$INTERMEDIATE_DATA_PATH" ]; then
    echo "Error: Intermediate data directory does not exist: $INTERMEDIATE_DATA_PATH"
    echo "Please ensure that post-processing has been completed first."
    exit 1
fi

# Print configuration
echo "============================================================"
echo "Mapping Node - Convert Intermediate Format to Target Format"
echo "============================================================"
echo "Category: $OBTAINER_CATEGORY"
echo "Model: $OBTAINER_MODEL_PATH"
echo "Base URL: $OBTAINER_BASE_URL"
echo "Temperature: $OBTAINER_TEMPERATURE"
echo "Debug Mode: $OBTAINER_DEBUG"
echo "Intermediate Data Path: $INTERMEDIATE_DATA_PATH"
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
export INTERMEDIATE_DATA_PATH="$INTERMEDIATE_DATA_PATH"
export USER_QUERY="$USER_QUERY"

# Change to project root
cd "$PROJECT_ROOT"

# Add project root to PYTHONPATH so Python can find the loopai module
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Run the mapping script
python "$SCRIPT_DIR/run_mapping.py"

echo ""
echo "Mapping completed!"


