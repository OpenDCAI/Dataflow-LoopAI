import os

from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync
from loopai.skills.Analyzer.analyzer_agent import AnalyzerAgent
from loopai.memory import checkpointer, store
from rich.console import Console

console = Console()

system = load_starter_system_config_sync(prefer_db=True)
pool = StarterModelPool(system)
provider = pool.resolve_role_provider("medium")
if provider is None:
    raise RuntimeError("Analyzer example requires a Starter model-pool medium provider")

sg = AnalyzerAgent(checkpointer=checkpointer, store=store)
graph = sg()

config = {"configurable": {"thread_id": "1"}}

graph.invoke({
    "output_dir": os.getenv("OUTPUT_DIR", "./output/analyze_outputs"),

    "eval": {
        "eval_result_path": os.getenv("EVAL_RESULT_PATH", "./output/humaneval_result_dev30.jsonl"),
    },

    "analyzer": {
        "analyze_model_path": provider.model,
        "analyze_base_url": provider.base_url,
        "analyze_api_key": provider.api_key,
        "analyze_temperature": 0,
        "analyze_top_p": 0.95,
        "analyze_task_type": "code",
        "analyze_sampling_top_k": 5,
        
        "analyze_batch_size": 20,
        "output_brief": True,
        "output_suggestion": True,
        "metric_config": {
            "metrics": ["bleu", "lexical_diversity", "ngram"],
            "weights": {
                "bleu": 0.35,
                "lexical_diversity": 0.35,
                "ngram": 0.30
            }
        }
    },
}, config=config)
