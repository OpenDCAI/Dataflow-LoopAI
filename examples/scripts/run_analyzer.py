# %%
from loopai.agents import AnalyzerAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

with open('api_key.txt', 'r') as f:
    api_key = f.read().strip()

sg = AnalyzerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "1"}}

# %%
graph = sg()

graph.invoke({
    'analyze_model_path': '/home/lpc/models/Qwen2.5-14B-Instruct/',
    'analyze_base_url': 'http://127.0.0.1:8911/v1',
    'analyze_api_key': api_key,
    'analyze_temperature': 0,
    'analyze_top_p': 0.95,
    'output_dir': '/home/lpc/repos/Dataflow-LoopAI/output/analyze_outputs',
    'output_brief': True,
    'analyze_task_type': 'code',
    'eval_result_path': '/home/lpc/repos/Dataflow-LoopAI/output/humaneval_result_dev30.jsonl',
    'analyze_sampling_top_k': 5,
    'output_brief': True,
    'output_suggestion': True,
    'analyze_batch_size': 20,
}, config=config)

# %%
