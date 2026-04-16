"""
Test script for WebPage Dataset Node - Generate PT/SFT dataset from webpage content
"""
import json
import os
from pathlib import Path
from omegaconf import OmegaConf

from loopai.agents.Obtainer.nodes.webpage_dataset_node import webpage_dataset_node
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

# Get obtainer configuration from config file
obtainer_model_path = cfg.default_states.get('obtainer_model_path', 'gpt-4o-mini')
obtainer_base_url = cfg.default_states.get('obtainer_base_url', cfg.starter.base_url)
obtainer_api_key = cfg.default_states.get('obtainer_api_key', '') or api_key
obtainer_temperature = float(cfg.default_states.get('obtainer_temperature', 0.7))
obtainer_category = cfg.default_states.get('obtainer_category', 'PT').upper()
obtainer_debug = cfg.default_states.get('obtainer_debug', False)

# Output directory
output_dir = os.getenv('OUTPUT_DIR', str(PROJECT_ROOT / 'output' / 'webpage_dataset_outputs'))
os.makedirs(output_dir, exist_ok=True)

# Test query (from command line argument or environment variable)
test_query = os.getenv('TEST_QUERY', '收集关于SQL代码的项目数据信息')
if len(os.sys.argv) > 1:
    test_query = ' '.join(os.sys.argv[1:])

# Get webpage data path (from previous webpage_collect run)
webpage_data_path = os.getenv('WEBPAGE_DATA_PATH', '')
if not webpage_data_path:
    # Try to find the latest webpage_data.jsonl in output directory
    webpage_collect_dir = PROJECT_ROOT / 'output' / 'webpage_collect_outputs'
    if webpage_collect_dir.exists():
        jsonl_files = list(webpage_collect_dir.glob('**/webpage_data.jsonl'))
        if jsonl_files:
            webpage_data_path = str(sorted(jsonl_files, key=lambda x: x.stat().st_mtime)[-1])
            print(f"Found webpage data: {webpage_data_path}")

# Get webpage URLs (optional, if no JSONL file)
webpage_urls = os.getenv('WEBPAGE_URLS', '').split(',') if os.getenv('WEBPAGE_URLS') else []

print("=" * 80)
print("WebPage Dataset Node - Generate PT/SFT Dataset from Webpages")
print("=" * 80)
print(f"Model: {obtainer_model_path}")
print(f"Base URL: {obtainer_base_url}")
print(f"Temperature: {obtainer_temperature}")
print(f"Category: {obtainer_category}")
print(f"Debug Mode: {obtainer_debug}")
print(f"Test Query: {test_query}")
print(f"Webpage Data Path: {webpage_data_path or 'Not provided (will fetch from URLs)'}")
print(f"Webpage URLs: {len(webpage_urls)} URLs provided" if webpage_urls else "No URLs provided")
print(f"Output Dir: {output_dir}")
print("=" * 80)

# Prepare state
initial_state = LoopAIState(
    task_id='webpage_dataset_test_001',
    output_dir=output_dir,
    automated_query=test_query,
    messages=[HumanMessage(content=test_query)],
    obtainer_model_path=obtainer_model_path,
    obtainer_base_url=obtainer_base_url,
    obtainer_api_key=obtainer_api_key,
    obtainer_temperature=obtainer_temperature,
    obtainer_category=obtainer_category,
    obtainer_debug=obtainer_debug,
    webpage_collect_jsonl_path=webpage_data_path if webpage_data_path else '',
    webpage_collect_urls_visited=webpage_urls if webpage_urls else [],
    obtainer_max_records_per_page=int(cfg.default_states.get('obtainer_max_records_per_page', os.getenv('MAX_RECORDS_PER_PAGE', '10'))),
    obtainer_min_relevance_score=float(cfg.default_states.get('obtainer_min_relevance_score', os.getenv('MIN_RELEVANCE_SCORE', '0.7'))),
    prompt_template_dir=None,  # Use default
)

print("\nStarting WebPage Dataset Node...")
print(f"Query: {test_query}\n")

try:
    # Run the node
    result_state = webpage_dataset_node(initial_state)
    
    print("\n" + "=" * 80)
    print("WebPage Dataset Results")
    print("=" * 80)
    
    # Check for errors
    if result_state.get("exception"):
        print(f"\n[ERROR] {result_state.get('exception')}")
        print("-" * 80)
    else:
        # Print summary
        if result_state.get('webpage_dataset_summary'):
            print("\n[Summary]")
            print("-" * 80)
            print(result_state.get('webpage_dataset_summary'))
        
        # Print statistics
        print(f"\n[Statistics]")
        print("-" * 80)
        print(f"  Generated Records: {result_state.get('webpage_dataset_count', 0)}")
        print(f"  Category: {obtainer_category}")
        
        # Print output file
        jsonl_path = result_state.get('webpage_dataset_jsonl_path', '')
        if jsonl_path:
            print(f"\n[Output File]")
            print("-" * 80)
            print(f"  JSONL: {jsonl_path}")
            
            # Show first few records as preview
            if os.path.exists(jsonl_path):
                print(f"\n[Preview - First 3 Records]")
                print("-" * 80)
                with open(jsonl_path, 'r', encoding='utf-8') as f:
                    for i, line in enumerate(f):
                        if i >= 3:
                            break
                        if line.strip():
                            try:
                                record = json.loads(line)
                                if obtainer_category == "PT":
                                    text_preview = record.get('text', '')[:200] if isinstance(record.get('text'), str) else str(record.get('text', ''))[:200]
                                    print(f"\n  Record {i+1}:")
                                    print(f"    Text: {text_preview}...")
                                    print(f"    Relevance: {record.get('relevance_score', 'N/A')}")
                                else:  # SFT
                                    messages = record.get('messages', [])
                                    print(f"\n  Record {i+1}:")
                                    for j, msg in enumerate(messages[:2]):  # Show first 2 messages
                                        role = msg.get('role', 'unknown')
                                        content = msg.get('content', '')[:150] if isinstance(msg.get('content'), str) else str(msg.get('content', ''))[:150]
                                        print(f"    {role}: {content}...")
                                    print(f"    Relevance: {record.get('relevance_score', 'N/A')}")
                            except json.JSONDecodeError:
                                pass
    
    # Save results to file
    output_file = Path(output_dir) / 'webpage_dataset_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'query': test_query,
            'category': obtainer_category,
            'summary': result_state.get('webpage_dataset_summary', ''),
            'dataset_count': result_state.get('webpage_dataset_count', 0),
            'jsonl_path': result_state.get('webpage_dataset_jsonl_path', ''),
            'exception': result_state.get('exception', ''),
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[Results saved to: {output_file}]")
    print("=" * 80)
    print("WebPage Dataset test finished!")
    
except Exception as e:
    print(f"\n[ERROR] Test failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

