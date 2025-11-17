"""
Test script for ObtainerAgent - Complete workflow (websearch + download)
"""
import json
import os
from pathlib import Path

from loopai.agents import ObtainerAgent
from loopai.memory import checkpointer, store
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage

# Configuration
# Read API key from file if exists, otherwise use environment variable
api_key = None
api_key_file = Path(__file__).parent / 'api_key.txt'
if api_key_file.exists():
    with open(api_key_file, 'r') as f:
        api_key = f.read().strip()
else:
    api_key = os.getenv('API_KEY', 'empty')

# Read Tavily API key if exists
tavily_api_key = None
tavily_api_key_file = Path(__file__).parent / 'tavily_api_key.txt'
if tavily_api_key_file.exists():
    with open(tavily_api_key_file, 'r') as f:
        tavily_api_key = f.read().strip()
        os.environ['TAVILY_API_KEY'] = tavily_api_key

# Read Kaggle credentials if exists
kaggle_username = os.getenv('KAGGLE_USERNAME', '')
kaggle_key = os.getenv('KAGGLE_KEY', '')

# Model configuration
MODEL_CONFIG = {
    'obtainer_model_path': os.getenv('OBTAINER_MODEL_PATH', 'gpt-4o'),
    'obtainer_base_url': os.getenv('OBTAINER_BASE_URL', 'http://123.129.219.111:3000/v1'),
    'obtainer_api_key': api_key,
    'obtainer_temperature': float(os.getenv('OBTAINER_TEMPERATURE', '0.7')),
    'obtainer_search_engine': os.getenv('OBTAINER_SEARCH_ENGINE', 'tavily'),  # tavily, duckduckgo, jina
    'obtainer_max_urls': int(os.getenv('OBTAINER_MAX_URLS', '10')),
    'obtainer_max_download_subtasks': int(os.getenv('OBTAINER_MAX_DOWNLOAD_SUBTASKS', '5')) if os.getenv('OBTAINER_MAX_DOWNLOAD_SUBTASKS') else None,
    'obtainer_reset_rag': os.getenv('OBTAINER_RESET_RAG', 'False').lower() == 'true',
    'obtainer_kaggle_username': kaggle_username,
    'obtainer_kaggle_key': kaggle_key,
    'obtainer_tavily_api_key': tavily_api_key if tavily_api_key else '',  # Tavily API key from file or env
    'obtainer_category': os.getenv('OBTAINER_CATEGORY', 'PT').upper(),  # PT or SFT
    'obtainer_debug': os.getenv('OBTAINER_DEBUG', 'False').lower() == 'true',  # Enable debug mode
}

# Output directory
output_dir = os.getenv('OUTPUT_DIR', str(Path(__file__).parent.parent.parent / 'output' / 'obtainer_outputs'))
os.makedirs(output_dir, exist_ok=True)

# Test query
test_query = os.getenv('TEST_QUERY', 'Find datasets about coding processing for llm SFT')

print("=" * 80)
print("ObtainerAgent - Complete Workflow Test")
print("=" * 80)
print(f"Model: {MODEL_CONFIG['obtainer_model_path']}")
print(f"Base URL: {MODEL_CONFIG['obtainer_base_url']}")
print(f"Search Engine: {MODEL_CONFIG['obtainer_search_engine']}")
print(f"Max URLs: {MODEL_CONFIG['obtainer_max_urls']}")
print(f"Max Download Subtasks: {MODEL_CONFIG['obtainer_max_download_subtasks']}")
print(f"Category: {MODEL_CONFIG['obtainer_category']}")  # PT or SFT
print(f"Debug Mode: {MODEL_CONFIG['obtainer_debug']}")  # Debug mode
print(f"Test Query: {test_query}")
print(f"Output Dir: {output_dir}")
print("=" * 80)

# Initialize agent
agent = ObtainerAgent(
    checkpointer=checkpointer,
    store=store,
)

# Create graph
graph = agent()

# Prepare state
config = {"configurable": {"thread_id": "obtainer_test_1"}}

initial_state = {
    'task_id': 'obtainer_test_001',
    'mined_data': '',
    'output_dir': output_dir,
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
    'analyze_top_p': 0.95,
    'output_brief': False,
    'analyze_output_result_path': '',
    'analyze_output_summary_path': '',
    'analyze_output_report_json_path': '',
    'analyze_output_report_text_path': '',
    'output_suggestion': False,
    'analyze_output_suggestion_path': '',
    'update_model_path': '',
    'current': 'obtain',
    'next_to': '',
    'automated_query': '',
    'exception': '',
    'obtainer_research_summary': '',
    'obtainer_subtasks': [],
    'obtainer_urls_visited': [],
    'obtainer_debug': MODEL_CONFIG['obtainer_debug'],
    'obtainer_download_results': {},
    'messages': [HumanMessage(content=test_query)],
    **MODEL_CONFIG
}

print("\nStarting ObtainerAgent complete workflow...")
print(f"Query: {test_query}\n")

try:
    # Run the graph
    result = graph.invoke(initial_state, config=config)
    
    print("\n" + "=" * 80)
    print("Complete Workflow Results")
    print("=" * 80)
    
    # Print research summary
    if result.get('obtainer_research_summary'):
        print("\n[Research Summary]")
        print("-" * 80)
        print(result['obtainer_research_summary'])
    
    # Print subtasks (before download)
    if result.get('obtainer_subtasks'):
        print(f"\n[Generated Download Subtasks: {len(result['obtainer_subtasks'])}]")
        print("-" * 80)
        for i, task in enumerate(result['obtainer_subtasks'], 1):
            print(f"\n  Subtask {i}:")
            print(f"    Type: {task.get('type', 'N/A')}")
            print(f"    Objective: {task.get('objective', 'N/A')}")
            print(f"    Search Keywords: {task.get('search_keywords', 'N/A')}")
            print(f"    Status: {task.get('status', 'pending')}")
            if task.get('status') == 'completed_successfully':
                print(f"    Method Used: {task.get('method_used', 'N/A')}")
                print(f"    Download Path: {task.get('download_path', 'N/A')}")
            elif task.get('status') in ['failed_to_download', 'failed_due_to_size_limit']:
                print(f"    Failure Reason: {task.get('failure_reason', 'N/A')}")
    else:
        print("\n[No subtasks generated]")
    
    # Print visited URLs
    if result.get('obtainer_urls_visited'):
        print(f"\n[Visited URLs: {len(result['obtainer_urls_visited'])}]")
        print("-" * 80)
        for i, url in enumerate(result['obtainer_urls_visited'], 1):
            print(f"  {i}. {url}")
    
    # Print download results
    if result.get('obtainer_download_results'):
        download_results = result['obtainer_download_results']
        print(f"\n[Download Execution Results]")
        print("-" * 80)
        print(f"  Total: {download_results.get('total', 0)}")
        print(f"  Completed: {download_results.get('completed', 0)}")
        print(f"  Failed: {download_results.get('failed', 0)}")
    
    # Print post-process results
    if result.get('obtainer_postprocess_results'):
        postprocess_results = result['obtainer_postprocess_results']
        print(f"\n[Post-process Results]")
        print("-" * 80)
        print(f"  Total Records Processed: {postprocess_results.get('total_records_processed', 0)}")
        print(f"  Processed Sources: {postprocess_results.get('processed_sources_count', 0)}")
        print(f"  Output Directory: {postprocess_results.get('output_dir', 'N/A')}")
    
    # Check for errors
    if result.get('exception'):
        print(f"\n[ERROR] {result['exception']}")
        print("-" * 80)
    
    # Save results to file
    output_file = Path(output_dir) / 'obtainer_complete_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'query': test_query,
            'category': MODEL_CONFIG.get('obtainer_category', 'PT'),
            'research_summary': result.get('obtainer_research_summary', ''),
            'subtasks': result.get('obtainer_subtasks', []),
            'urls_visited': result.get('obtainer_urls_visited', []),
            'download_results': result.get('obtainer_download_results', {}),
            'postprocess_results': result.get('obtainer_postprocess_results', {}),
            'exception': result.get('exception', ''),
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[Results saved to: {output_file}]")
    print("=" * 80)
    print("Complete workflow test finished!")
    
except Exception as e:
    print(f"\n[ERROR] Test failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

