"""
Test script for WebPage Collect Node - Independent webpage collection workflow
"""
import json
import os
from pathlib import Path
from omegaconf import OmegaConf
from loopai.agents.Obtainer.nodes.webpage_collect_node import webpage_collect_node
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage
# Get script directory
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
CONFIG_PATH = PROJECT_ROOT / "examples" / "config" / "starter.yaml"

# Load configuration from YAML
cfg = OmegaConf.load(str(CONFIG_PATH))

# Read API key from file if exists
api_key = None
api_key_path = Path(cfg.starter.api_key_path)
if not api_key_path.is_absolute():
    api_key_path = SCRIPT_DIR / api_key_path
if api_key_path.exists():
    with open(api_key_path, 'r') as f:
        api_key = f.read().strip()
else:
    api_key = os.getenv('API_KEY', 'empty')

# Read Tavily API key
tavily_api_key = None
tavily_api_key_path = Path(cfg.starter.tavily_api_key_path)
if not tavily_api_key_path.is_absolute():
    tavily_api_key_path = SCRIPT_DIR / tavily_api_key_path
if tavily_api_key_path.exists():
    with open(tavily_api_key_path, 'r') as f:
        tavily_api_key = f.read().strip()

elif os.getenv('TAVILY_API_KEY'):
    tavily_api_key = os.getenv('TAVILY_API_KEY')

# Get obtainer configuration from config file
obtainer_model_path = cfg.default_states.get('obtainer_model_path', 'gpt-4o')
obtainer_base_url = cfg.default_states.get('obtainer_base_url', cfg.starter.base_url)
obtainer_api_key = cfg.default_states.get('obtainer_api_key', '') or api_key
obtainer_temperature = float(cfg.default_states.get('obtainer_temperature', 0.7))
obtainer_tavily_api_key = tavily_api_key if tavily_api_key else ''
obtainer_max_exploration_depth = int(cfg.default_states.get('obtainer_max_exploration_depth', 5))
obtainer_max_jina_urls = int(cfg.default_states.get('obtainer_max_jina_urls', 50))
obtainer_debug = cfg.default_states.get('obtainer_debug', False)

# Output directory
output_dir = os.getenv('OUTPUT_DIR', str(PROJECT_ROOT / 'output' / 'webpage_collect_outputs'))
os.makedirs(output_dir, exist_ok=True)

# Test query (from command line argument or environment variable)
test_query = os.getenv('TEST_QUERY', '收集关于机器学习的数据集信息')
if len(os.sys.argv) > 1:
    test_query = ' '.join(os.sys.argv[1:])

print("=" * 80)
print("WebPage Collect Node - Independent Test")
print("=" * 80)
print(f"Model: {obtainer_model_path}")
print(f"Base URL: {obtainer_base_url}")
print(f"Temperature: {obtainer_temperature}")
print(f"Max Exploration Depth: {obtainer_max_exploration_depth}")
print(f"Max Jina URLs: {obtainer_max_jina_urls}")
print(f"Debug Mode: {obtainer_debug}")
print(f"Test Query: {test_query}")
print(f"Output Dir: {output_dir}")
print("=" * 80)

# Prepare state
initial_state = LoopAIState(
    task_id='webpage_collect_test_001',
    output_dir=output_dir,
    automated_query=test_query,
    messages=[HumanMessage(content=test_query)],
    obtainer_model_path=obtainer_model_path,
    obtainer_base_url=obtainer_base_url,
    obtainer_api_key=obtainer_api_key,
    obtainer_temperature=obtainer_temperature,
    obtainer_tavily_api_key=obtainer_tavily_api_key,
    obtainer_max_exploration_depth=obtainer_max_exploration_depth,
    obtainer_max_jina_urls=obtainer_max_jina_urls,
    obtainer_debug=obtainer_debug,
    obtainer_proxy=os.getenv('OBTAINER_PROXY') or os.getenv('HTTP_PROXY') or os.getenv('HTTPS_PROXY') or os.getenv('ALL_PROXY') or '',
    prompt_template_dir=None,  # Use default
)

print("\nStarting WebPage Collect Node...")
print(f"Query: {test_query}\n")

try:
    # Run the node
    result_state = webpage_collect_node(initial_state)
    
    print("\n" + "=" * 80)
    print("WebPage Collect Results")
    print("=" * 80)
    
    # Check for errors
    if result_state.get("exception"):
        print(f"\n[ERROR] {result_state.get('exception')}")
        print("-" * 80)
    else:
        # Print summary
        if result_state.get('webpage_collect_summary'):
            print("\n[Summary]")
            print("-" * 80)
            print(result_state.get('webpage_collect_summary'))
        
        # Print statistics
        print(f"\n[Statistics]")
        print("-" * 80)
        print(f"  Collected Pages: {result_state.get('webpage_collect_data_count', 0)}")
        print(f"  Visited URLs: {len(result_state.get('webpage_collect_urls_visited', []))}")
        
        # Print visited URLs
        if result_state.get('webpage_collect_urls_visited'):
            print(f"\n[Visited URLs: {len(result_state.get('webpage_collect_urls_visited', []))}]")
            print("-" * 80)
            for i, url in enumerate(result_state.get('webpage_collect_urls_visited', [])[:20], 1):
                print(f"  {i}. {url}")
            if len(result_state.get('webpage_collect_urls_visited', [])) > 20:
                print(f"  ... and {len(result_state.get('webpage_collect_urls_visited', [])) - 20} more")
        
        # Print output files
        print(f"\n[Output Files]")
        print("-" * 80)
        jsonl_path = result_state.get('webpage_collect_jsonl_path', '')
        db_path = result_state.get('webpage_collect_db_path', '')
        if jsonl_path:
            print(f"  JSONL: {jsonl_path}")
        if db_path:
            print(f"  Database: {db_path}")
    
    # Save results to file
    output_file = Path(output_dir) / 'webpage_collect_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'query': test_query,
            'summary': result_state.get('webpage_collect_summary', ''),
            'data_count': result_state.get('webpage_collect_data_count', 0),
            'urls_visited': result_state.get('webpage_collect_urls_visited', []),
            'jsonl_path': result_state.get('webpage_collect_jsonl_path', ''),
            'db_path': result_state.get('webpage_collect_db_path', ''),
            'exception': result_state.get('exception', ''),
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[Results saved to: {output_file}]")
    print("=" * 80)
    print("WebPage Collect test finished!")
    
except Exception as e:
    print(f"\n[ERROR] Test failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

