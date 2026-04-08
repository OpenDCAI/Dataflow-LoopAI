# %%
from loopai.agents import JudgerAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

# with open('api_key.txt', 'r') as f:
#     api_key = f.read().strip()

sg = JudgerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "1"}}

# %%
graph = sg()

graph.invoke({
    "judger":{
        'eval_model_path': '/root/brjverl/models/sft1000/',
        # 'eval_base_url': 'http://127.0.0.1:8911/v1',
        'eval_base_url': '',
        'eval_api_key': "EMPTY",
        'eval_temperature': 0,
        'eval_top_p': 0.95,
        'eval_task_type': 'text2sql',
        'eval_problem_path': '/root/brjverl/dataflow/examples/scripts/data/dev_bird_for_oj.jsonl',
        'eval_format_type': '',
        'eval_text2sql_dir': '/root/brjverl/dataflow/examples/scripts/database/',
        'eval_batch_size': 20,
        'eval_case_num': 1,
        'eval_vllm_port': 8911,
        'eval_vllm_env_path': '/root/miniconda3/envs/loopai/bin/python3',
        'eval_vllm_tensor_parallel_size': 2,
        'eval_vllm_gpu_memory_utilization': 0.9,
        'eval_env_configs':'{"CUDA_VISIBLE_DEVICES": "0,1","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}'
    },
    "trainer":{
        "trainer_task_id":"ceshi"
    },
    "task_id": 20260002,
    'output_dir': '/root/brjverl/dataflow/examples/scripts/output/',
}, config=config)

# %%
