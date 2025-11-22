"""
Test script for Post-process Node - Converts downloaded datasets to PT/SFT format
"""
import json
import os
from pathlib import Path

from loopai.agents.Obtainer.nodes.postprocess_node import postprocess_node
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage

# Configuration
# Read API key from file if exists, otherwise use environment variable
api_key = None
api_key_file = Path(__file__).parent / 'api_key.txt'
if api_key_file.exists():
    with open(api_key_file, 'r', encoding='utf-8') as f:
        api_key = f.read().strip().strip('\r\n').strip('\n').strip('\r')
        if not api_key:
            print(f"Warning: API key file {api_key_file} exists but is empty")
            api_key = os.getenv('API_KEY', 'empty')
else:
    api_key = os.getenv('API_KEY', 'empty')

# Validate API key
if not api_key or api_key == 'empty':
    print("=" * 80)
    print("WARNING: API key is missing or invalid!")
    print("Please set API_KEY environment variable or create api_key.txt file")
    print(f"Expected location: {api_key_file}")
    print("=" * 80)

# Model configuration
MODEL_CONFIG = {
    'obtainer_model_path': os.getenv('OBTAINER_MODEL_PATH', 'gpt-4o'),
    'obtainer_base_url': os.getenv('OBTAINER_BASE_URL', 'http://123.129.219.111:3000/v1'),
    'obtainer_api_key': api_key,
    'obtainer_temperature': float(os.getenv('OBTAINER_TEMPERATURE', '0.0')),
    'obtainer_category': os.getenv('OBTAINER_CATEGORY', 'PT').upper(),  # PT or SFT
    'obtainer_debug': os.getenv('OBTAINER_DEBUG', 'False').lower() == 'true',  # Enable debug mode
}

# Output directory (should point to the downloads directory)
# If DOWNLOAD_DIR is provided, use its parent as output_dir
if os.getenv('DOWNLOAD_DIR'):
    download_dir = os.getenv('DOWNLOAD_DIR')
    output_dir = os.path.dirname(download_dir)
else:
    output_dir = os.getenv('OUTPUT_DIR', str(Path(__file__).parent.parent.parent / 'output' / 'obtainer_outputs'))
    download_dir = os.path.join(output_dir, 'downloads')

# If download_dir doesn't exist, try to use output_dir/downloads as fallback
if not os.path.exists(download_dir):
    fallback_download_dir = os.path.join(output_dir, 'downloads')
    if os.path.exists(fallback_download_dir):
        download_dir = fallback_download_dir
        print(f"Using fallback download directory: {download_dir}")

# User query for context (optional)
user_query = os.getenv('USER_QUERY', '')

print("=" * 80)
print("Post-process Node - Convert Downloaded Datasets to PT/SFT Format")
print("=" * 80)
print(f"Model: {MODEL_CONFIG['obtainer_model_path']}")
print(f"Base URL: {MODEL_CONFIG['obtainer_base_url']}")
print(f"Category: {MODEL_CONFIG['obtainer_category']}")  # PT or SFT
print(f"Debug Mode: {MODEL_CONFIG['obtainer_debug']}")
print(f"Download Dir: {download_dir}")
print(f"User Query: {user_query if user_query else '(Not provided)'}")
print("=" * 80)

# Check if download directory exists
if not os.path.exists(download_dir):
    print(f"Error: Download directory does not exist: {download_dir}")
    print("Please ensure that downloads have been completed first.")
    exit(1)

# Prepare state with mock successful download tasks
# In a real scenario, these would come from the actual download results
# For testing, we'll create a mock state that points to the download directory
initial_state = {
    'task_id': 'postprocess_test_001',
    'mined_data': '',
    'output_dir': output_dir,  # This should be the parent of downloads directory
    'configer_error': '',
    'configer_statement': '',
    'eval_model_path': '',
    'eval_base_url': '',
    'eval_api_key': '',
    'eval_test_case_path': '',
    'eval_problem_path': '',
    'eval_result_path': '',
    'analyze_model_path': MODEL_CONFIG['obtainer_model_path'],
    'analyze_base_url': MODEL_CONFIG['obtainer_base_url'],
    'analyze_api_key': MODEL_CONFIG['obtainer_api_key'],
    'analyze_temperature': MODEL_CONFIG['obtainer_temperature'],
    'obtainer_model_path': MODEL_CONFIG['obtainer_model_path'],
    'obtainer_base_url': MODEL_CONFIG['obtainer_base_url'],
    'obtainer_api_key': MODEL_CONFIG['obtainer_api_key'],
    'obtainer_temperature': MODEL_CONFIG['obtainer_temperature'],
    'obtainer_category': MODEL_CONFIG['obtainer_category'],
    'obtainer_debug': MODEL_CONFIG['obtainer_debug'],
    'obtainer_subtasks': [
        {
            'type': 'download',
            'status': 'completed_successfully',
            'download_path': download_dir,  # Point to download directory
            'objective': 'Post-process downloaded datasets',
        }
    ] if os.path.exists(download_dir) and os.listdir(download_dir) else [],
    'messages': [
        HumanMessage(content=user_query) if user_query else HumanMessage(content='Post-process downloaded datasets')
    ],
    'automated_query': user_query if user_query else '',
}

# Create state object
state = LoopAIState(**initial_state)

print("\nStarting post-process node...")
print("-" * 80)

# Run post-process node
try:
    result_state = postprocess_node(state)
    
    print("\n" + "=" * 80)
    print("Post-process Node Completed")
    print("=" * 80)
    
    # Print results
    postprocess_results = result_state.get('obtainer_postprocess_results', {})
    if postprocess_results:
        print(f"Total Records Processed: {postprocess_results.get('total_records_processed', 0)}")
        print(f"Processed Sources Count: {postprocess_results.get('processed_sources_count', 0)}")
        print(f"Output Directory: {postprocess_results.get('output_dir', 'N/A')}")
    else:
        print("No post-processing results found.")
    
    if result_state.get('exception'):
        print(f"\nWarning: Exception occurred: {result_state.get('exception')}")
    
    print("\n" + "=" * 80)
    print("Post-process completed successfully!")
    print("=" * 80)
    
except Exception as e:
    print(f"\nError during post-processing: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

