# %%
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

with open('api_key.txt', 'r') as f:
    api_key = f.read().strip()

sg = StarterAgent(tools=[check_motivation],
                  model_name="deepseek-chat",
                  base_url="https://api.deepseek.com",
                  api_key=api_key,
                  checkpointer=checkpointer,
                  store=store)

sg.init_graph()

# %%
config = {"configurable": {"thread_id": "1"}}
sg.start(config=config)
thread_states = sg.get_state(config)

# %%
while thread_states.interrupts:
    query = input("请输入: ")
    with Live(console=console, refresh_per_second=4) as live:
        for chunk in sg(
            query,
            config=config
        ):
            live.update(Text(sg.agent_event.text(), style="cyan"))
    # for chunk in sg(
    #         query,
    #         config=config
    #     ):
    #     print(chunk)
    # thread_states = sg.get_state(config)

console.print("[bold yellow]Done![/bold yellow]")

# %%
