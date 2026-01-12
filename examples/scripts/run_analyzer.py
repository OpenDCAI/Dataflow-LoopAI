from loopai.agents import AnalyzerAgent
from loopai.memory import checkpointer, store
from rich.console import Console

console = Console()

with open('api_key.txt', 'r') as f:
    api_key = f.read().strip()

sg = AnalyzerAgent(checkpointer=checkpointer, store=store)
graph = sg()

config = {"configurable": {"thread_id": "1"}}

graph.invoke({
    "output_dir": "/home/lpc/repos/Dataflow-LoopAI/output/analyze_outputs",

    "eval": {
        "eval_result_path": "/home/lpc/repos/Dataflow-LoopAI/output/humaneval_result_dev30.jsonl",
    },

    "analyzer": {
        "analyze_model_path": "/home/lpc/models/Qwen2.5-14B-Instruct/",
        "analyze_base_url": "http://127.0.0.1:8911/v1",
        "analyze_api_key": api_key,
        "analyze_temperature": 0,
        "analyze_top_p": 0.95,
        "analyze_task_type": "code",
        "analyze_sampling_top_k": 5,
        "analyze_batch_size": 20,
        "output_brief": True,
        "output_suggestion": True,
    },
}, config=config)