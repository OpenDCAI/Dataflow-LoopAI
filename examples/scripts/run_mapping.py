"""
Test script for Mapping Subgraph - Converts intermediate format data to target format

This script tests the new MappingSubgraph which handles:
1. User inquiry for format selection
2. Preset format selection (non-LLM)
3. Custom format generation (LLM)
4. Format confirmation
5. Data mapping (script-based for presets, LLM-based for custom)
6. Result summary
"""
import json
import os
from pathlib import Path

from loopai.agents.Obtainer.mapping import MappingSubgraph
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

# Configuration
# Read API key from file if exists, otherwise use environment variable
api_key = None
api_key_file = Path(__file__).parent / 'api_key.txt'
if api_key_file.exists():
    with open(api_key_file, 'r') as f:
        api_key = f.read().strip()
else:
    api_key = os.getenv('API_KEY', 'empty')

# Model configuration
MODEL_CONFIG = {
    'obtainer_model_path': os.getenv('OBTAINER_MODEL_PATH', 'gpt-4o'),
    'obtainer_base_url': os.getenv('OBTAINER_BASE_URL', 'http://123.129.219.111:3000/v1'),
    'obtainer_api_key': api_key,
    'obtainer_temperature': float(os.getenv('OBTAINER_TEMPERATURE', '0.7')),
    'obtainer_category': os.getenv('OBTAINER_CATEGORY', 'PT').upper(),  # PT or SFT
    'obtainer_debug': os.getenv('OBTAINER_DEBUG', 'False').lower() == 'true',
}

# Intermediate data path
intermediate_data_path = os.getenv('INTERMEDIATE_DATA_PATH', '/mnt/DataFlow/lz/proj/agentgroup/binrui/postprocess_banchmark/processed_output')

# Output directory (parent of intermediate data path)
if os.getenv('OUTPUT_DIR'):
    output_dir = os.getenv('OUTPUT_DIR')
else:
    output_dir = os.path.dirname(intermediate_data_path)

# User query for context (optional)
user_query = os.getenv('USER_QUERY', '')

print("=" * 80)
print("Mapping Subgraph - Convert Intermediate Format to Target Format")
print("=" * 80)
print(f"Model: {MODEL_CONFIG['obtainer_model_path']}")
print(f"Base URL: {MODEL_CONFIG['obtainer_base_url']}")
print(f"Category: {MODEL_CONFIG['obtainer_category']}")
print(f"Debug Mode: {MODEL_CONFIG['obtainer_debug']}")
print(f"Intermediate Data Path: {intermediate_data_path}")
print(f"Output Dir: {output_dir}")
print(f"User Query: {user_query if user_query else '(Not provided)'}")
print("=" * 80)

# Check if intermediate data directory exists
if not os.path.exists(intermediate_data_path):
    print(f"Error: Intermediate data directory does not exist: {intermediate_data_path}")
    print("Please ensure that post-processing has been completed first.")
    exit(1)

# Prepare initial state
initial_state = {
    'task_id': 'mapping_test_001',
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
    'obtainer_model_path': MODEL_CONFIG['obtainer_model_path'],
    'obtainer_base_url': MODEL_CONFIG['obtainer_base_url'],
    'obtainer_api_key': MODEL_CONFIG['obtainer_api_key'],
    'obtainer_temperature': MODEL_CONFIG['obtainer_temperature'],
    'obtainer_category': MODEL_CONFIG['obtainer_category'],
    'obtainer_debug': MODEL_CONFIG['obtainer_debug'],
    'obtainer_intermediate_data_path': intermediate_data_path,
    # New mapping subgraph state fields
    'obtainer_mapping_user_intent': '',
    'obtainer_mapping_selected_format_id': '',
    'obtainer_mapping_custom_description': '',
    'obtainer_pending_format': None,
    'obtainer_confirmed_format': None,
    'obtainer_confirmation_result': '',
    'obtainer_mapping_results': None,
    'messages': [],  # 不设置初始消息，让 inquiry_node 显示欢迎消息
    'automated_query': user_query if user_query else '',
}

# Create state object
state = LoopAIState(**initial_state)

print("\nStarting mapping subgraph...")
print("-" * 80)
print("Note: This subgraph will interactively ask you to select or customize the target format.")
print("-" * 80)

# Create checkpointer and store for the subgraph
checkpointer = MemorySaver()
store = InMemoryStore()

# Build the mapping subgraph
mapping_subgraph = MappingSubgraph(checkpointer=checkpointer, store=store)
graph = mapping_subgraph.build()


def extract_interrupt_message(interrupt_info):
    """Extract the message from interrupt info"""
    # Handle tuple format from stream - (Interrupt(...),)
    if isinstance(interrupt_info, tuple):
        if len(interrupt_info) > 0:
            interrupt_obj = interrupt_info[0]
            if hasattr(interrupt_obj, 'value'):
                return str(interrupt_obj.value)
    
    # Handle list format - [Interrupt(...)]
    if isinstance(interrupt_info, list) and len(interrupt_info) > 0:
        interrupt_obj = interrupt_info[0]
        if hasattr(interrupt_obj, 'value'):
            return str(interrupt_obj.value)
    
    # Handle dict format
    if isinstance(interrupt_info, dict):
        return interrupt_info.get('value', '')
    
    # Handle string format
    if isinstance(interrupt_info, str):
        return interrupt_info
    
    # Handle direct Interrupt object
    if hasattr(interrupt_info, 'value'):
        return str(interrupt_info.value)
    
    return ""


def display_interrupt_message(interrupt_message, graph, config):
    """Display the interrupt message to user"""
    print("\n" + "=" * 80)
    print("[格式选择] 系统正在等待您的输入")
    print("=" * 80)
    
    message_to_display = None
    
    # First try to use the interrupt message directly
    if interrupt_message and interrupt_message.strip():
        message_to_display = interrupt_message
    
    # Fallback: get message from state
    if not message_to_display:
        current_state = graph.get_state(config)
        if current_state.values:
            messages = current_state.values.get("messages", [])
            if messages:
                # Find and display the last AI message
                for msg in reversed(messages):
                    if hasattr(msg, "content"):
                        msg_type = getattr(msg, "__class__", type(msg)).__name__
                        if "AI" in msg_type or "Assistant" in msg_type:
                            message_to_display = msg.content
                            break
                    elif isinstance(msg, dict):
                        role = msg.get("role", "")
                        if role in ["assistant", "ai"]:
                            message_to_display = msg.get('content', '')
                            break
    
    # Display the message
    if message_to_display:
        print(f"\n{message_to_display}")
    else:
        # Ultimate fallback: show basic instructions
        print("""
📋 可用的预设格式：
  • alpaca: Alpaca格式 - Alpaca微调格式，包含instruction、input、output字段
  • chatml: ChatML格式 - OpenAI ChatML格式，用于对话微调
  • jsonl_pt: JSONL预训练格式 - 简单的JSONL格式，每行一个text字段
  • jsonl_sft: JSONL微调格式 - JSONL格式，包含conversations字段
  • openai_finetune: OpenAI微调格式 - OpenAI官方微调API格式
  • llama2_chat: Llama2对话格式 - Meta Llama2对话格式

请选择您需要的格式：
  • 输入格式ID（如 alpaca, chatml 等）选择预设格式
  • 输入 'list' 查看所有格式的详细信息和示例
  • 或描述您需要的自定义格式""")
    
    print("\n" + "-" * 80)


# Run the subgraph with interactive handling
try:
    config = {"configurable": {"thread_id": "mapping_test_001"}}
    
    max_iterations = 20  # Prevent infinite loops
    iteration = 0
    current_input = None
    result_state = None
    
    while iteration < max_iterations:
        iteration += 1
        
        # Use stream to handle interrupts
        if current_input is not None:
            # Resume with user input
            stream_events = graph.stream(
                Command(resume=current_input),
                config=config,
                stream_mode=["updates"]
            )
            current_input = None
        else:
            # First run
            stream_events = graph.stream(state, config=config, stream_mode=["updates"])
        
        interrupted = False
        
        for event in stream_events:
            # Handle different return formats
            if isinstance(event, tuple):
                if len(event) == 3:
                    namespace_item, stream_mode, chunk_item = event
                elif len(event) == 2:
                    stream_mode, chunk_item = event
                else:
                    continue
            elif isinstance(event, dict):
                chunk_item = event
                stream_mode = "updates"
            else:
                continue
            
            # For updates mode, chunk_item is a dict with node names as keys
            if stream_mode == "updates" and isinstance(chunk_item, dict):
                for node_name, node_state in chunk_item.items():
                    if node_name == "__interrupt__":
                        # Handle interrupt
                        interrupt_message = extract_interrupt_message(node_state)
                        display_interrupt_message(interrupt_message, graph, config)
                        
                        user_input = input("请输入您的选择: ").strip()
                        if not user_input:
                            print("输入为空，将使用默认选项。")
                            current_input = ""
                        else:
                            current_input = user_input
                        interrupted = True
                        break
                    else:
                        # Normal node update
                        result_state = node_state
        
        # If no interrupt occurred, we're done
        if not interrupted:
            break
    
    # Get final state if not already set
    if result_state is None:
        final_state = graph.get_state(config)
        if final_state.values:
            result_state = final_state.values
        else:
            result_state = state
    
    print("\n" + "=" * 80)
    print("Mapping Subgraph Completed")
    print("=" * 80)
    
    # Print results
    mapping_results = result_state.get('obtainer_mapping_results', {})
    if mapping_results:
        print(f"\n📊 映射结果:")
        print(f"  总记录数: {mapping_results.get('total_records', 0)}")
        print(f"  成功映射: {mapping_results.get('mapped_records', 0)}")
        print(f"  失败记录: {mapping_results.get('failed_records', 0)}")
        print(f"  映射类型: {mapping_results.get('mapping_type', 'unknown')}")
        print(f"  输出目录: {mapping_results.get('output_dir', 'N/A')}")
        print(f"  输出文件: {mapping_results.get('output_file', 'N/A')}")
    else:
        print("\n未找到映射结果。")
    
    # Print confirmed format
    confirmed_format = result_state.get('obtainer_confirmed_format')
    if confirmed_format:
        print(f"\n📋 确认的格式:")
        print(f"  格式ID: {confirmed_format.get('format_id', 'N/A')}")
        print(f"  格式名称: {confirmed_format.get('format_name', 'N/A')}")
        print(f"  是否预设: {'是' if confirmed_format.get('is_preset') else '否'}")
        print(f"  Schema: {json.dumps(confirmed_format.get('schema', {}), ensure_ascii=False, indent=4)}")
    
    if result_state.get('exception'):
        print(f"\n⚠️ 警告: {result_state.get('exception')}")
    
    print("\n" + "=" * 80)
    print("Mapping completed!")
    print("=" * 80)

except KeyboardInterrupt:
    print("\n\n用户中断操作。")
    exit(0)
except Exception as e:
    print(f"\nError during mapping: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
