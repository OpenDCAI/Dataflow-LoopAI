# %%
from omegaconf import OmegaConf
import os
from datetime import datetime
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation
from loopai.logger import get_logger, add_file_handler

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

cfg = OmegaConf.load("./examples/config/starter.yaml")

with open(cfg.starter.api_key_path, 'r') as f:
    api_key = f.read().strip()

# Read Tavily API key
tavily_api_key = None
if hasattr(cfg.starter, 'tavily_api_key_path') and os.path.exists(cfg.starter.tavily_api_key_path):
    with open(cfg.starter.tavily_api_key_path, 'r') as f:
        tavily_api_key = f.read().strip()
        os.environ['TAVILY_API_KEY'] = tavily_api_key

rag_api_key = None
if hasattr(cfg, 'rag') and hasattr(cfg.rag, 'api_key_path') and os.path.exists(cfg.rag.api_key_path):
    with open(cfg.rag.api_key_path, 'r') as f:
        rag_api_key = f.read().strip()

kaggle_username = getattr(cfg.starter, 'kaggle_username', '') or ''
kaggle_key = getattr(cfg.starter, 'kaggle_key', '') or ''

sg = StarterAgent(tools=[check_motivation],
                  model_name="deepseek-chat",
                  base_url="https://api.deepseek.com",
                  api_key=api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
config = {"configurable": {"thread_id": "1"}}

# Prepare obtainer configuration from config file (nested structure)
obtainer_dict = {}

# Read from nested obtainer config if it exists
if hasattr(cfg.default_states, 'obtainer') and cfg.default_states.obtainer:
    obtainer_cfg = cfg.default_states.obtainer
    if hasattr(obtainer_cfg, 'model_path') and obtainer_cfg.model_path:
        obtainer_dict['model_path'] = obtainer_cfg.model_path
    if hasattr(obtainer_cfg, 'base_url') and obtainer_cfg.base_url:
        obtainer_dict['base_url'] = obtainer_cfg.base_url
    if hasattr(obtainer_cfg, 'api_key') and obtainer_cfg.api_key:
        obtainer_dict['api_key'] = obtainer_cfg.api_key
    else:
        obtainer_dict['api_key'] = api_key
    
    # Add other obtainer parameters from config
    if hasattr(obtainer_cfg, 'temperature'):
        obtainer_dict['temperature'] = obtainer_cfg.temperature
    if hasattr(obtainer_cfg, 'search_engine'):
        obtainer_dict['search_engine'] = obtainer_cfg.search_engine
    if hasattr(obtainer_cfg, 'max_urls'):
        obtainer_dict['max_urls'] = obtainer_cfg.max_urls
    if hasattr(obtainer_cfg, 'max_download_subtasks'):
        obtainer_dict['max_download_subtasks'] = obtainer_cfg.max_download_subtasks
    if hasattr(obtainer_cfg, 'category'):
        obtainer_dict['category'] = str(obtainer_cfg.category).upper()
    if hasattr(obtainer_cfg, 'proxy'):
        obtainer_dict['proxy'] = obtainer_cfg.proxy
    if hasattr(obtainer_cfg, 'default_mapping_format'):
        obtainer_dict['default_mapping_format'] = obtainer_cfg.default_mapping_format
    if hasattr(obtainer_cfg, 'max_exploration_depth'):
        obtainer_dict['max_exploration_depth'] = obtainer_cfg.max_exploration_depth
    if hasattr(obtainer_cfg, 'max_jina_urls'):
        obtainer_dict['max_jina_urls'] = obtainer_cfg.max_jina_urls
    if hasattr(obtainer_cfg, 'max_records_per_page'):
        obtainer_dict['max_records_per_page'] = obtainer_cfg.max_records_per_page
    if hasattr(obtainer_cfg, 'min_relevance_score'):
        obtainer_dict['min_relevance_score'] = obtainer_cfg.min_relevance_score
else:
    # Fallback: if no nested structure, use default api_key
    obtainer_dict['api_key'] = api_key

# Add Tavily and Kaggle credentials
obtainer_dict['tavily_api_key'] = tavily_api_key if tavily_api_key else ''
obtainer_dict['kaggle_username'] = kaggle_username
obtainer_dict['kaggle_key'] = kaggle_key

# RAG configuration (also nested in obtainer)
if hasattr(cfg, 'rag'):
    if hasattr(cfg.rag, 'reset'):
        obtainer_dict['reset_rag'] = cfg.rag.reset
    if hasattr(cfg.rag, 'embed_model'):
        embed_model = cfg.rag.embed_model
        if embed_model:  # Only set if not empty
            obtainer_dict['rag_embed_model'] = embed_model
    if hasattr(cfg.rag, 'collection_name'):
        obtainer_dict['rag_collection_name'] = cfg.rag.collection_name
    if hasattr(cfg.rag, 'api_base_url'):
        if cfg.rag.api_base_url:  # Only set if not empty
            obtainer_dict['rag_api_base_url'] = cfg.rag.api_base_url
    if rag_api_key:
        obtainer_dict['rag_api_key'] = rag_api_key

# Prepare merged states with nested obtainer structure
merged_states_dict = {
    'eval_batch_size': 10,
    'analyze_batch_size': 20,
}
if obtainer_dict:
    merged_states_dict['obtainer'] = obtainer_dict

# Handle obtainer_debug separately if it exists (not nested in obtainer dict)
if hasattr(cfg.default_states, 'obtainer_debug'):
    merged_states_dict['obtainer_debug'] = cfg.default_states.obtainer_debug

merged_states = OmegaConf.merge(cfg.default_states, merged_states_dict)

# 配置日志文件路径
# 从 merged_states 获取 output_dir，如果不存在则使用默认值
try:
    output_dir = merged_states.get('output_dir', './outputs')
except (AttributeError, KeyError):
    output_dir = getattr(merged_states, 'output_dir', './outputs')
log_dir = os.path.join(output_dir, 'log')
os.makedirs(log_dir, exist_ok=True)

# 生成日志文件名（带时间戳）
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_file_path = os.path.join(log_dir, f'starter_{timestamp}.log')

# 获取 logger 并添加文件 handler
logger = get_logger()
add_file_handler(logger, log_file_path)

console.print(f"[green]日志文件已启用: {log_file_path}[/green]")

sg.start(default_state=OmegaConf.to_container(merged_states, resolve=True), config=config)
thread_states = sg.get_state(config)

def render_text(lines):
    t = Text()
    if type(lines) == str:
        t.append(lines)
        return t
    for line, color in lines:
        t.append(line + '\n', style=color)
    return t

# %%
while thread_states.interrupts:
    interrupt_value = thread_states.interrupts[0].value
    
    # Display interrupt message nicely
    console.print("\n" + "=" * 80, style="cyan")
    console.print("[bold cyan][交互式输入] 系统正在等待您的输入[/bold cyan]")
    console.print("=" * 80, style="cyan")
    
    # Check if it's a long message (from MappingSubgraph)
    if len(interrupt_value) > 100:
        # Display the full message
        console.print(f"\n{interrupt_value}")
        console.print("\n" + "-" * 80, style="dim")
        query = input("请输入您的选择: ")
    else:
        # Short prompt (from query_node, etc.)
        query = input(f"Please input ({interrupt_value}): ")
    
    with Live(console=console, refresh_per_second=1) as live:
        for chunk in sg(
            query,
            config=config
        ):
            live.update(render_text(sg.agent_event.text(only_updated=True)))
            # print(chunk)
            # namespace_item, stream_mode, chunk_item = chunk
            # if stream_mode == 'updates' or stream_mode == 'custom':
            #     print(namespace_item, '⭐⭐⭐' + stream_mode + '⭐⭐⭐', chunk_item)
            # if stream_mode == 'messages':
            #     print(namespace_item, '⭐⭐⭐' + stream_mode + '⭐⭐⭐', chunk_item)
    
    # # 不使用Live显示，直接运行
    # for chunk in sg(
    #     query,
    #     config=config
    # ):
        # pass  # 不显示live输出
    
    thread_states = sg.get_state(config)

console.print("[bold yellow]Done![/bold yellow]")

# %%
