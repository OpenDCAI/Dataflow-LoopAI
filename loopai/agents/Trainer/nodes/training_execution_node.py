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
from loopai.agents.Trainer.utils.insert_dataset import insert_dataset_to_llamafactory
from loopai.agents.Trainer.utils.training_log_parser import parse_task_training_progress, TrainingLogParser
from loopai.logger import get_logger

logger = get_logger()


def training_execution_node(state: LoopAIState, writer=None) -> LoopAIState:
    """
    训练执行节点
    
    调用远程训练服务执行模型微调任务
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_output_config_path: YAML配置文件路径
            - train_input_task_description: 训练任务描述
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
        if not state.get('trainer', {}).get('trainer_config_generation_success', False):
            raise ValueError("配置生成未成功，无法执行训练")
        
        framework = state.get('trainer', {}).get('train_framework')

        config_path = state.get('trainer', {}).get('train_output_config_path')
        if not config_path or not os.path.exists(config_path):
            raise ValueError(f"配置文件不存在: {config_path}")
        
        # 获取参数
        task_description = state.get('trainer', {}).get('train_input_task_description', '未指定任务描述')
        service_url = state.get('trainer', {}).get('training_service_url', 'http://localhost:8000')
        dataset_path = state.get('trainer', {}).get('train_input_dataset_path')
        llamafactory_dir = state.get('trainer', {}).get('llamafactory_dir')
        
        logger.info(f"配置文件: {config_path}")
        logger.info(f"训练服务地址: {service_url}")
        logger.info(f"任务描述: {task_description}")
        logger.info(f"训练框架: {framework}")
        logger.info(f"数据集路径: {dataset_path}")
        logger.info(f"LlamaFactory目录: {llamafactory_dir}")
        
        # 进度：准备数据集注册
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="正在检查并注册数据集到LlamaFactory...",
                data={"dataset_path": dataset_path, "llamafactory_dir": llamafactory_dir}
            ).json())
        
        # 插入数据集到 LlamaFactory dataset_info.json
        if framework == 'llamafactory' and dataset_path and llamafactory_dir:
            logger.info("开始将数据集注册到LlamaFactory...")
            success, dataset_name, error = insert_dataset_to_llamafactory(dataset_path, llamafactory_dir)
            
            if success:
                logger.info(f"✅ 数据集 '{dataset_name}' 已成功注册到LlamaFactory")
                state.setdefault('trainer', {})['dataset_name'] = dataset_name
                
                # 进度：数据集注册成功
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=0.0,
                        message=f"数据集 '{dataset_name}' 已成功注册到LlamaFactory",
                        data={"dataset_name": dataset_name, "registered": True}
                    ).json())
            else:
                error_msg = f"数据集注册失败: {error}"
                logger.error(error_msg)
                state.setdefault('trainer', {})['train_output_training_error'] = error_msg
                
                # 进度：数据集注册失败
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=1.0,
                        message=f"数据集注册失败: {error}",
                        data={"error": error, "registered": False}
                    ).json())
                
                return state
        elif framework == 'llamafactory':
            logger.warning("LlamaFactory框架需要dataset_path和llamafactory_dir参数，但未提供")
        else:
            logger.info(f"当前框架 '{framework}' 无需数据集注册，跳过此步骤")
        
        # 进度：开始连接服务
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="正在连接训练服务...",
                data={"service_url": service_url, "config_path": config_path}
            ).json())
        
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
                progress=0.0,
                message="训练服务连接成功，准备提交任务...",
                data={"service_status": "connected"}
            ).json())        
        # 启动训练任务
        logger.info("🚀 提交训练任务到远程服务...")
        
        # 进度：正在提交任务
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="正在提交训练任务到远程服务...",
                data={"task_description": task_description}
            ).json())
        
        start_time = time.time()
        success, task_id_or_error, error_detail = client.start_training(
            framework=framework,
            config_path=config_path,
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
                progress=0.0,
                message=f"训练任务提交成功，任务ID: {task_id}, 训练框架: {framework}",
                data={"task_id": task_id, "start_time": start_time}
            ).json())

        log_parser = TrainingLogParser()  # 创建日志解析器实例
        
        def progress_callback(tid, status_info, elapsed_time):
            status = status_info.get('status', 'unknown')
            logger.info(f"训练进度 - 任务ID: {tid}, 状态: {status}, 已用时: {int(elapsed_time)}秒")
            
            # 更新状态到state中
            state.setdefault('trainer', {})['trainer_current_training_status'] = status
            state.setdefault('trainer', {})['current_training_elapsed'] = elapsed_time

            # 读取训练日志，解析当前进度
            # output_dir = state.get('trainer', {}).get('output_dir', './output/trainer')
            # log_output_dir = os.path.join('./api/logs') if framework == 'llamafactory' and llamafactory_dir else output_dir
            training_progress = parse_task_training_progress(tid)
            
            # 计算实际进度
            if training_progress:
                # 从日志中解析出的实际训练进度
                actual_progress = log_parser.get_progress_percentage(training_progress)
                progress_val = actual_progress
                progress_text = training_progress['progress_text']
                time_text = training_progress['time_text']
                
                logger.info(f"训练进度详情: {progress_text} [{time_text}]")
                
                progress_message = f"训练进行中 - {progress_text} [{time_text}] - 状态: {status}"
                progress_data = {
                    "task_id": tid,
                    "status": status,
                    "elapsed_time": int(elapsed_time),
                    "training_progress": progress_text,
                    "training_time": time_text,
                    "current_step": training_progress['current_step'],
                    "total_steps": training_progress['total_steps'],
                    "actual_progress": f"{int(actual_progress * 100)}%"
                }
            else:
                # 如果无法解析日志，使用时间估算
                progress_val = elapsed_time / 3600.0  # 假设最多1小时，进度从0.4到0.8
                progress_val = min(progress_val, 1.0)
                
                progress_message = f"训练进行中 - 状态: {status}"
                progress_data = {
                    "task_id": tid,
                    "status": status,
                    "elapsed_time": int(elapsed_time),
                    "estimated_progress": f"{int(progress_val * 100)}%"
                }
            
            # 实时进度报告
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=progress_val,
                    message=progress_message,
                    data=progress_data
                ).json())        
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
                progress=1.0,
                message="训练完成，正在获取训练日志...",
                data={
                    "training_time": int(training_time),
                    "final_status": final_status.get('status') if final_status else 'unknown',
                    "success": success
                }
            ).json())
          # 获取训练日志
        logger.info("📄 获取训练日志...")
        log_success, logs, log_error = client.get_task_logs(task_id, lines=1000)
        
        # 获取SwanLab日志路径
        logger.info("📊 获取SwanLab日志路径...")
        swanlab_success, swanlab_path, swanlab_error = client.get_train_output_swanlab_log_path(task_id)
        
        if swanlab_success and swanlab_path:
            logger.info(f"SwanLab日志路径: {swanlab_path}")
            state.setdefault('trainer', {})['train_output_swanlab_log_path'] = swanlab_path
        elif swanlab_success:
            logger.warning(f"SwanLab日志路径未找到: {swanlab_error}")
            state.setdefault('trainer', {})['train_output_swanlab_log_path'] = None
        else:
            logger.error(f"获取SwanLab日志路径失败: {swanlab_error}")
            state.setdefault('trainer', {})['train_output_swanlab_log_path'] = None
        
        # 保存训练日志
        output_dir = state.get('trainer', {}).get('output_dir', './output/trainer')
        os.makedirs(output_dir, exist_ok=True)
        
        log_path = os.path.join(output_dir, f'training_log_{task_id}.txt')
        if log_success and logs:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(f"训练任务日志 - 任务ID: {task_id}\n")
                f.write("="*60 + "\n\n")
                f.write(logs)
            logger.info(f"训练日志已保存到: {log_path}")
            state.setdefault('trainer', {})['train_output_training_log_path'] = log_path        # 进度：生成训练报告
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message="正在生成训练报告...",
                data={
                    "log_retrieved": log_success, 
                    "log_lines": len(logs.split('\n')) if logs else 0,
                    "swanlab_path": state.get('trainer', {}).get('train_output_swanlab_log_path'),
                    "swanlab_retrieved": swanlab_success
                }
            ).json())
          # 生成训练报告
        report = _generate_remote_training_report(
            task_id=task_id,
            final_status=final_status,
            training_time=training_time,
            task_description=task_description,
            train_output_swanlab_log_path=state.get('trainer', {}).get('train_output_swanlab_log_path'),
            error=error
        )
        
        report_path = os.path.join(output_dir, f'training_report_{task_id}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        state.setdefault('trainer', {})['train_output_training_report_path'] = report_path
        
        # 更新状态
        state.setdefault('trainer', {})['trainer_training_task_id'] = task_id
        state.setdefault('trainer', {})['trainer_training_execution_time'] = training_time
        state.setdefault('trainer', {})['trainer_training_final_status'] = final_status
        
        # 检查最终结果
        if success and final_status and final_status.get('status') == 'completed':
            logger.info("🎉 训练任务执行成功!")
            logger.info(f"训练时间: {training_time:.2f} 秒")
            logger.info(f"任务ID: {task_id}")
            
            state.setdefault('trainer', {})['trainer_training_success'] = True
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
                        "log_path": state.get('trainer', {}).get('train_output_training_log_path'),
                        "train_output_swanlab_log_path": state.get('trainer', {}).get('train_output_swanlab_log_path')
                    }
                ).json())
            
        else:
            final_status_str = final_status.get('status', 'unknown') if final_status else 'unknown'
            logger.error(f"❌ 训练任务执行失败! 最终状态: {final_status_str}")
            if error:
                logger.error(f"错误信息: {error}")
            
            state.setdefault('trainer', {})['trainer_training_success'] = False
            state.setdefault('trainer', {})['train_output_training_error'] = error or f"训练未成功完成，最终状态: {final_status_str}"
            
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
                ).json())
        
        logger.info(f"训练报告已保存到: {report_path}")        
    except Exception as e:
        logger.error(f"训练节点执行失败: {str(e)}")
        state.setdefault('trainer', {})['trainer_training_success'] = False
        state.setdefault('trainer', {})['train_output_training_error'] = str(e)
        
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
            ).json())
    
    logger.info("训练节点执行完成")
    return state


def _generate_remote_training_report(task_id: str, final_status: dict, 
                                   training_time: float, task_description: str, 
                                   train_output_swanlab_log_path: str = None, error: str = None) -> str:
    """
    生成远程训练报告
    
    Args:
        task_id: 训练任务ID
        final_status: 最终状态信息
        training_time: 训练用时
        task_description: 任务描述
        train_output_swanlab_log_path: SwanLab日志路径
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
    
    if train_output_swanlab_log_path:
        report.append(f"  SwanLab日志路径: {train_output_swanlab_log_path}")
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
        if train_output_swanlab_log_path:
            report.append(f"- SwanLab训练监控日志: {train_output_swanlab_log_path}")
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
    if train_output_swanlab_log_path:
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
        "trainer_data_check_passed": state.get('trainer', {}).get('trainer_data_check_passed', False),
        "trainer_config_generation_success": state.get('trainer', {}).get('trainer_config_generation_success', False),
        "trainer_training_success": state.get('trainer', {}).get('trainer_training_success', False),
        "training_time": state.get('trainer', {}).get('trainer_training_execution_time', 0),
        "swanlab_url": state.get('trainer', {}).get('swanlab_url', ''),  # 保持向后兼容
        "train_output_swanlab_log_path": state.get('trainer', {}).get('train_output_swanlab_log_path'),  # 新增SwanLab日志路径
        "train_output_training_log_path": state.get('trainer', {}).get('train_output_training_log_path'),
        "output_dir": state.get('trainer', {}).get('train_output_dir'),
        "errors": []
    }
    
    # 收集错误信息
    if state.get('trainer', {}).get('trainer_data_check_error'):
        status["errors"].append(f"数据检查: {state.get('trainer', {}).get('trainer_data_check_error', '')}")
    
    if state.get('trainer', {}).get('trainer_config_generation_error'):
        status["errors"].append(f"配置生成: {state.get('trainer', {}).get('trainer_config_generation_error', '')}")
    
    if state.get('trainer', {}).get('train_output_training_error'):
        status["errors"].append(f"训练执行: {state.get('trainer', {}).get('train_output_training_error', '')}")
    
    return status
