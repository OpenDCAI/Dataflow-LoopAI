# %%
from omegaconf import OmegaConf
from pathlib import Path
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

# Read Tavily API key from file if exists
tavily_api_key = None
tavily_api_key_file = Path(__file__).parent / 'tavily_api_key.txt'
if tavily_api_key_file.exists():
    with open(tavily_api_key_file, 'r') as f:
        tavily_api_key = f.read().strip()
        os.environ['TAVILY_API_KEY'] = tavily_api_key

sg = StarterAgent(tools=[check_motivation],
                  model_name="deepseek-v3.1-250821",
                  base_url="http://123.129.219.111:3000/v1",
                  api_key=api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
config = {"configurable": {"thread_id": "1"}}
merged_states = OmegaConf.merge(cfg.default_states, {
    'eval_batch_size': 10,
    'analyze_batch_size': 20,
    'obtainer_tavily_api_key': tavily_api_key if tavily_api_key else '',  # Tavily API key from file
})
sg.start(default_state=OmegaConf.to_container(merged_states, resolve=True), config=config)
thread_states = sg.get_state(config)

# %%
# Set to True to show detailed state info (may overwrite logs)
# Set to False to see logs clearly
SHOW_DETAILED_STATE = False

while thread_states.interrupts:
    query = input(f"Please input ({thread_states.interrupts[0].value}): ")
    
    if SHOW_DETAILED_STATE:
        # Show full state info (may overwrite logs)
        with Live(console=console, refresh_per_second=4) as live:
            for chunk in sg(query, config=config):
                live.update(Text(sg.agent_event.text(), style="cyan"))
    else:
        # Show logs clearly, only print state summary at the end
        for chunk in sg(query, config=config):
            pass
        # Print a brief state summary after execution
        event = sg.agent_event
        if event.node or event.node_path:
            console.print(f"\n[bold cyan]Execution completed[/bold cyan]")
            console.print(f"Node: {event.node or 'N/A'}")
            if event.node_path:
                console.print(f"Path: {' -> '.join(event.node_path)}")
    
    thread_states = sg.get_state(config)

console.print("[bold yellow]Done![/bold yellow]")

# %%
