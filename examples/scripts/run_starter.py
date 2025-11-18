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
while thread_states.interrupts:
    query = input(f"Please input ({thread_states.interrupts[0].value}): ")
    
    with Live(console=console, refresh_per_second=4) as live:
        for chunk in sg(
            query,
            config=config
        ):
            # Build display text with state and custom events
            display_lines = []
            
            # Add state information
            state_text = sg.agent_event.text()
            display_lines.append(state_text)
            
            # Add all custom events (from all agents)
            all_custom_info = sg.agent_event.get_custom_info()
            
            if all_custom_info:
                display_lines.append("\n" + "="*10 + "Custom Events" + "="*10)
                
                # Helper function to format a single custom event
                def format_custom_event(event, event_key=None):
                    """Format a single custom event for display"""
                    if not isinstance(event, dict):
                        return None
                    
                    event_lines = []
                    if event_key:
                        event_lines.append(f"[{event_key}]")
                    if event.get('current'):
                        event_lines.append(f"Current: {event['current']}")
                    if event.get('message'):
                        event_lines.append(f"Message: {event['message']}")
                    if event.get('progress') is not None:
                        progress = event.get('progress', 0)
                        progress_num = event.get('progress_num', 0)
                        total = event.get('total', 0)
                        if total > 0:
                            event_lines.append(f"Progress: {progress_num}/{total} ({progress*100:.1f}%)")
                    if event.get('data'):
                        data = event['data']
                        if isinstance(data, dict):
                            data_lines = []
                            for k, v in data.items():
                                if isinstance(v, (str, int, float, bool)):
                                    # Truncate long strings
                                    if isinstance(v, str) and len(v) > 100:
                                        v = v[:100] + "..."
                                    data_lines.append(f"  {k}: {v}")
                                elif isinstance(v, list) and len(v) > 0:
                                    data_lines.append(f"  {k}: {len(v)} items")
                                elif v is not None:
                                    data_lines.append(f"  {k}: {type(v).__name__}")
                            if data_lines:
                                event_lines.append("Data:")
                                event_lines.extend(data_lines)
                    
                    return "\n".join(event_lines) if event_lines else None
                
                # Collect all events with their keys
                all_events = []
                for key, events in all_custom_info.items():
                    for event in events:
                        formatted = format_custom_event(event, key)
                        if formatted:
                            all_events.append(formatted)
                
                # Show only the most recent events (last 10 to avoid clutter)
                if all_events:
                    recent_events = all_events[-10:]
                    for formatted_event in recent_events:
                        display_lines.append(formatted_event)
                        display_lines.append("-" * 40)
            
            # Update live display
            display_text = "\n".join(display_lines)
            live.update(Text(display_text, style="cyan"))
    
    thread_states = sg.get_state(config)

console.print("[bold yellow]Done![/bold yellow]")

# %%
