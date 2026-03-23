"""
训练任务管理器
从 api/app/utils/train/tasks.py 迁移到 loopai

主要改动：
- 移除对 api 侧 starter.yaml 的直接依赖，改为通过构造函数参数传入配置
- 移除对 api 相对路径的依赖
- 使用 loopai 的 logger 替代 print
"""

import os
import subprocess
import shutil
from datetime import datetime
from typing import Dict, Optional, List
import threading
from concurrent.futures import ThreadPoolExecutor
import json

from loopai.logger import get_logger
from .task_status import TaskStatus
from .task_tools import ensure_directory_exists, get_current_timestamp
from .realtime_log_parser import RealTimeLogParser

logger = get_logger()


class TaskManager:
    """训练任务管理器
    
    负责管理训练任务的生命周期：创建、启动、监控、取消、查询。
    通过 subprocess 启动训练进程，使用 RealTimeLogParser 实时解析日志。
    """

    def __init__(self, configs_dir: str, logs_dir: str, runs_dir: str, app_config: dict = None):
        """
        初始化任务管理器
        
        Args:
            configs_dir: 配置文件存储目录
            logs_dir: 日志文件存储目录
            runs_dir: 运行记录存储目录
            app_config: 应用配置字典，包含以下可选字段：
                - llamafactory_dir: LlamaFactory 安装目录
                - verl_dir: verl 安装目录
                - llamafactory_env_path: LlamaFactory 虚拟环境路径
                - CUDA_VISIBLE_DEVICES: GPU 设备号
                - swanlab_api_key: SwanLab API 密钥
        """
        self.configs_dir = configs_dir
        self.logs_dir = logs_dir
        self.runs_dir = runs_dir
        self.tasks: Dict[str, Dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=4)

        # 配置通过参数传入，而非从 starter.yaml 读取
        self.app_config = app_config or {}
        self.llamafactory_dir = self.app_config.get("llamafactory_dir", "")
        self.verl_dir = self.app_config.get("verl_dir", "")

        # 实时日志解析器字典，按任务ID索引
        self.log_parsers: Dict[str, RealTimeLogParser] = {}

        # 确保目录存在
        for directory in [configs_dir, logs_dir, runs_dir]:
            ensure_directory_exists(directory)
        if self.llamafactory_dir:
            ensure_directory_exists(self.llamafactory_dir)

    def create_task(self, task_id: str, config_path: str, framework: str, task_name: Optional[str] = None) -> Dict:
        """创建新的训练任务"""
        task_info = {
            'task_id': task_id,
            'task_name': task_name or task_id,
            'config_path': config_path,
            'framework': framework,
            'status': TaskStatus.PENDING,
            'created_at': get_current_timestamp(),
            'started_at': None,
            'completed_at': None,
            'error_message': None,
            'process': None
        }

        self.tasks[task_id] = task_info
        return task_info

    def get_last_task(self) -> Optional[Dict]:
        """获取最后创建的任务"""
        if not self.tasks:
            return None
        sorted_tasks = sorted(self.tasks.values(), key=lambda x: x['created_at'], reverse=True)
        return sorted_tasks[0]

    def start_training(self, task_id: str, output_dir: str) -> bool:
        """启动训练任务"""
        if task_id not in self.tasks:
            return False

        task_info = self.tasks[task_id]

        if task_info['status'] != TaskStatus.PENDING:
            return False

        # 更新任务状态
        task_info['status'] = TaskStatus.RUNNING
        task_info['started_at'] = get_current_timestamp()
        self.metrics_dir = os.path.join(output_dir, "metrics")
        ensure_directory_exists(self.metrics_dir)

        # 在线程池中异步执行训练
        future = self.executor.submit(self._run_training, task_id)
        task_info['future'] = future

        return True

    def _get_safe_env(self) -> dict:
        """获取安全的环境变量配置"""
        env = os.environ.copy()

        # 从配置中获取
        env["CUDA_VISIBLE_DEVICES"] = self.app_config.get("CUDA_VISIBLE_DEVICES", "0,1")
        env["NCCL_ALGO"] = "Ring"

        # 获取 LLaMA Factory 环境路径
        llamafactory_env_path = self.app_config.get("llamafactory_env_path", "")
        if llamafactory_env_path:
            env["LLAMAFACTORY_ENV_PATH"] = llamafactory_env_path
        else:
            logger.warning("未找到LLAMAFACTORY_ENV_PATH配置，将使用系统默认的llamafactory-cli")

        # 检查必需的API密钥
        swanlab_key = self.app_config.get("swanlab_api_key", "")
        if swanlab_key:
            env["SWANLAB_API_KEY"] = swanlab_key
        else:
            logger.warning("未找到SWANLAB_API_KEY配置，某些功能可能无法正常工作")

        return env

    def _run_training(self, task_id: str) -> None:
        """执行训练任务的内部方法"""
        task_info = self.tasks[task_id]
        config_path = task_info['config_path']
        log_path = os.path.join(self.logs_dir, f"{task_id}.log")
        framework = task_info['framework']

        # 创建实时日志解析器
        metrics_file = os.path.join(self.metrics_dir, "metrics.json")
        log_parser = RealTimeLogParser(log_path, metrics_file)
        self.log_parsers[task_id] = log_parser

        try:
            if framework == 'llamafactory':
                self._run_llamafactory_training(task_info, config_path, log_path, log_parser)
            elif framework == 'verl':
                self._run_verl_training(task_info, config_path, log_path, log_parser)
            else:
                task_info['status'] = TaskStatus.FAILED
                task_info['error_message'] = f"Unsupported training framework: {framework}"
                with open(log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n\nError: Unsupported training framework: {framework}\n")

        except Exception as e:
            task_info['status'] = TaskStatus.FAILED
            task_info['error_message'] = str(e)

            with open(log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n\nError: {str(e)}\n")

        finally:
            # 停止实时日志解析
            if task_id in self.log_parsers:
                self.log_parsers[task_id].stop_monitoring()

            task_info['completed_at'] = get_current_timestamp()
            task_info['process'] = None

    def _run_llamafactory_training(self, task_info: dict, config_path: str, log_path: str, log_parser: RealTimeLogParser) -> None:
        """执行 LlamaFactory 训练"""
        env = self._get_safe_env()
        config_env_path = self.app_config.get("llamafactory_env_path", "")
        env_path = env.get("LLAMAFACTORY_ENV_PATH") or config_env_path

        # 尝试根据环境路径推断 PYTHONPATH 和 PATH
        python_site_packages = None
        bin_path = None
        env_root = None
        if env_path:
            env_path = os.path.abspath(env_path)
            env_root = env_path
            if os.path.basename(env_path) == "bin":
                env_root = os.path.dirname(env_path)

            lib_dir = os.path.join(env_root, "lib")
            if os.path.isdir(lib_dir):
                try:
                    candidates = [d for d in os.listdir(lib_dir) if d.startswith("python")]
                    candidates.sort()
                    for py_dir in reversed(candidates):
                        candidate = os.path.join(lib_dir, py_dir, "site-packages")
                        if os.path.isdir(candidate):
                            python_site_packages = candidate
                            break
                except Exception:
                    python_site_packages = None

            bin_path = os.path.join(env_root, "bin")
            if os.path.isdir(bin_path):
                env["PATH"] = os.pathsep.join([bin_path, env.get("PATH", "")])

            if python_site_packages:
                env["PYTHONPATH"] = python_site_packages
            else:
                logger.warning(f"未能在 {lib_dir} 中找到 site-packages; 将不设置 PYTHONPATH")
        else:
            logger.warning("未指定 llamafactory 环境路径，使用系统 PATH 中的 llamafactory-cli")

        env["PYTHONNOUSERSITE"] = "True"

        # 根据环境路径构建命令
        if env_root and bin_path:
            cli_path = os.path.join(bin_path, "llamafactory-cli")
            if os.path.exists(cli_path):
                cmd = [cli_path, "train", config_path]
                logger.info(f"使用指定环境路径执行训练: {env_root}")
            else:
                cmd = ["llamafactory-cli", "train", config_path]
                logger.info("指定环境路径中未找到 llamafactory-cli，使用系统默认的 llamafactory-cli")
        else:
            cmd = ["llamafactory-cli", "train", config_path]
            logger.info("使用系统默认的llamafactory-cli执行训练")

        logger.info(f"训练命令: {' '.join(cmd)}")

        # 启动训练进程
        with open(log_path, 'w', encoding='utf-8') as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                cwd=self.llamafactory_dir,
                env=env
            )

            task_info['process'] = process
            try:
                log_parser.start_monitoring()
            except Exception as e:
                logger.error(f"启动日志监控失败: {e}")

            return_code = process.wait()

            if return_code == 0:
                task_info['status'] = TaskStatus.COMPLETED
            else:
                task_info['status'] = TaskStatus.FAILED
                task_info['error_message'] = f"Training process exited with code {return_code}"

    def _run_verl_training(self, task_info: dict, config_path: str, log_path: str, log_parser: RealTimeLogParser) -> None:
        """执行 verl 训练"""
        env = self._get_safe_env()

        # verl 环境配置 - 可通过 app_config 传入
        verl_env_path = self.app_config.get("verl_env_path", "")
        if verl_env_path:
            env["PYTHONPATH"] = os.path.join(verl_env_path, "lib/python3.10/site-packages")
            env["PATH"] = f"{os.path.join(verl_env_path, 'bin')}:{env.get('PATH', '')}"
        env["PYTHONNOUSERSITE"] = "True"

        cmd = ["bash", config_path]
        logger.info(f"训练命令: {' '.join(cmd)}")

        # 启动实时日志解析
        log_parser.start_monitoring()

        with open(log_path, 'w', encoding='utf-8') as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                cwd=self.verl_dir,
                env=env
            )

            task_info['process'] = process

            return_code = process.wait()

            if return_code == 0:
                task_info['status'] = TaskStatus.COMPLETED
            else:
                task_info['status'] = TaskStatus.FAILED
                task_info['error_message'] = f"Training process exited with code {return_code}"

    def get_task_status(self, task_id: str) -> Optional[Dict]:
        """获取任务状态"""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> Dict[str, Dict]:
        """获取所有任务"""
        return self.tasks.copy()

    def cancel_task(self, task_id: str) -> bool:
        """取消训练任务"""
        if task_id not in self.tasks:
            return False

        task_info = self.tasks[task_id]

        if task_info['status'] == TaskStatus.RUNNING:
            process = task_info.get('process')
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")

            task_info['status'] = TaskStatus.CANCELLED
            task_info['completed_at'] = get_current_timestamp()
            task_info['error_message'] = "Task cancelled by user"
            return True

        return False

    def cleanup_completed_tasks(self, max_keep: int = 100) -> None:
        """清理已完成的任务（保留最近的max_keep个）"""
        completed_tasks = [
            (task_id, task_info) for task_id, task_info in self.tasks.items()
            if task_info['status'] in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
        ]

        if len(completed_tasks) > max_keep:
            completed_tasks.sort(key=lambda x: x[1].get('completed_at', ''))
            tasks_to_remove = completed_tasks[:-max_keep]

            for task_id, _ in tasks_to_remove:
                del self.tasks[task_id]

    def get_train_output_swanlab_log_path(self, task_id: str) -> Optional[str]:
        """获取指定任务的SwanLab日志文件夹路径"""
        if task_id not in self.tasks:
            return None

        task_info = self.tasks[task_id]
        swanlog_dir = os.path.join(self.llamafactory_dir, "swanlog")

        if not os.path.exists(swanlog_dir):
            return None

        started_at = task_info.get('started_at')
        if not started_at:
            return None

        try:
            task_start_time = datetime.fromisoformat(started_at.replace('Z', '+00:00'))

            log_folders = []
            for item in os.listdir(swanlog_dir):
                item_path = os.path.join(swanlog_dir, item)
                if os.path.isdir(item_path) and item.startswith('run-'):
                    folder_create_time = datetime.fromtimestamp(os.path.getctime(item_path))

                    if folder_create_time >= task_start_time:
                        log_folders.append((item_path, folder_create_time))

            if log_folders:
                log_folders.sort(key=lambda x: x[1])
                return log_folders[-1][0]

        except Exception as e:
            logger.error(f"Error finding SwanLab log folder for task {task_id}: {e}")

        return None

    def get_all_swanlab_logs(self) -> List[Dict[str, str]]:
        """获取所有SwanLab日志文件夹"""
        swanlog_dir = os.path.join(self.llamafactory_dir, "swanlog")

        if not os.path.exists(swanlog_dir):
            return []

        log_folders = []
        try:
            for item in os.listdir(swanlog_dir):
                item_path = os.path.join(swanlog_dir, item)
                if os.path.isdir(item_path) and item.startswith('run-'):
                    folder_create_time = datetime.fromtimestamp(os.path.getctime(item_path))
                    log_folders.append({
                        'folder_name': item,
                        'folder_path': item_path,
                        'created_at': folder_create_time.isoformat()
                    })

            log_folders.sort(key=lambda x: x['created_at'], reverse=True)

        except Exception as e:
            logger.error(f"Error listing SwanLab log folders: {e}")

        return log_folders

    def get_task_metrics(self, task_id: str, count: int = 100) -> Optional[Dict]:
        """获取任务的训练指标数据"""
        if task_id not in self.log_parsers:
            metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
            if os.path.exists(metrics_file):
                try:
                    with open(metrics_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    logger.error(f"读取指标文件失败: {e}")
                    return None
            return None

        try:
            latest_metrics = self.log_parsers[task_id].get_latest_metrics(count)
            summary = self.log_parsers[task_id].get_metrics_summary()

            return {
                "task_id": task_id,
                "summary": summary,
                "latest_metrics": latest_metrics
            }
        except Exception as e:
            logger.error(f"获取任务指标失败: {e}")
            return None

    def get_task_metrics_file_path(self, task_id: str) -> Optional[str]:
        """获取任务指标文件路径"""
        metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
        return metrics_file if os.path.exists(metrics_file) else None

    def cleanup_task_metrics(self, task_id: str) -> bool:
        """清理任务的指标数据"""
        try:
            if task_id in self.log_parsers:
                self.log_parsers[task_id].stop_monitoring()
                del self.log_parsers[task_id]

            metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
            if os.path.exists(metrics_file):
                os.remove(metrics_file)

            return True
        except Exception as e:
            logger.error(f"清理任务指标失败: {e}")
            return False

    def get_log_path(self, task_id: str) -> str:
        """获取指定任务的日志文件路径"""
        return os.path.join(self.logs_dir, f"{task_id}.log")
