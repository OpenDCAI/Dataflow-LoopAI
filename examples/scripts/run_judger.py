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
    "judger":{
        'eval_model_path': '/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/',
        'eval_base_url': 'http://127.0.0.1:8911/v1',
        'eval_api_key': "EMPTY",
        'eval_temperature': 0.7,
        'eval_top_p': 0.95,
        'eval_task_type': 'code',
        'eval_problem_path': '/root/brjverl/dataflow/examples/scripts/data/human-eval-v2-20210705.jsonl',
        'eval_format_type': 'human-eval',
        'eval_text2sql_dir': '/root/brjverl/dataflow/examples/scripts/database/',
        'output_dir': '/root/brjverl/dataflow/examples/scripts/output/',
        'eval_batch_size': 10,
        'eval_case_num': 1
    },
    "task_id": 10000,
}, config=config)

# %%
