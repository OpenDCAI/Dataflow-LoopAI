"""
数据检查节点
验证数据集格式是否符合 LlamaFactory 要求
"""

import os

from loopai.skills.Trainer.utils.stream_events import prepare_trainer_run
from loopai.schema.states import LoopAIState
from loopai.skills.Trainer.utils.data_checker import check_data_format, generate_format_report
from loopai.skills.Trainer.utils.verl_data_checker import (
    check_verl_grpo_inputs,
    generate_verl_data_report,
)
from loopai.logger import get_logger

logger = get_logger()


def data_check_node(state: LoopAIState) -> LoopAIState:
    """
    数据检查节点
    
    检查数据集格式是否符合 LlamaFactory 要求
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_input_dataset_path: 训练数据集路径
            - output_dir: 输出目录（用于保存检查报告）
    
    Returns:
        更新后的 LoopAIState 对象
    """
    
    logger.info("开始执行数据检查节点")
    # version_id 是 Trainer 单次运行的唯一 ID，其他历史字段仅作兼容别名。
    prepare_trainer_run(state)
    
    try:
        # 获取数据集路径 - 优先使用 obtainer/constructor 映射结果
        obtainer_output_file = state.get('obtainer', {}).get('mapping_results', {}).get('output_file') if state.get('obtainer', {}).get('mapping_results') else None
        constructor_output_file = state.get('constructor', {}).get('mapping_results', {}).get('output_file') if state.get('constructor', {}).get('mapping_results') else None
        
        framework = state.get('trainer', {}).get('train_framework')
        if framework == "verl":
            # Constructor/Obtainer currently produce SFT JSON. An explicitly supplied
            # RL Parquet must not be silently replaced by those artifacts.
            dataset_path = state.get('trainer', {}).get('train_input_dataset_path')
        elif obtainer_output_file and os.path.exists(obtainer_output_file):
            dataset_path = obtainer_output_file
            logger.info(f"使用 obtainer 映射结果作为训练数据集: {dataset_path}")
        elif constructor_output_file and os.path.exists(constructor_output_file):
            dataset_path = constructor_output_file
            logger.info(f"使用 constructor 映射结果作为训练数据集: {dataset_path}")
        else:
            dataset_path = state.get('trainer', {}).get('train_input_dataset_path')
        
        if not dataset_path:
            raise ValueError("缺少训练数据集路径 (train_input_dataset_path)，且 obtainer/constructor 映射结果中也未找到输出文件")
        
        # 同步更新到 trainer state，确保后续节点统一使用
        state.setdefault('trainer', {})['train_input_dataset_path'] = dataset_path
        
        logger.info(f"检查数据集: {dataset_path}")

        if framework == "llamafactory":
            # 执行数据格式检查
            check_result = check_data_format(dataset_path)
            
            # 生成检查报告
            report = generate_format_report(check_result)
            
            # 保存报告到输出目录
            output_dir = state.get('trainer', {}).get('trainer_output_dir', './output/trainer')
            os.makedirs(output_dir, exist_ok=True)
            
            report_path = os.path.join(output_dir, 'data_check_report.txt')
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report)
            
            logger.info(f"数据检查报告已保存到: {report_path}")
            
            # 更新状态
            state.setdefault('trainer', {})['trainer_data_check_result'] = check_result
            state.setdefault('trainer', {})['train_output_data_check_report_path'] = report_path
            
            # 记录检查结果
            if check_result['is_valid']:
                logger.info("✅ 数据格式检查通过")
                logger.info(f"数据集包含 {check_result['total_samples']} 个样本")
                state.setdefault('trainer', {})['trainer_data_check_passed'] = True
            else:
                logger.warning("❌ 数据格式检查未通过")
                logger.warning(f"发现 {len(check_result['errors'])} 个错误")
                for error in check_result['errors'][:5]:  # 只显示前5个错误
                    logger.warning(f"  - {error}")
                state.setdefault('trainer', {})['trainer_data_check_passed'] = False
            
            # 显示警告信息
            if check_result.get('warnings'):
                logger.warning(f"发现 {len(check_result['warnings'])} 个警告")
                for warning in check_result['warnings'][:3]:  # 只显示前3个警告
                    logger.warning(f"  - {warning}")
        elif framework == "verl":
            trainer_state = state.setdefault('trainer', {})
            if trainer_state.get('train_stage') != 'grpo':
                raise ValueError("Verl 当前只支持 GRPO")
            eval_path = trainer_state.get('train_input_eval_dataset_path')
            if not eval_path:
                raise ValueError("Verl GRPO 缺少验证 Parquet (train_input_eval_dataset_path)")

            check_result = check_verl_grpo_inputs(
                dataset_path,
                eval_path,
                trainer_state.get('verl_reward_function_path'),
                trainer_state.get('verl_reward_function_name') or 'compute_score',
                trainer_state.get('verl_reward_mode'),
                trainer_state.get('verl_reward_preset'),
                trainer_state.get('verl_reward_kwargs'),
                trainer_state.get('verl_dir'),
                trainer_state.get('verl_env_path'),
            )
            output_dir = trainer_state.get('trainer_output_dir', './output/trainer')
            os.makedirs(output_dir, exist_ok=True)
            report_path = os.path.join(output_dir, 'data_check_report.txt')
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(generate_verl_data_report(check_result))
            trainer_state['trainer_data_check_result'] = check_result
            trainer_state['train_output_data_check_report_path'] = report_path
            trainer_state['trainer_data_check_passed'] = check_result['is_valid']
            if not check_result['is_valid']:
                trainer_state['trainer_data_check_error'] = '; '.join(check_result['errors'])
                logger.warning(f"❌ Verl GRPO 数据检查失败: {trainer_state['trainer_data_check_error']}")
            else:
                logger.info("✅ Verl GRPO 数据、验证集与 reward 预检查通过")
        else:
            raise ValueError(f"未知的训练框架: {framework}")
        
    except Exception as e:
        logger.error(f"数据检查节点执行失败: {str(e)}")
        state.setdefault('trainer', {})['trainer_data_check_passed'] = False
        state.setdefault('trainer', {})['trainer_data_check_error'] = str(e)
    
    logger.info("数据检查节点执行完成")
    return state
