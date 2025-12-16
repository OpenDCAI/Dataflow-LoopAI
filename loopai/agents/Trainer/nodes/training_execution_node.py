"""
训练执行节点
调用远程训练服务进行模型微调
"""

import os
import time
from pathlib import Path
from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.agents.Trainer.utils.training_service_client import create_training_client
from loopai.logger import get_logger

logger = get_logger()


def training_execution_node(state: LoopAIState, writer=None) -> LoopAIState:
    """
    训练执行节点
    
    调用远程训练服务执行模型微调任务
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_config_output_path: YAML配置文件路径
            - train_task_description: 训练任务描述
            - training_service_url: 训练服务地址（可选）
            - output_dir: 输出目录
        writer: StreamEvent writer，可选
    
    Returns:
        更新后的 LoopAIState 对象
    """    
    if writer is None:
        writer = get_stream_writer()
        
    logger.info("开始执行训练节点")
    
    try:
        # 检查前置条件
        if not state.get('config_generation_success', False):
            raise ValueError("配置生成未成功，无法执行训练")
        
        config_path = state.get('train_config_output_path')
        if not config_path or not os.path.exists(config_path):
            raise ValueError(f"YAML配置文件不存在: {config_path}")
        
        # 获取参数
        task_description = state.get('train_task_description', '未指定任务描述')
        service_url = state.get('training_service_url', 'http://localhost:8000')
        
        logger.info(f"YAML配置文件: {config_path}")
        logger.info(f"训练服务地址: {service_url}")
        logger.info(f"任务描述: {task_description}")
        
        # 进度：开始连接服务
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.1,
                message="正在连接训练服务...",
                data={"service_url": service_url, "config_path": config_path}
            ))
        
        # 创建训练服务客户端
        logger.info("连接训练服务...")
        client = create_training_client(service_url)
        
        # 检查服务健康状态
        if not client.check_service_health():
            raise RuntimeError(f"训练服务不可用: {service_url}")
        
        logger.info("✅ 训练服务连接成功")
        
        # 进度：服务连接成功
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.2,
                message="训练服务连接成功，准备提交任务...",
                data={"service_status": "connected"}
            ))        
        # 启动训练任务
        logger.info("🚀 提交训练任务到远程服务...")
        
        # 进度：正在提交任务
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.3,
                message="正在提交训练任务到远程服务...",
                data={"task_description": task_description}
            ))
        
        start_time = time.time()
        success, task_id_or_error, error_detail = client.start_training(
            yaml_config_path=config_path,
            task_name=f"trainer_agent_{int(start_time)}"
        )
        
        if not success:
            raise RuntimeError(f"启动训练任务失败: {task_id_or_error}")
        
        task_id = task_id_or_error
        logger.info(f"✅ 训练任务启动成功，任务ID: {task_id}")
        
        # 进度：任务提交成功
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.4,
                message=f"训练任务提交成功，任务ID: {task_id}",
                data={"task_id": task_id, "start_time": start_time}
            ))
        
        # 等待训练完成并监控进度
        def progress_callback(tid, status_info, elapsed_time):
            status = status_info.get('status', 'unknown')
            logger.info(f"训练进度 - 任务ID: {tid}, 状态: {status}, 已用时: {int(elapsed_time)}秒")
            
            # 更新状态到state中
            state['current_training_status'] = status
            state['current_training_elapsed'] = elapsed_time
            
            # 实时进度报告
            if writer:
                progress_val = 0.4 + (elapsed_time / 3600.0) * 0.4  # 假设最多1小时，进度从0.4到0.8
                progress_val = min(progress_val, 0.8)
                
                writer(StreamEvent(
                    current=state['current'],
                    progress=progress_val,
                    message=f"训练进行中 - 状态: {status}",
                    data={
                        "task_id": tid,
                        "status": status,
                        "elapsed_time": int(elapsed_time),
                        "estimated_progress": f"{int(progress_val * 100)}%"
                    }
                ))        
        logger.info("⏳ 等待训练完成...")
        success, final_status, error = client.wait_for_completion(
            state=state,
            task_id=task_id,
            check_interval=30,  # 30秒检查一次
            max_wait_time=3600,  # 最多等待1小时
            progress_callback=progress_callback
        )
        
        end_time = time.time()
        training_time = end_time - start_time
        
        # 进度：训练完成，开始获取日志
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.85,
                message="训练完成，正在获取训练日志...",
                data={
                    "training_time": int(training_time),
                    "final_status": final_status.get('status') if final_status else 'unknown',
                    "success": success
                }
            ))
          # 获取训练日志
        logger.info("📄 获取训练日志...")
        log_success, logs, log_error = client.get_task_logs(task_id, lines=1000)
        
        # 获取SwanLab日志路径
        logger.info("📊 获取SwanLab日志路径...")
        swanlab_success, swanlab_path, swanlab_error = client.get_swanlab_log_path(task_id)
        
        if swanlab_success and swanlab_path:
            logger.info(f"SwanLab日志路径: {swanlab_path}")
            state['swanlab_log_path'] = swanlab_path
        elif swanlab_success:
            logger.warning(f"SwanLab日志路径未找到: {swanlab_error}")
            state['swanlab_log_path'] = None
        else:
            logger.error(f"获取SwanLab日志路径失败: {swanlab_error}")
            state['swanlab_log_path'] = None
        
        # 保存训练日志
        output_dir = state.get('output_dir', './output/trainer')
        os.makedirs(output_dir, exist_ok=True)
        
        log_path = os.path.join(output_dir, f'training_log_{task_id}.txt')
        if log_success and logs:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(f"训练任务日志 - 任务ID: {task_id}\n")
                f.write("="*60 + "\n\n")
                f.write(logs)
            logger.info(f"训练日志已保存到: {log_path}")
            state['training_log_path'] = log_path        # 进度：生成训练报告
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.9,
                message="正在生成训练报告...",
                data={
                    "log_retrieved": log_success, 
                    "log_lines": len(logs.split('\n')) if logs else 0,
                    "swanlab_path": state.get('swanlab_log_path'),
                    "swanlab_retrieved": swanlab_success
                }
            ))
          # 生成训练报告
        report = _generate_remote_training_report(
            task_id=task_id,
            final_status=final_status,
            training_time=training_time,
            task_description=task_description,
            swanlab_log_path=state.get('swanlab_log_path'),
            error=error
        )
        
        report_path = os.path.join(output_dir, f'training_report_{task_id}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        state['training_report_path'] = report_path
        
        # 更新状态
        state['training_task_id'] = task_id
        state['training_execution_time'] = training_time
        state['training_final_status'] = final_status
        
        # 检查最终结果
        if success and final_status and final_status.get('status') == 'completed':
            logger.info("🎉 训练任务执行成功!")
            logger.info(f"训练时间: {training_time:.2f} 秒")
            logger.info(f"任务ID: {task_id}")
            
            state['training_success'] = True
              # 进度：训练成功完成
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message=f"训练任务成功完成！用时 {training_time:.1f} 秒",
                    data={
                        "success": True,
                        "task_id": task_id,
                        "training_time": training_time,
                        "report_path": report_path,
                        "log_path": state.get('training_log_path'),
                        "swanlab_log_path": state.get('swanlab_log_path')
                    }
                ))
            
        else:
            final_status_str = final_status.get('status', 'unknown') if final_status else 'unknown'
            logger.error(f"❌ 训练任务执行失败! 最终状态: {final_status_str}")
            if error:
                logger.error(f"错误信息: {error}")
            
            state['training_success'] = False
            state['training_error'] = error or f"训练未成功完成，最终状态: {final_status_str}"
            
            # 进度：训练失败
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message=f"训练任务执行失败: {final_status_str}",
                    data={
                        "success": False,
                        "final_status": final_status_str,
                        "error": error,
                        "training_time": training_time
                    }
                ))
        
        logger.info(f"训练报告已保存到: {report_path}")        
    except Exception as e:
        logger.error(f"训练节点执行失败: {str(e)}")
        state['training_success'] = False
        state['training_error'] = str(e)
        
        # 进度：执行异常
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"训练执行异常: {str(e)}",
                data={
                    "success": False,
                    "error_type": "execution_exception",
                    "error_message": str(e)
                }
            ))
    
    logger.info("训练节点执行完成")
    return state


def _generate_remote_training_report(task_id: str, final_status: dict, 
                                   training_time: float, task_description: str, 
                                   swanlab_log_path: str = None, error: str = None) -> str:
    """
    生成远程训练报告
    
    Args:
        task_id: 训练任务ID
        final_status: 最终状态信息
        training_time: 训练用时
        task_description: 任务描述
        swanlab_log_path: SwanLab日志路径
        error: 错误信息
    
    Returns:
        训练报告文本    """
    
    report = []
    report.append("="*60)
    report.append("远程训练服务执行报告")
    report.append("="*60)
    report.append("")
    
    # 基础信息
    report.append("基础信息:")
    report.append(f"  任务ID: {task_id}")
    report.append(f"  任务描述: {task_description}")
    report.append(f"  执行时间: {training_time:.2f} 秒")
    
    if swanlab_log_path:
        report.append(f"  SwanLab日志路径: {swanlab_log_path}")
    else:
        report.append("  SwanLab日志路径: 未找到")
    
    report.append("")
    
    # 状态信息
    if final_status:
        status = final_status.get('status', 'unknown')
        report.append("执行状态:")
        report.append(f"  最终状态: {status}")
        
        if final_status.get('created_at'):
            report.append(f"  创建时间: {final_status['created_at']}")
        if final_status.get('started_at'):
            report.append(f"  开始时间: {final_status['started_at']}")
        if final_status.get('completed_at'):
            report.append(f"  完成时间: {final_status['completed_at']}")
        
        if final_status.get('error_message'):
            report.append(f"  错误信息: {final_status['error_message']}")
        
        report.append("")
      # 结果总结
    if final_status and final_status.get('status') == 'completed':
        report.append("✅ 训练执行成功")
        report.append("- 训练任务已成功完成")
        report.append("- 模型已保存到训练服务指定目录")
        report.append("- 可通过训练服务API获取详细日志和结果")
        if swanlab_log_path:
            report.append(f"- SwanLab训练监控日志: {swanlab_log_path}")
        else:
            report.append("- SwanLab训练监控日志: 未找到或未生成")
    elif error:
        report.append("❌ 训练执行失败")
        report.append(f"- 错误原因: {error}")
        report.append("- 请检查配置文件和训练服务状态")
        report.append("- 查看详细日志以获取更多信息")
    else:
        report.append("⚠️  训练状态未知")
        report.append("- 无法确定训练最终状态")
        report.append("- 请手动检查训练服务状态")
    report.append("")
    report.append("注意事项:")
    report.append("- 训练结果保存在远程训练服务中")
    report.append("- 如需下载模型，请使用训练服务提供的接口")
    report.append("- 训练日志和监控数据可通过服务API获取")
    if swanlab_log_path:
        report.append("- SwanLab实验监控数据可在指定路径查看")
        report.append("- 可通过SwanLab界面查看训练曲线和指标")
    
    return "\n".join(report)


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
        "swanlab_url": state.get('swanlab_url'),  # 保持向后兼容
        "swanlab_log_path": state.get('swanlab_log_path'),  # 新增SwanLab日志路径
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
