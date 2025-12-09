# %%
from loopai.agents import JudgerAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

with open('api_key.txt', 'r') as f:
    api_key = f.read().strip()

sg = JudgerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "1"}}

# %%
graph = sg()

graph.invoke({
    'eval_model_path': '/home/lpc/models/glm-4-9b-chat/',
    'eval_base_url': 'http://127.0.0.1:8911/v1',
    'eval_api_key': api_key,
    'eval_temperature': 0,
    'eval_top_p': 0.95,
    'eval_test_case_path': '/home/lpc/repos/Dataflow-LoopAI/output/test.json',
    'eval_problem_path': '/home/lpc/repos/Dataflow-LoopAI/data/human-eval-v2-20210705.jsonl',
    'eval_result_path': '/home/lpc/repos/Dataflow-LoopAI/output/result.json',
    'eval_batch_size': 10,
}, config=config)

# %%
