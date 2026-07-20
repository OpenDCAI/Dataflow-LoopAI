"""
配置生成节点
根据任务描述生成 LlamaFactory 训练配置（YAML格式）
"""

import os
import yaml
from pathlib import Path
from loopai.schema.states import LoopAIState
from loopai.skills.Trainer.utils.config_generator import ConfigGenerator, generate_config_explanation
from loopai.skills.Trainer.utils.verl_config_generator import (
    generate_verl_config_explanation,
    generate_verl_grpo_config,
    save_verl_grpo_config,
)
from loopai.logger import get_logger

logger = get_logger()


def config_generation_node(state: LoopAIState) -> LoopAIState:
    """
    配置生成节点
    
    根据任务描述和数据集信息生成合理的 LlamaFactory 训练配置
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_input_task_description: 训练任务描述
            - train_input_dataset_path: 训练数据集路径
            - train_input_model_name: 基础模型名称（可选，默认 qwen2.5-7b-instruct）
            - train_input_config_template_path: 配置模板路径（可选）
            - output_dir: 输出目录
    
    Returns:
        更新后的 LoopAIState 对象
    """
    
    logger.info("开始执行配置生成节点")
    
    try:
        # 检查数据检查是否通过
        if not state.get('trainer', {}).get('trainer_data_check_passed', False):
            raise ValueError("数据格式检查未通过，无法生成配置")
        
        # 获取必要参数
        task_description = state.get('trainer', {}).get('train_input_task_description')
        if not task_description:
            raise ValueError("缺少训练任务描述 (train_input_task_description)")
        
        dataset_path = state.get('trainer', {}).get('train_input_dataset_path')
        if not dataset_path:
            raise ValueError("缺少训练数据集路径 (train_input_dataset_path)")
        
        logger.info(f"任务描述: {task_description}")
        logger.info(f"数据集路径: {dataset_path}")
        
        # 获取可选参数
        model_name = state.get('trainer', {}).get('train_input_model_name', 'qwen2.5-7b-instruct')
        template_path = state.get('trainer', {}).get('train_input_config_template_path')
        training_output_dir = os.path.abspath(
            state.get('trainer', {}).get('output_dir') or './output/training'
        )
        
        framework = state.get('trainer', {}).get('train_framework')

        if framework == 'llamafactory':
            # 创建配置生成器
            generator = ConfigGenerator()
            
            # 生成配置
            logger.info("正在生成训练配置...")
            config = generator.generate_config(
                task_description=task_description,
                dataset_path=dataset_path,
                model_name=model_name,
                output_dir=training_output_dir,
                template_path=template_path,
            )
            config["output_dir"] = os.path.abspath(str(config.get("output_dir") or training_output_dir))

            deepspeed_path = config.get("deepspeed")
            if deepspeed_path and not os.path.exists(str(deepspeed_path)):
                llamafactory_dir = (
                    state.get('trainer', {}).get('llamafactory_dir')
                    or state.get('system', {}).get('llamafactory_dir')
                    or ''
                )
                candidate = os.path.join(
                    llamafactory_dir,
                    "examples",
                    "deepspeed",
                    os.path.basename(str(deepspeed_path)),
                )
                if llamafactory_dir and os.path.exists(candidate):
                    config["deepspeed"] = os.path.abspath(candidate)
                    logger.info(f"已修正 deepspeed 配置路径: {config['deepspeed']}")
                else:
                    config.pop("deepspeed", None)
                    logger.warning(f"deepspeed 配置不存在，已移除: {deepspeed_path}")
            
            # 确保输出目录存在
            output_dir = os.path.abspath(state.get('trainer', {}).get('output_dir') or './output/trainer')
            os.makedirs(output_dir, exist_ok=True)
            # 保存配置文件为YAML格式
            config_output_path = state.get('trainer', {}).get('train_output_config_path')
            if not config_output_path:
                config_output_path = os.path.join(output_dir, 'training_config.yaml')
            
            # 保存为YAML格式
            success = generator.save_config_as_yaml(config, config_output_path)
            if not success:
                raise RuntimeError("保存YAML配置文件失败")
            
            # 生成配置说明文档
            explanation = generate_config_explanation(config, task_description)
            explanation_path = os.path.join(output_dir, 'config_explanation.txt')
            with open(explanation_path, 'w', encoding='utf-8') as f:
                f.write(explanation)
            
            # 更新状态
            state.setdefault('trainer', {})['train_config'] = config
            state.setdefault('trainer', {})['train_output_config_path'] = config_output_path
            state.setdefault('trainer', {})['trainer_config_explanation_path'] = explanation_path
            state.setdefault('trainer', {})['trainer_config_generation_success'] = True
            
            logger.info("✅ 配置生成成功")
            logger.info(f"配置文件保存至: {config_output_path}")
            logger.info(f"配置说明保存至: {explanation_path}")
            
            # 显示关键配置信息
            logger.info("关键配置信息:")
            logger.info(f"  模型名称: {config.get('model_name')}")
            logger.info(f"  微调类型: {config.get('finetuning_type')}")
            logger.info(f"  学习率: {config.get('learning_rate')}")
            logger.info(f"  训练轮数: {config.get('num_train_epochs')}")
            logger.info(f"  批次大小: {config.get('per_device_train_batch_size')}")
            
            if config.get('finetuning_type') == 'lora':
                logger.info(f"  LoRA Rank: {config.get('lora_r')}")
                logger.info(f"  LoRA Alpha: {config.get('lora_alpha')}")
            
        elif framework == 'verl':
            stage = state.get('trainer', {}).get('train_stage')
            if stage != 'grpo':
                raise ValueError(f"Verl 当前只支持 GRPO，收到 train_stage={stage}")

            template_path = state.get('trainer', {}).get('train_input_config_template_path')
            if not template_path or not os.path.exists(template_path):
                raise ValueError("Verl GRPO 需要有效的 YAML 模板路径 (train_input_config_template_path)")

            output_dir = os.path.abspath(
                state.get('trainer', {}).get('trainer_output_dir')
                or state.get('trainer', {}).get('output_dir')
                or './output/trainer'
            )
            os.makedirs(output_dir, exist_ok=True)
            config_output_path = state.get('trainer', {}).get('train_output_config_path')
            if not config_output_path or Path(str(config_output_path)).suffix.lower() not in {'.yaml', '.yml'}:
                config_output_path = os.path.join(output_dir, 'training_config.yaml')

            config = generate_verl_grpo_config(state, template_path)
            config_output_path = save_verl_grpo_config(config, config_output_path)
            explanation_path = os.path.join(output_dir, 'config_explanation.txt')
            with open(explanation_path, 'w', encoding='utf-8') as f:
                f.write(generate_verl_config_explanation(config))

            trainer_state = state.setdefault('trainer', {})
            trainer_state['train_config'] = config
            trainer_state['train_output_config_path'] = config_output_path
            trainer_state['trainer_config_explanation_path'] = explanation_path
            trainer_state['trainer_config_generation_success'] = True
            logger.info(f"✅ Verl GRPO YAML 已生成: {config_output_path}")
        else:
            raise ValueError(f"未知的训练框架: {framework}")
        
    except Exception as e:
        logger.error(f"配置生成节点执行失败: {str(e)}")
        state.setdefault('trainer', {})['trainer_config_generation_success'] = False
        state.setdefault('trainer', {})['trainer_config_generation_error'] = str(e)
    
    logger.info("配置生成节点执行完成")
    return state
