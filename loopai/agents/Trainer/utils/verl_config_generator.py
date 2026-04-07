"""
verl 配置生成工具
根据训练模式和任务描述自动生成 verl 训练 shell 脚本
"""

import os
import re
from pathlib import Path
from typing import Dict, Any, Optional
from loopai.logger import get_logger

logger = get_logger()

# verl 训练模式到模板文件的映射
VERL_TEMPLATE_MAP = {
    "grpo": "verl_grpo.sh",
    "ppo": "verl_ppo.sh",
    "sft": "verl_sft.sh",
}

# 默认参数值（按训练模式分组）
DEFAULT_PARAMS = {
    "grpo": {
        "TRAIN_BATCH_SIZE": "1024",
        "MAX_PROMPT_LENGTH": "1024",
        "MAX_RESPONSE_LENGTH": "1024",
        "LEARNING_RATE": "1e-6",
        "PPO_MINI_BATCH_SIZE": "256",
        "PPO_MICRO_BATCH_SIZE": "16",
        "TENSOR_PARALLEL_SIZE": "2",
        "ROLLOUT_N": "5",
        "N_GPUS_PER_NODE": "8",
        "NNODES": "1",
        "SAVE_FREQ": "20",
        "TEST_FREQ": "5",
        "TOTAL_EPOCHS": "15",
    },
    "ppo": {
        "TRAIN_BATCH_SIZE": "1024",
        "MAX_PROMPT_LENGTH": "512",
        "MAX_RESPONSE_LENGTH": "512",
        "LEARNING_RATE": "1e-6",
        "CRITIC_LR": "1e-5",
        "PPO_MINI_BATCH_SIZE": "256",
        "PPO_MICRO_BATCH_SIZE": "16",
        "TENSOR_PARALLEL_SIZE": "4",
        "N_GPUS_PER_NODE": "8",
        "NNODES": "1",
        "SAVE_FREQ": "20",
        "TEST_FREQ": "1",
        "TOTAL_EPOCHS": "15",
    },
    "sft": {
        "TRAIN_BATCH_SIZE": "128",
        "MAX_TOKEN_LEN_PER_GPU": "8192",
        "MAX_LENGTH": "1024",
        "LEARNING_RATE": "1e-5",
        "N_GPUS_PER_NODE": "1",
        "NNODES": "1",
        "SAVE_FREQ": "-1",
        "TEST_FREQ": "-1",
        "TOTAL_EPOCHS": "4",
    },
}


class VerlConfigGenerator:
    """verl 训练脚本生成器"""

    def __init__(self):
        self.templates_dir = Path(__file__).parent.parent / "templates"

    def generate_script(
        self,
        train_mode: str,
        model_path: str,
        train_files: str,
        val_files: str = "",
        output_dir: str = "./checkpoints/verl",
        task_description: str = "",
        project_name: str = "",
        experiment_name: str = "",
        extra_params: Optional[Dict[str, str]] = None,
        template_path: Optional[str] = None,
    ) -> str:
        """
        生成 verl 训练脚本

        Args:
            train_mode: 训练模式 (grpo/ppo/sft)
            model_path: 模型路径
            train_files: 训练数据文件路径
            val_files: 验证数据文件路径
            output_dir: 输出目录
            task_description: 任务描述（用于自动调参）
            project_name: 项目名称
            experiment_name: 实验名称
            extra_params: 额外参数覆盖
            template_path: 自定义模板路径

        Returns:
            生成的 shell 脚本内容
        """
        if train_mode not in VERL_TEMPLATE_MAP:
            raise ValueError(f"不支持的训练模式: {train_mode}，支持: {list(VERL_TEMPLATE_MAP.keys())}")

        # 加载模板
        if template_path and os.path.exists(template_path):
            with open(template_path, 'r') as f:
                script = f.read()
            logger.info(f"使用自定义模板: {template_path}")
        else:
            tpl_path = self.templates_dir / VERL_TEMPLATE_MAP[train_mode]
            if not tpl_path.exists():
                raise FileNotFoundError(f"模板文件不存在: {tpl_path}")
            with open(tpl_path, 'r') as f:
                script = f.read()
            logger.info(f"使用默认 {train_mode} 模板: {tpl_path}")

        # 构建参数
        params = dict(DEFAULT_PARAMS.get(train_mode, {}))

        # 基于任务描述自动调参
        if task_description:
            auto_params = self._auto_tune_params(train_mode, task_description)
            params.update(auto_params)

        # 必填参数
        params["MODEL_PATH"] = model_path
        params["TRAIN_FILES"] = train_files
        if val_files:
            params["VAL_FILES"] = val_files
        else:
            params["VAL_FILES"] = train_files  # verl 要求提供 val_files

        params["OUTPUT_DIR"] = output_dir
        if project_name:
            params["PROJECT_NAME"] = project_name
        if experiment_name:
            params["EXPERIMENT_NAME"] = experiment_name

        # PPO 模式默认 critic 模型与 actor 相同
        if train_mode == "ppo" and "CRITIC_MODEL_PATH" not in params:
            params["CRITIC_MODEL_PATH"] = model_path

        # 合并额外参数
        if extra_params:
            params.update(extra_params)

        # 生成导出变量的脚本头
        header_lines = ["#!/bin/bash", "set -x", ""]
        for key, value in sorted(params.items()):
            header_lines.append(f'export {key}="{value}"')
        header_lines.append("")

        # 将模板中的 set -x 去掉（header 已包含），并拼接
        script = script.replace("set -x\n", "", 1)

        full_script = "\n".join(header_lines) + script

        logger.info(f"verl {train_mode} 训练脚本生成完成")
        return full_script

    def save_script(self, script_content: str, output_path: str) -> bool:
        """保存生成的脚本到文件"""
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
            os.chmod(output_path, 0o755)
            logger.info(f"训练脚本已保存到: {output_path}")
            return True
        except Exception as e:
            logger.error(f"保存训练脚本失败: {e}")
            return False

    def _auto_tune_params(self, train_mode: str, task_description: str) -> Dict[str, str]:
        """基于任务描述自动调整参数"""
        params = {}
        desc = task_description.lower()

        if train_mode in ("grpo", "ppo"):
            # 数学/推理任务: 更长的响应长度，更低的学习率
            if any(kw in desc for kw in ["数学", "math", "推理", "reasoning", "代码", "code"]):
                params["MAX_RESPONSE_LENGTH"] = "2048"
                params["LEARNING_RATE"] = "5e-7"
                params["TOTAL_EPOCHS"] = "20"
                logger.info("检测到数学/推理任务，调整参数: 响应长度2048, lr=5e-7")

            # 对话任务
            elif any(kw in desc for kw in ["对话", "chat", "聊天", "conversation"]):
                params["MAX_RESPONSE_LENGTH"] = "512"
                params["ROLLOUT_N"] = "3"
                logger.info("检测到对话任务，调整参数: 响应长度512, rollout_n=3")

        elif train_mode == "sft":
            if any(kw in desc for kw in ["长文本", "long", "代码", "code"]):
                params["MAX_LENGTH"] = "4096"
                params["MAX_TOKEN_LEN_PER_GPU"] = "16384"
                logger.info("检测到长文本/代码任务，调整参数: max_length=4096")

        return params


def generate_verl_config_explanation(train_mode: str, params: Dict[str, str], task_description: str) -> str:
    """生成 verl 配置说明文档"""
    lines = [
        "=" * 60,
        f"verl {train_mode.upper()} 训练配置说明",
        "=" * 60,
        "",
        f"任务描述: {task_description}",
        f"训练模式: {train_mode}",
        "",
        "主要配置参数:",
        f"  模型路径: {params.get('MODEL_PATH', 'N/A')}",
        f"  训练数据: {params.get('TRAIN_FILES', 'N/A')}",
        f"  学习率: {params.get('LEARNING_RATE', 'N/A')}",
        f"  训练轮数: {params.get('TOTAL_EPOCHS', 'N/A')}",
        f"  GPU 数量: {params.get('N_GPUS_PER_NODE', 'N/A')}",
        f"  节点数: {params.get('NNODES', 'N/A')}",
        "",
    ]

    if train_mode in ("grpo", "ppo"):
        lines.extend([
            "RL 训练参数:",
            f"  Batch Size: {params.get('TRAIN_BATCH_SIZE', 'N/A')}",
            f"  最大 Prompt 长度: {params.get('MAX_PROMPT_LENGTH', 'N/A')}",
            f"  最大响应长度: {params.get('MAX_RESPONSE_LENGTH', 'N/A')}",
            f"  TP 并行度: {params.get('TENSOR_PARALLEL_SIZE', 'N/A')}",
            "",
        ])
        if train_mode == "grpo":
            lines.append(f"  Rollout 采样数: {params.get('ROLLOUT_N', 'N/A')}")
        elif train_mode == "ppo":
            lines.append(f"  Critic 学习率: {params.get('CRITIC_LR', 'N/A')}")
    else:
        lines.extend([
            "SFT 训练参数:",
            f"  Batch Size: {params.get('TRAIN_BATCH_SIZE', 'N/A')}",
            f"  最大序列长度: {params.get('MAX_LENGTH', 'N/A')}",
            "",
        ])

    return "\n".join(lines)
