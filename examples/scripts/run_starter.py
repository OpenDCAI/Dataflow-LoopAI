# %%
from omegaconf import OmegaConf
import os
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

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
                  model_name="gpt-4o",
                  base_url="http://123.129.219.111:3000/v1",
                  api_key=api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
config = {"configurable": {"thread_id": "1"}}

# Prepare obtainer configuration from config file
obtainer_config = {}
if cfg.default_states.get('obtainer_model_path'):
    obtainer_config['obtainer_model_path'] = cfg.default_states.obtainer_model_path
if cfg.default_states.get('obtainer_base_url'):
    obtainer_config['obtainer_base_url'] = cfg.default_states.obtainer_base_url
if cfg.default_states.get('obtainer_api_key'):
    obtainer_config['obtainer_api_key'] = cfg.default_states.obtainer_api_key
else:
    obtainer_config['obtainer_api_key'] = api_key

# Add other obtainer parameters from config
if 'obtainer_temperature' in cfg.default_states:
    obtainer_config['obtainer_temperature'] = cfg.default_states.obtainer_temperature
if 'obtainer_search_engine' in cfg.default_states:
    obtainer_config['obtainer_search_engine'] = cfg.default_states.obtainer_search_engine
if 'obtainer_max_urls' in cfg.default_states:
    obtainer_config['obtainer_max_urls'] = cfg.default_states.obtainer_max_urls
if 'obtainer_max_download_subtasks' in cfg.default_states:
    obtainer_config['obtainer_max_download_subtasks'] = cfg.default_states.obtainer_max_download_subtasks
if 'obtainer_category' in cfg.default_states:
    obtainer_config['obtainer_category'] = cfg.default_states.obtainer_category.upper()
if 'obtainer_debug' in cfg.default_states:
    obtainer_config['obtainer_debug'] = cfg.default_states.obtainer_debug

obtainer_config['obtainer_tavily_api_key'] = tavily_api_key if tavily_api_key else ''
obtainer_config['obtainer_kaggle_username'] = kaggle_username
obtainer_config['obtainer_kaggle_key'] = kaggle_key

rag_config = {}
if hasattr(cfg, 'rag'):
    if hasattr(cfg.rag, 'reset'):
        rag_config['obtainer_reset_rag'] = cfg.rag.reset
    if hasattr(cfg.rag, 'embed_model'):
        embed_model = cfg.rag.embed_model
        if embed_model:  # Only set if not empty
            rag_config['obtainer_rag_embed_model'] = embed_model
    if hasattr(cfg.rag, 'collection_name'):
        rag_config['obtainer_rag_collection_name'] = cfg.rag.collection_name
    if hasattr(cfg.rag, 'api_base_url'):
        if cfg.rag.api_base_url:  # Only set if not empty
            rag_config['obtainer_rag_api_base_url'] = cfg.rag.api_base_url
    if rag_api_key:
        rag_config['obtainer_rag_api_key'] = rag_api_key

merged_states = OmegaConf.merge(cfg.default_states, {
    'eval_batch_size': 10,
    'analyze_batch_size': 20,
    **obtainer_config,
    **rag_config
})
sg.start(default_state=OmegaConf.to_container(merged_states, resolve=True), config=config)
thread_states = sg.get_state(config)

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
    
    with Live(console=console, refresh_per_second=4) as live:
        for chunk in sg(
            query,
            config=config
        ):
            live.update(Text(sg.agent_event.text(), style="cyan"))
    
    # # 不使用Live显示，直接运行
    # for chunk in sg(
    #     query,
    #     config=config
    # ):
        pass  # 不显示live输出
    
    thread_states = sg.get_state(config)

console.print("[bold yellow]Done![/bold yellow]")

# %%
