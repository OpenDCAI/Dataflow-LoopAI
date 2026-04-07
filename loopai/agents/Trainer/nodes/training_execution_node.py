"""
训练执行节点
直接在本地执行模型微调，不再依赖远程 api 服务
"""

import os
import json
import time
import shutil
from pathlib import Path
from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.agents.Trainer.utils.task_manager import TaskManager
from loopai.agents.Trainer.utils.task_status import TaskStatus
from loopai.agents.Trainer.utils.task_tools import read_log_file
from loopai.agents.Trainer.utils.insert_dataset import insert_dataset_to_llamafactory
from loopai.agents.Trainer.utils.training_log_parser import parse_task_training_progress, TrainingLogParser
from loopai.logger import get_logger

logger = get_logger()

# 模块级别的 TaskManager 单例（避免重复创建线程池）
_task_manager_instance: TaskManager = None


def _get_task_manager(state: dict) -> TaskManager:
    """
    获取或创建 TaskManager 实例
    
    根据 state 中的配置创建 TaskManager，配置来源优先级：
    1. state['trainer'] 中的字段
    2. state['system'] 中的字段（从 starter.yaml 加载）
    """
    global _task_manager_instance

    trainer_state = state.get('trainer', {})
    system_config = state.get('system', {})

    # 获取输出目录
    output_dir = trainer_state.get('output_dir', './output/trainer')
    configs_dir = os.path.join(output_dir, "configs")
    logs_dir = os.path.join(output_dir, "logs")
    runs_dir = os.path.join(output_dir, "runs")

    # 构建 app_config：合并 system 级别配置和 trainer 级别配置
    app_config = {
        "llamafactory_dir": trainer_state.get('llamafactory_dir') or system_config.get('llamafactory_dir', ''),
        "verl_dir": trainer_state.get('verl_dir') or system_config.get('verl_dir', ''),
        "llamafactory_env_path": trainer_state.get('llamafactory_env_path') or system_config.get('llamafactory_env_path', ''),
        "CUDA_VISIBLE_DEVICES": trainer_state.get('CUDA_VISIBLE_DEVICES') or system_config.get('CUDA_VISIBLE_DEVICES', '0,1'),
        "swanlab_api_key": trainer_state.get('swanlab_api_key') or system_config.get('swanlab_api_key', ''),
        "verl_env_path": trainer_state.get('verl_env_path') or system_config.get('verl_env_path', ''),
    }

    # 每次都重新创建，因为不同任务可能有不同的配置
    _task_manager_instance = TaskManager(
        configs_dir=configs_dir,
        logs_dir=logs_dir,
        runs_dir=runs_dir,
        app_config=app_config
    )

    return _task_manager_instance


def training_execution_node(state: LoopAIState, writer=None) -> LoopAIState:
    """
    训练执行节点
    
    直接在本地通过 TaskManager 执行模型微调任务，不再依赖远程 api 服务。
    
    Args:
        state: LoopAIState 对象，需要包含：
            - train_output_config_path: YAML配置文件路径
            - train_input_task_description: 训练任务描述
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
        trainer_task_id = state.get('trainer', {}).get('trainer_task_id')
        dataset_path = state.get('trainer', {}).get('train_input_dataset_path')
        llamafactory_dir = state.get('trainer', {}).get('llamafactory_dir')
        output_dir = state.get('trainer', {}).get('output_dir', './output/trainer')

        logger.info(f"配置文件: {config_path}")
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

        # 进度：初始化本地任务管理器
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="正在初始化本地训练任务管理器...",
                data={"config_path": config_path}
            ).json())

        # 创建本地 TaskManager（不再需要远程 api 服务）
        task_manager = _get_task_manager(state)
        logger.info("✅ 本地任务管理器初始化成功")

        # 进度：准备提交任务
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="任务管理器就绪，准备提交训练任务...",
                data={"manager_status": "ready"}
            ).json())

        # 拷贝配置文件到任务管理器的 configs 目录
        task_id = trainer_task_id
        if framework == 'llamafactory':
            config_copy_path = os.path.join(task_manager.configs_dir, f"{task_id}.yaml")
        elif framework == 'verl':
            config_copy_path = os.path.join(task_manager.configs_dir, f"{task_id}.sh")
        else:
            config_copy_path = os.path.join(task_manager.configs_dir, f"{task_id}.yaml")
        shutil.copy(config_path, config_copy_path)

        # 创建并启动训练任务
        logger.info("🚀 提交本地训练任务...")
        start_time = time.time()
        task_name = f"trainer_agent_{int(start_time)}"

        task_info = task_manager.create_task(
            task_id=task_id,
            config_path=config_copy_path,
            framework=framework,
            task_name=task_name
        )

        if not task_manager.start_training(task_id, output_dir):
            raise RuntimeError("启动训练任务失败")

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

        # 等待训练完成（本地轮询）
        logger.info("⏳ 等待训练完成...")
        check_interval = 30  # 30秒检查一次
        max_wait_time = 3600  # 最多等待1小时

        while True:
            elapsed_time = time.time() - start_time

            if elapsed_time > max_wait_time:
                logger.warning(f"等待超时（{max_wait_time}秒），任务可能仍在运行")
                break

            # 直接从 TaskManager 获取任务状态（无需 HTTP 请求）
            status_info = task_manager.get_task_status(task_id)
            if not status_info:
                break

            task_status = status_info.get('status')
            if isinstance(task_status, TaskStatus):
                task_status = task_status.value

            state.setdefault('trainer', {})['trainer_current_training_status'] = task_status
            state.setdefault('trainer', {})['current_training_elapsed'] = elapsed_time

            # 读取训练日志，解析当前进度
            log_path = task_manager.get_log_path(task_id)
            training_progress = None
            if os.path.exists(log_path):
                temp_parser = TrainingLogParser()
                training_progress = temp_parser.parse_training_progress(log_path)

            # 计算实际进度
            if training_progress:
                actual_progress = log_parser.get_progress_percentage(training_progress)
                progress_val = actual_progress
                progress_text = training_progress['progress_text']
                time_text = training_progress['time_text']

                logger.info(f"训练进度详情: {progress_text} [{time_text}]")

                progress_message = f"训练进行中 - {progress_text} [{time_text}] - 状态: {task_status}"
                progress_data = {
                    "task_id": task_id,
                    "status": task_status,
                    "elapsed_time": int(elapsed_time),
                    "training_progress": progress_text,
                    "training_time": time_text,
                    "current_step": training_progress['current_step'],
                    "total_steps": training_progress['total_steps'],
                    "actual_progress": f"{int(actual_progress * 100)}%"
                }
            else:
                progress_val = elapsed_time / 3600.0
                progress_val = min(progress_val, 1.0)

                progress_message = f"训练进行中 - 状态: {task_status}"
                progress_data = {
                    "task_id": task_id,
                    "status": task_status,
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

            # 检查任务是否完成
            if task_status in ["completed", "failed", "cancelled"]:
                logger.info(f"任务完成，最终状态: {task_status}")
                break

            logger.info(f"任务状态: {task_status}, 已等待: {int(elapsed_time)}秒")
            time.sleep(check_interval)

        end_time = time.time()
        training_time = end_time - start_time

        # 获取最终状态
        final_status = task_manager.get_task_status(task_id)
        final_status_dict = None
        if final_status:
            final_status_value = final_status['status']
            if isinstance(final_status_value, TaskStatus):
                final_status_value = final_status_value.value
            final_status_dict = {
                'status': final_status_value,
                'created_at': final_status.get('created_at'),
                'started_at': final_status.get('started_at'),
                'completed_at': final_status.get('completed_at'),
                'error_message': final_status.get('error_message'),
            }

        # 进度：训练完成，开始获取日志
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message="训练完成，正在获取训练日志...",
                data={
                    "training_time": int(training_time),
                    "final_status": final_status_dict.get('status') if final_status_dict else 'unknown',
                    "success": final_status_dict.get('status') == 'completed' if final_status_dict else False
                }
            ).json())

        # 直接从本地读取训练日志（无需 HTTP 请求）
        logger.info("📄 获取训练日志...")
        log_path = task_manager.get_log_path(task_id)
        logs, log_total_lines = read_log_file(log_path, max_lines=1000)
        log_success = bool(logs)

        # 直接从本地获取 SwanLab 日志路径（无需 HTTP 请求）
        logger.info("📊 获取SwanLab日志路径...")
        swanlab_path = task_manager.get_train_output_swanlab_log_path(task_id)

        if swanlab_path:
            logger.info(f"SwanLab日志路径: {swanlab_path}")
            state.setdefault('trainer', {})['train_output_swanlab_log_path'] = swanlab_path
        else:
            logger.warning("SwanLab日志路径未找到")
            state.setdefault('trainer', {})['train_output_swanlab_log_path'] = None

        # 保存训练日志
        os.makedirs(output_dir, exist_ok=True)

        training_log_path = os.path.join(output_dir, f'training_log_{task_id}.txt')
        if log_success and logs:
            with open(training_log_path, 'w', encoding='utf-8') as f:
                f.write(f"训练任务日志 - 任务ID: {task_id}\n")
                f.write("=" * 60 + "\n\n")
                f.write(logs)
            logger.info(f"训练日志已保存到: {training_log_path}")
            state.setdefault('trainer', {})['train_output_training_log_path'] = training_log_path

        # 进度：生成训练报告
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message="正在生成训练报告...",
                data={
                    "log_retrieved": log_success,
                    "log_lines": log_total_lines,
                    "swanlab_path": state.get('trainer', {}).get('train_output_swanlab_log_path'),
                }
            ).json())

        # 生成训练报告
        report = _generate_training_report(
            task_id=task_id,
            final_status=final_status_dict,
            training_time=training_time,
            task_description=task_description,
            train_output_swanlab_log_path=state.get('trainer', {}).get('train_output_swanlab_log_path'),
            error=final_status_dict.get('error_message') if final_status_dict else None
        )

        report_path = os.path.join(output_dir, f'training_report_{task_id}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)

        state.setdefault('trainer', {})['train_output_training_report_path'] = report_path

        # 更新状态
        state.setdefault('trainer', {})['trainer_training_task_id'] = task_id
        state.setdefault('trainer', {})['trainer_training_execution_time'] = training_time
        state.setdefault('trainer', {})['trainer_training_final_status'] = final_status_dict

        # 检查最终结果
        if final_status_dict and final_status_dict.get('status') == 'completed':
            logger.info("🎉 训练任务执行成功!")
            logger.info(f"训练时间: {training_time:.2f} 秒")
            logger.info(f"任务ID: {task_id}")

            state.setdefault('trainer', {})['trainer_training_success'] = True

            # === 扫描 checkpoint 目录 ===
            training_output_dir = state.get('trainer', {}).get('train_config', {}).get('output_dir', '')
            # verl 的 checkpoint 路径在 trainer.default_local_dir 下
            if not training_output_dir and framework == 'verl':
                training_output_dir = state.get('trainer', {}).get('output_dir', '')

            checkpoints = []
            if training_output_dir and os.path.isdir(training_output_dir):
                for entry in sorted(os.listdir(training_output_dir)):
                    entry_path = os.path.join(training_output_dir, entry)
                    # LlamaFactory: checkpoint-XXX, verl: global_step_XXX / epoch_XXX
                    if os.path.isdir(entry_path) and (
                        entry.startswith('checkpoint-') or
                        entry.startswith('global_step_') or
                        entry.startswith('epoch_')
                    ):
                        checkpoints.append(entry)
            state.setdefault('trainer', {})['training_checkpoints'] = checkpoints
            logger.info(f"发现 {len(checkpoints)} 个 checkpoint: {checkpoints}")

            # === 提取关键 step 的 loss ===
            step_losses = []
            if framework == 'verl':
                # verl file logger 输出 JSONL: {"step": N, "data": {"train/loss": V, ...}}
                # 搜索可能的日志文件位置
                verl_log_candidates = []
                if training_output_dir:
                    verl_log_candidates.append(os.path.join(training_output_dir, '*.jsonl'))
                # 也检查 metrics 目录
                metrics_dir = os.path.join(output_dir, "metrics")
                if os.path.isdir(metrics_dir):
                    for f_name in os.listdir(metrics_dir):
                        if f_name.endswith('.json') or f_name.endswith('.jsonl'):
                            verl_log_candidates.append(os.path.join(metrics_dir, f_name))

                # 从实时日志解析器的 metrics 文件读取
                metrics_file = os.path.join(output_dir, "metrics", "metrics.json")
                if os.path.isfile(metrics_file):
                    try:
                        with open(metrics_file, 'r', encoding='utf-8') as f:
                            metrics_data = json.load(f)
                        for m in metrics_data.get("metrics", []):
                            entry = {"step": m.get("step")}
                            for k in ["loss", "train/loss", "grad_norm", "train/grad_norm", "lr", "train/lr"]:
                                if k in m:
                                    short_k = k.split("/")[-1] if "/" in k else k
                                    entry[short_k] = m[k]
                            if any(k in entry for k in ["loss", "grad_norm", "lr"]):
                                step_losses.append(entry)
                        logger.info(f"从 verl metrics 提取了 {len(step_losses)} 条记录")
                    except Exception as e:
                        logger.warning(f"读取 verl metrics 失败: {e}")
            else:
                # LlamaFactory: trainer_log.jsonl
                trainer_log_path = os.path.join(training_output_dir, 'trainer_log.jsonl') if training_output_dir else ''
                if trainer_log_path and os.path.isfile(trainer_log_path):
                    try:
                        with open(trainer_log_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    record = json.loads(line)
                                    entry = {"step": record.get("current_steps")}
                                    if "loss" in record:
                                        entry["loss"] = record["loss"]
                                    if "eval_loss" in record:
                                        entry["eval_loss"] = record["eval_loss"]
                                    if "loss" in entry or "eval_loss" in entry:
                                        step_losses.append(entry)
                                except json.JSONDecodeError:
                                    continue
                        logger.info(f"从 trainer_log.jsonl 提取了 {len(step_losses)} 条 step-loss 记录")
                    except Exception as e:
                        logger.warning(f"读取 trainer_log.jsonl 失败: {e}")
                else:
                    logger.warning(f"trainer_log.jsonl 不存在: {trainer_log_path}")
            state.setdefault('trainer', {})['training_step_losses'] = step_losses

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
                        "train_output_swanlab_log_path": state.get('trainer', {}).get('train_output_swanlab_log_path'),
                        "training_checkpoints": checkpoints,
                        "training_step_losses_count": len(step_losses)
                    }
                ).json())

        else:
            final_status_str = final_status_dict.get('status', 'unknown') if final_status_dict else 'unknown'
            error_msg = final_status_dict.get('error_message') if final_status_dict else None
            logger.error(f"❌ 训练任务执行失败! 最终状态: {final_status_str}")
            if error_msg:
                logger.error(f"错误信息: {error_msg}")

            state.setdefault('trainer', {})['trainer_training_success'] = False
            state.setdefault('trainer', {})['train_output_training_error'] = error_msg or f"训练未成功完成，最终状态: {final_status_str}"

            # 进度：训练失败
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message=f"训练任务执行失败: {final_status_str}",
                    data={
                        "success": False,
                        "final_status": final_status_str,
                        "error": error_msg,
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


def _generate_training_report(task_id: str, final_status: dict,
                              training_time: float, task_description: str,
                              train_output_swanlab_log_path: str = None, error: str = None) -> str:
    """
    生成训练报告
    
    Args:
        task_id: 训练任务ID
        final_status: 最终状态信息
        training_time: 训练用时
        task_description: 任务描述
        train_output_swanlab_log_path: SwanLab日志路径
        error: 错误信息
    
    Returns:
        训练报告文本
    """

    report = []
    report.append("=" * 60)
    report.append("训练执行报告")
    report.append("=" * 60)
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
        report.append("- 模型已保存到指定输出目录")
        if train_output_swanlab_log_path:
            report.append(f"- SwanLab训练监控日志: {train_output_swanlab_log_path}")
        else:
            report.append("- SwanLab训练监控日志: 未找到或未生成")
    elif error:
        report.append("❌ 训练执行失败")
        report.append(f"- 错误原因: {error}")
        report.append("- 请检查配置文件和训练环境")
        report.append("- 查看详细日志以获取更多信息")
    else:
        report.append("⚠️  训练状态未知")
        report.append("- 无法确定训练最终状态")
        report.append("- 请手动检查训练状态")

    report.append("")
    report.append("注意事项:")
    report.append("- 训练结果保存在本地输出目录中")
    report.append("- 训练日志和监控数据可在输出目录中查看")
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
        "swanlab_url": state.get('trainer', {}).get('swanlab_url', ''),
        "train_output_swanlab_log_path": state.get('trainer', {}).get('train_output_swanlab_log_path'),
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
