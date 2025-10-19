# %%
from loopai.graphs import StarterGraph
from loopai.graphs.Starter.tools.check_motivation import check_motivation

sg = StarterGraph(tools=[check_motivation],
                  model_name="/data1/lh/lpc/models/Qwen3-32B/",
                  base_url="http://localhost:8911/v1")

sg.init_graph()

# %%
sg("我想评估一下昨天上传的模型")

# %%
