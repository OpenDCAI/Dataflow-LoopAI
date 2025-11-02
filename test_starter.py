# %%
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from langgraph.types import Command

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
graph = sg(config=config)
thread_states = graph.get_state(config)

# %%
while thread_states.interrupts:
    query = input("请输入: ")
    print(graph.invoke(
        Command(resume=query),
        config=config
    ))
    thread_states = graph.get_state(config)

# %%
