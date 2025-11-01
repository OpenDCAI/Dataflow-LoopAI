# %%
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

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
sg("我想评估一下昨天上传的模型", config=config)

# %%
config = {"configurable": {"thread_id": "2"}}
sg("我想训练一下刚上传的模型", config=config)

# %%
config = {"configurable": {"thread_id": "1"}}
sg("我想看看一下昨天上传的模型", config=config)

# %%
