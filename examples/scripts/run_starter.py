# %%
from omegaconf import OmegaConf
import os
from datetime import datetime
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation
from loopai.logger import get_logger, add_file_handler
from loopai.schema.states import process_obtainer_config

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

cfg = OmegaConf.load("./examples/config/starter.yaml")

# Read starter API keys for fallback
starter_api_key = getattr(cfg.starter, 'api_key', '') or ''
starter_tavily_api_key = getattr(cfg.starter, 'tavily_api_key', '') or ''
starter_kaggle_username = getattr(cfg.starter, 'kaggle_username', '') or ''
starter_kaggle_key = getattr(cfg.starter, 'kaggle_key', '') or ''


sg = StarterAgent(tools=[check_motivation],
                  model_name="deepseek-chat",
                  base_url="https://api.deepseek.com",
                  api_key=starter_api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
config = {"configurable": {"thread_id": "1"}}

# Prepare starter and RAG configs for fallback
starter_config = {
    'api_key': starter_api_key,
    'tavily_api_key': starter_tavily_api_key,
    'kaggle_username': starter_kaggle_username,
    'kaggle_key': starter_kaggle_key,
}

rag_config = {}
if hasattr(cfg, 'rag') and cfg.rag:
    rag_config = OmegaConf.to_container(cfg.rag, resolve=True) or {}

# Prepare merged states with fallback logic
merged_states_dict = {
    'eval_batch_size': 10,
    'analyze_batch_size': 20,
}

# Process obtainer configuration with fallbacks
if hasattr(cfg.default_states, 'obtainer') and cfg.default_states.obtainer:
    obtainer_cfg = cfg.default_states.obtainer
    obtainer_dict = OmegaConf.to_container(obtainer_cfg, resolve=True) or {}
    merged_states_dict['obtainer'] = process_obtainer_config(
        obtainer_dict, starter_config, rag_config
    )
else:
    # Fallback: create minimal obtainer config
    merged_states_dict['obtainer'] = process_obtainer_config(
        {}, starter_config, rag_config
    )

# Handle obtainer_debug separately if it exists
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
