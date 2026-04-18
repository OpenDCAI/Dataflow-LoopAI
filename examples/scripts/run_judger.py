#!/usr/bin/env python
# -*- coding: utf-8 -*-
import torch
from multiprocessing import freeze_support
from loopai.agents import JudgerAgent
from loopai.memory import checkpointer, store
from rich.console import Console

console = Console()

def main():
    sg = JudgerAgent(checkpointer=checkpointer, store=store)
    config = {"configurable": {"thread_id": "1"}}

    graph = sg()

    key_mapping = {
        "input_question_key": "question",
        "input_target_key": "answer"
    }

    result = graph.invoke({
        "judger": {
            'eval_model_path': '/data/laipeichao/Qwen2.5-7B-Instruct/',
            'eval_base_url': '',
            'eval_api_key': "EMPTY",
            'eval_temperature': 0,
            'eval_top_p': 0.95,
            'eval_task_type': 'general_text',
            'eval_problem_path': '',
            'eval_format_type': '',
            'eval_batch_size': 4,
            'eval_case_num': 1,
            'eval_vllm_port': 8911,
            'eval_vllm_env_path': '/home/laipeichao/miniconda3/envs/zx312/bin/python',
            'tensor_parallel_size': 1,
            'eval_vllm_gpu_memory_utilization': 0.9,
            'eval_env_configs': '{"CUDA_VISIBLE_DEVICES": "5","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}',
            'cuda_visible_devices': '5',
            'key_mapping': key_mapping,
            'bench_name': 'gsm8k',
            'bench_dataflow_eval_type': 'key2_qa'
        },
        "task_id": '20260002',
        'output_dir': '../output'
    }, config=config)
    
    print("Evaluation completed successfully!")
    return result

if __name__ == '__main__':
    freeze_support()
    main()