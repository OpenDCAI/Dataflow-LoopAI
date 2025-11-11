"""
训练执行节点
调用 LlamaFactory 进行模型微调并集成 SwanLab 监控
"""

import os
import time
import threading
from pathlib import Path
from loopai.states.base import LoopAIState
from loopai.agents.Trainer.utils.training_executor import (
    TrainingExecutor, 
    validate_training_environment, 
    generate_training_report
)
from loopai.logger import get_logger

logger = get_logger()


def training_execution_node(state: LoopAIState) -> LoopAIState:
    """
    训练执行节点
    
    执行 LlamaFactory 训练任务并提供 SwanLab 监控
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_config_output_path: 训练配置文件路径
            - train_output_dir: 训练输出目录（可选）
            - train_use_swanlab: 是否使用 SwanLab（可选，默认 True）
            - train_swanlab_project: SwanLab 项目名称（可选）
            - output_dir: 输出目录
    
    Returns:
        更新后的 LoopAIState 对象
    """
    
    logger.info("开始执行训练节点")
    
    try:
        # 检查前置条件
        if not state.get('config_generation_success', False):
            raise ValueError("配置生成未成功，无法执行训练")
        
        config_path = state.get('train_config_output_path')
        if not config_path or not os.path.exists(config_path):
            raise ValueError(f"训练配置文件不存在: {config_path}")
        
        # 获取参数
        training_output_dir = state.get('train_output_dir', './output/training')
        use_swanlab = state.get('train_use_swanlab', True)
        swanlab_project = state.get('train_swanlab_project', 'llamafactory_training')
        
        logger.info(f"配置文件: {config_path}")
        logger.info(f"训练输出目录: {training_output_dir}")
        logger.info(f"使用 SwanLab: {use_swanlab}")
        
        # 验证训练环境
        logger.info("验证训练环境...")
        env_result = validate_training_environment()
        
        if not env_result['valid']:
            error_msg = "训练环境验证失败:\n" + "\n".join(env_result['errors'])
            raise RuntimeError(error_msg)
        
        if env_result.get('warnings'):
            logger.warning("环境检查警告:")
            for warning in env_result['warnings']:
                logger.warning(f"  - {warning}")
        
        logger.info("✅ 训练环境验证通过")
        
        # 显示环境信息
        logger.info("训练环境信息:")
        logger.info(f"  Python 版本: {env_result['python_version'].split()[0]}")
        logger.info(f"  CUDA 可用: {env_result.get('cuda_available', False)}")
        if env_result.get('cuda_device_count'):
            logger.info(f"  CUDA 设备数: {env_result['cuda_device_count']}")
        
        # 创建训练执行器
        executor = TrainingExecutor()
        
        # 开始训练
        logger.info("🚀 开始执行训练任务...")
        
        start_time = time.time()
        result = executor.execute_training(
            config_path=config_path,
            output_dir=training_output_dir,
            use_swanlab=use_swanlab,
            swanlab_project=swanlab_project
        )
        end_time = time.time()
        
        # 更新状态
        state['training_result'] = result
        state['training_execution_time'] = end_time - start_time
        
        # 生成训练报告
        report = generate_training_report(result)
        
        # 保存报告
        output_dir = state.get('output_dir', './output/trainer')
        os.makedirs(output_dir, exist_ok=True)
        
        report_path = os.path.join(output_dir, 'training_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        state['training_report_path'] = report_path
        
        # 记录结果
        if result['success']:
            logger.info("🎉 训练任务执行成功!")
            logger.info(f"训练时间: {result['training_time']:.2f} 秒")
            logger.info(f"输出目录: {result['output_dir']}")
            
            if result.get('swanlab_url'):
                logger.info(f"SwanLab 监控: {result['swanlab_url']}")
                state['swanlab_url'] = result['swanlab_url']
            
            if result.get('log_file'):
                logger.info(f"训练日志: {result['log_file']}")
                state['training_log_path'] = result['log_file']
            
            state['training_success'] = True
            
        else:
            logger.error("❌ 训练任务执行失败!")
            if result.get('error_message'):
                logger.error(f"错误信息: {result['error_message']}")
            
            state['training_success'] = False
            state['training_error'] = result.get('error_message', '未知错误')
        
        logger.info(f"训练报告已保存到: {report_path}")
        
        # 如果使用 SwanLab，启动监控线程
        if use_swanlab and result.get('swanlab_url'):
            logger.info("启动 SwanLab 监控转发...")
            _start_swanlab_monitoring(result.get('swanlab_url'), state)
        
    except Exception as e:
        logger.error(f"训练节点执行失败: {str(e)}")
        state['training_success'] = False
        state['training_error'] = str(e)
    
    logger.info("训练节点执行完成")
    return state


def _start_swanlab_monitoring(swanlab_url: str, state: LoopAIState):
    """
    启动 SwanLab 监控转发
    
    Args:
        swanlab_url: SwanLab 项目 URL
        state: 状态对象
    """
    
    def monitor_thread():
        try:
            logger.info(f"SwanLab 监控已启动: {swanlab_url}")
            
            # 这里可以添加实际的监控逻辑
            # 例如：定期检查训练状态、转发监控数据等
            
            # 示例：简单的状态更新
            state['swanlab_monitoring_active'] = True
            
            # 可以在这里添加更多的监控功能：
            # - 实时获取训练指标
            # - 转发到其他监控系统
            # - 生成训练状态报告
            # - 异常检测和报警
            
        except Exception as e:
            logger.error(f"SwanLab 监控线程异常: {str(e)}")
            state['swanlab_monitoring_error'] = str(e)
    
    # 启动监控线程
    monitor = threading.Thread(target=monitor_thread, daemon=True)
    monitor.start()


def get_training_status(state: LoopAIState) -> dict:
    """
    获取训练状态信息
    
    Args:
        state: 状态对象
    
    Returns:
        训练状态字典
    """
    
    status = {
        "data_check_passed": state.get('data_check_passed', False),
        "config_generation_success": state.get('config_generation_success', False),
        "training_success": state.get('training_success', False),
        "training_time": state.get('training_execution_time', 0),
        "swanlab_url": state.get('swanlab_url'),
        "training_log_path": state.get('training_log_path'),
        "output_dir": state.get('train_output_dir'),
        "errors": []
    }
    
    # 收集错误信息
    if state.get('data_check_error'):
        status["errors"].append(f"数据检查: {state['data_check_error']}")
    
    if state.get('config_generation_error'):
        status["errors"].append(f"配置生成: {state['config_generation_error']}")
    
    if state.get('training_error'):
        status["errors"].append(f"训练执行: {state['training_error']}")
    
    return status
