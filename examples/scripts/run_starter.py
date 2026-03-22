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

# Read starter configuration
starter_model_name = getattr(cfg.starter, 'model_name', 'deepseek-chat') or 'deepseek-chat'
starter_base_url = getattr(cfg.starter, 'base_url', 'https://api.deepseek.com') or 'https://api.deepseek.com'
starter_api_key = getattr(cfg.starter, 'api_key', '') or ''
starter_tavily_api_key = getattr(cfg.starter, 'tavily_api_key', '') or ''
starter_kaggle_username = getattr(cfg.starter, 'kaggle_username', '') or ''
starter_kaggle_key = getattr(cfg.starter, 'kaggle_key', '') or ''

sg = StarterAgent(tools=[check_motivation],
                  model_name=starter_model_name,
                  base_url=starter_base_url,
                  api_key=starter_api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
# Prepare merged states
merged_states_dict = {
    'eval_batch_size': 10,
    'analyze_batch_size': 20,
}

# Handle obtainer configuration
if hasattr(cfg.default_states, 'obtainer') and cfg.default_states.obtainer:
    obtainer_cfg = cfg.default_states.obtainer
    merged_states_dict['obtainer'] = OmegaConf.to_container(obtainer_cfg, resolve=True) or {}

# Inject starter-level tavily_api_key into obtainer state (config-first, env/txt fallback in ObtainerAgent)
if starter_tavily_api_key:
    merged_states_dict.setdefault('obtainer', {})['tavily_api_key'] = starter_tavily_api_key

# Handle webcrawler tavily_api_key injection
if 'webcrawler' not in merged_states_dict:
    if hasattr(cfg.default_states, 'webcrawler') and cfg.default_states.webcrawler:
        merged_states_dict['webcrawler'] = OmegaConf.to_container(cfg.default_states.webcrawler, resolve=True) or {}
if starter_tavily_api_key:
    merged_states_dict.setdefault('webcrawler', {})['tavily_api_key'] = starter_tavily_api_key

# Handle obtainer_debug separately if it exists
if hasattr(cfg.default_states, 'obtainer_debug'):
    merged_states_dict['obtainer_debug'] = cfg.default_states.obtainer_debug

merged_states = OmegaConf.merge(cfg.default_states, merged_states_dict)

# 从状态中读取 recursion_limit，如果没有则使用较大的默认值（例如 100）
try:
    recursion_limit = merged_states.get('recursion_limit', 100)
except (AttributeError, KeyError):
    recursion_limit = getattr(merged_states, 'recursion_limit', 100)
if not recursion_limit:
    recursion_limit = 100

# LangGraph 配置：显式提高 recursion_limit
config = {
    "recursion_limit": recursion_limit,
    "configurable": {"thread_id": "1"},
}

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
