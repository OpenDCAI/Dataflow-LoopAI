"""
verl 训练示例脚本

演示如何使用 TrainerAgent 执行 verl GRPO/PPO/SFT 训练。
使用前请确保：
1. verl 已安装并配置好环境
2. 训练数据已准备（parquet/json/jsonl 格式）
3. 模型已下载到本地
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store


def run_verl_grpo():
    """verl GRPO 训练示例"""
    trainer = TrainerAgent(checkpointer=checkpointer, store=store)

    training_state = {
        "trainer": {
            "train_framework": "verl",
            "verl_train_mode": "grpo",
            "verl_dir": "/path/to/verl",
            "verl_env_path": "/path/to/verl_venv",
            "train_input_dataset_path": "/path/to/data/gsm8k/train.parquet",
            "train_input_task_description": "数学推理任务的 GRPO 强化学习训练",
            "train_input_model_name": "Qwen/Qwen2-7B-Instruct",
            "CUDA_VISIBLE_DEVICES": "0,1,2,3,4,5,6,7",
        },
        "output_dir": "./output/verl_grpo_test",
    }

    config = {"configurable": {"thread_id": "verl_grpo_training"}}
    graph = trainer()
    result = graph.invoke(training_state, config=config)
    print(f"训练结果: {result.get('trainer', {}).get('trainer_training_success')}")


def run_verl_sft():
    """verl SFT 训练示例"""
    trainer = TrainerAgent(checkpointer=checkpointer, store=store)

    training_state = {
        "trainer": {
            "train_framework": "verl",
            "verl_train_mode": "sft",
            "verl_dir": "/path/to/verl",
            "verl_env_path": "/path/to/verl_venv",
            "train_input_dataset_path": "/path/to/data/gsm8k_sft/train.parquet",
            "train_input_task_description": "GSM8K 数学 SFT 微调",
            "train_input_model_name": "Qwen/Qwen2.5-0.5B-Instruct",
            "CUDA_VISIBLE_DEVICES": "0",
        },
        "output_dir": "./output/verl_sft_test",
    }

    config = {"configurable": {"thread_id": "verl_sft_training"}}
    graph = trainer()
    result = graph.invoke(training_state, config=config)
    print(f"训练结果: {result.get('trainer', {}).get('trainer_training_success')}")


def run_verl_with_custom_script():
    """使用自定义脚本的 verl 训练示例"""
    trainer = TrainerAgent(checkpointer=checkpointer, store=store)

    training_state = {
        "trainer": {
            "train_framework": "verl",
            "verl_train_mode": "grpo",
            "verl_dir": "/path/to/verl",
            "train_input_dataset_path": "/path/to/data/train.parquet",
            "train_input_task_description": "自定义 GRPO 训练",
            "train_input_model_name": "Qwen/Qwen2-7B-Instruct",
            # 提供自定义脚本，跳过自动配置生成
            "train_input_config_template_path": "/path/to/my_custom_train.sh",
        },
        "output_dir": "./output/verl_custom_test",
    }

    config = {"configurable": {"thread_id": "verl_custom_training"}}
    graph = trainer()
    result = graph.invoke(training_state, config=config)
    print(f"训练结果: {result.get('trainer', {}).get('trainer_training_success')}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="verl Trainer 示例")
    parser.add_argument("--mode", choices=["grpo", "sft", "custom"], default="grpo")
    args = parser.parse_args()

    if args.mode == "grpo":
        run_verl_grpo()
    elif args.mode == "sft":
        run_verl_sft()
    elif args.mode == "custom":
        run_verl_with_custom_script()
