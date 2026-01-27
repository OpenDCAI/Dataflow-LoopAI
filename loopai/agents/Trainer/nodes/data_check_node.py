"""
数据检查节点
验证数据集格式是否符合 LlamaFactory 要求
"""

import os
from pathlib import Path
from loopai.schema.states import LoopAIState
from loopai.agents.Trainer.utils.data_checker import check_data_format, generate_format_report
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
    
    try:
        # 获取数据集路径
        dataset_path = state.get('train_input_dataset_path')
        if not dataset_path:
            raise ValueError("缺少训练数据集路径 (train_input_dataset_path)")
        
        logger.info(f"检查数据集: {dataset_path}")
        
        # 执行数据格式检查
        check_result = check_data_format(dataset_path)
        
        # 生成检查报告
        report = generate_format_report(check_result)
        
        # 保存报告到输出目录
        output_dir = state.get('output_dir', './output/trainer')
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
        
    except Exception as e:
        logger.error(f"数据检查节点执行失败: {str(e)}")
        state.setdefault('trainer', {})['trainer_data_check_passed'] = False
        state.setdefault('trainer', {})['trainer_data_check_error'] = str(e)
    
    logger.info("数据检查节点执行完成")
    return state
