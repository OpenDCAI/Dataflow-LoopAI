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

graph.invoke({"judger":{
    'eval_model_path': '/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/',
    'eval_base_url': 'http://127.0.0.1:8911/v1',
    'eval_api_key': "EMPTY",
    'eval_temperature': 0.7,
    'eval_top_p': 0.95,
    'eval_task_type': 'code',
    'eval_test_case_path': '/root/brjverl/dataflow/examples/scripts/sample/test_format.jsonl',
    'eval_problem_path': '/root/brjverl/dataflow/examples/scripts/data/test_no_format.jsonl',
    'eval_problem_format_path': '/root/brjverl/dataflow/examples/scripts/data/test_format.jsonl',
    'eval_format_type': 'human-eval',
    'eval_result_path': '/root/brjverl/dataflow/examples/scripts/result/test_format.jsonl',
    'eval_batch_size': 10,
}}, config=config)

# %%
