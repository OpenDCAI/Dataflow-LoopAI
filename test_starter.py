# %%
from loopai.agents import StarterAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

sg = StarterAgent(tools=[check_motivation],
                  model_name="/data1/lh/lpc/models/Qwen3-32B/",
                  base_url="http://localhost:8911/v1",
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
