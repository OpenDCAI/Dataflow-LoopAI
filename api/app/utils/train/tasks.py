import os
import subprocess
import asyncio
from datetime import datetime
from typing import Dict, Optional, List, List
import threading
from concurrent.futures import ThreadPoolExecutor
import glob
import json

from ...models.task_models import TaskStatus
from .tools import ensure_directory_exists, get_current_timestamp
from .realtime_log_parser import RealTimeLogParser

from dotenv import load_dotenv
load_dotenv()

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(CURRENT_DIR)))

class TaskManager:
    """训练任务管理器"""
    
    def __init__(self, configs_dir: str, logs_dir: str, runs_dir: str):
        self.configs_dir = configs_dir
        self.logs_dir = logs_dir
        self.runs_dir = runs_dir
        self.tasks: Dict[str, Dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.app_config = json.load(open(os.path.join(BASE_DIR, "app_config.json")))
        self.llamafactory_dir = self.app_config.get("llamafactory_dir")
        self.verl_dir = self.app_config.get("verl_dir")
        # self.llamafactory_dir = "/home/lpc/repos/LLaMA-Factory/"
        
        # 实时日志解析器字典，按任务ID索引
        self.log_parsers: Dict[str, RealTimeLogParser] = {}
        
        # 指标文件存储目录
        self.metrics_dir = os.path.join(BASE_DIR, "metrics")
        ensure_directory_exists(self.metrics_dir)
        
        # 确保目录存在
        for directory in [configs_dir, logs_dir, runs_dir, self.llamafactory_dir]:
            ensure_directory_exists(directory)

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
        # 按创建时间排序，返回最新的任务
        sorted_tasks = sorted(self.tasks.values(), key=lambda x: x['created_at'], reverse=True)
        return sorted_tasks[0]
    
    def start_training(self, task_id: str) -> bool:
        """启动训练任务"""
        if task_id not in self.tasks:
            return False
        
        task_info = self.tasks[task_id]
        
        if task_info['status'] != TaskStatus.PENDING:
            return False
        
        # 更新任务状态
        task_info['status'] = TaskStatus.RUNNING
        task_info['started_at'] = get_current_timestamp()
        
        # 在线程池中异步执行训练
        future = self.executor.submit(self._run_training, task_id)
        task_info['future'] = future
        
        return True
    
    def _get_safe_env(self) -> dict:
        """获取安全的环境变量配置"""
        env = os.environ.copy()
        
        # 从环境变量中获取配置，如果没有则使用默认值
        env["CUDA_VISIBLE_DEVICES"] = self.app_config.get("CUDA_VISIBLE_DEVICES", "0,1")
        env["NCCL_ALGO"] = "Ring"
        
        # 获取 LLaMA Factory 环境路径
        llamafactory_env_path = self.app_config.get("llamafactory_env_path", "")
        if llamafactory_env_path:
            env["LLAMAFACTORY_ENV_PATH"] = llamafactory_env_path
        else:
            print("警告: 未找到LLAMAFACTORY_ENV_PATH环境变量，将使用系统默认的llamafactory-cli")
        
        # 检查必需的API密钥
        swanlab_key = self.app_config.get("swanlab_api_key", "sGBINQXB1ThNYERXGPggy")
        if swanlab_key:
            env["SWANLAB_API_KEY"] = swanlab_key
        else:
            print("警告: 未找到SWANLAB_API_KEY环境变量，某些功能可能无法正常工作")
        
        return env
    
    def _run_training(self, task_id: str) -> None:
        """执行训练任务的内部方法"""
        task_info = self.tasks[task_id]
        config_path = task_info['config_path']
        log_path = os.path.join(self.logs_dir, f"{task_id}.log")
        framework = task_info['framework']
        
        # 创建实时日志解析器
        metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
        log_parser = RealTimeLogParser(log_path, metrics_file)
        self.log_parsers[task_id] = log_parser
        
        try:
            if framework == 'llamafactory':
                # 构建训练命令
                env = self._get_safe_env()
                # 优先使用 _get_safe_env 中的 LLAMAFACTORY_ENV_PATH，否则使用 app_config 中的配置
                config_env_path = self.app_config.get("llamafactory_env_path", "")
                env_path = env.get("LLAMAFACTORY_ENV_PATH") or config_env_path

                # 尝试根据环境路径推断 PYTHONPATH 和 PATH
                python_site_packages = None
                bin_path = None
                env_root = None
                if env_path:
                    # 规范化路径并判断是否指向 bin
                    env_path = os.path.abspath(env_path)
                    env_root = env_path
                    if os.path.basename(env_path) == "bin":
                        env_root = os.path.dirname(env_path)

                    lib_dir = os.path.join(env_root, "lib")
                    if os.path.isdir(lib_dir):
                        try:
                            # 寻找 lib 下以 python 开头的目录，例如 python3.10
                            candidates = [d for d in os.listdir(lib_dir) if d.startswith("python")]
                            candidates.sort()
                            # 优先使用版本号最高的目录
                            for py_dir in reversed(candidates):
                                candidate = os.path.join(lib_dir, py_dir, "site-packages")
                                if os.path.isdir(candidate):
                                    python_site_packages = candidate
                                    break
                        except Exception:
                            python_site_packages = None

                    # 将 env 的 bin 添加到 PATH
                    bin_path = os.path.join(env_root, "bin")
                    if os.path.isdir(bin_path):
                        env["PATH"] = os.pathsep.join([bin_path, env.get("PATH", "")])

                    if python_site_packages:
                        env["PYTHONPATH"] = python_site_packages
                    else:
                        print(f"警告: 未能在 {lib_dir} 中找到 site-packages; 将不设置 PYTHONPATH")
                else:
                    print("警告: 未指定 llamafactory 环境路径，使用系统 PATH 中的 llamafactory-cli")

                env["PYTHONNOUSERSITE"] = "True"

                # 根据环境路径构建命令，优先使用 env 中 bin 下的 llamafactory-cli
                if env_root and bin_path:
                    cli_path = os.path.join(bin_path, "llamafactory-cli")
                    if os.path.exists(cli_path):
                        cmd = [cli_path, "train", config_path]
                        print(f"使用指定环境路径执行训练: {env_root}")
                    else:
                        cmd = ["llamafactory-cli", "train", config_path]
                        print("指定环境路径中未找到 llamafactory-cli，使用系统默认的 llamafactory-cli")
                else:
                    cmd = ["llamafactory-cli", "train", config_path]
                    print("使用系统默认的llamafactory-cli执行训练")
                
                print(f"训练命令: {' '.join(cmd)}")
                
                # 启动实时日志解析
                log_parser.start_monitoring()
                
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
                    
                    # 等待进程完成
                    return_code = process.wait()
                    
                    if return_code == 0:
                        task_info['status'] = TaskStatus.COMPLETED
                    else:
                        task_info['status'] = TaskStatus.FAILED
                        task_info['error_message'] = f"Training process exited with code {return_code}"
            elif framework == 'verl':
                # 构建训练命令
                env = self._get_safe_env()
                env["PYTHONPATH"] = "/jizhicfs/hymiezhao/miniconda3/envs/sql-verl/lib/python3.10/site-packages"
                env["PATH"] = f"/jizhicfs/hymiezhao/miniconda3/envs/sql-verl/bin:{env.get('PATH','')}"
                env["PYTHONNOUSERSITE"] = "True"
                # 根据环境路径构建命令
                # verl_env_path = env.get("VERL_ENV_PATH")
                # if verl_env_path:
                #     # 如果指定了环境路径，使用完整路径
                #     cmd = [os.path.join(verl_env_path, "verl-cli"), "train", config_path]
                #     print(f"使用指定环境路径执行训练: {verl_env_path}")
                # else:
                #     # 否则使用系统PATH中的llamafactory-cli
                #     cmd = ["llamafactory-cli", "train", config_path]
                #     print("使用系统默认的llamafactory-cli执行训练")
                cmd = ["bash", config_path]
                print(f"训练命令: {' '.join(cmd)}")

                # 启动实时日志解析
                log_parser.start_monitoring()

                # 启动训练进程
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
                    
                    # 等待进程完成
                    return_code = process.wait()
                    
                    if return_code == 0:
                        task_info['status'] = TaskStatus.COMPLETED
                    else:
                        task_info['status'] = TaskStatus.FAILED
                        task_info['error_message'] = f"Training process exited with code {return_code}"
            else:
                task_info['status'] = TaskStatus.FAILED
                task_info['error_message'] = f"Unsupported training framework: {framework}" 
                with open(log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n\nError: Unsupported training framework: {framework}\n")

                
        except Exception as e:
            task_info['status'] = TaskStatus.FAILED
            task_info['error_message'] = str(e)
            
            # 记录错误到日志文件
            with open(log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n\nError: {str(e)}\n")
        
        finally:
            # 停止实时日志解析
            if task_id in self.log_parsers:
                self.log_parsers[task_id].stop_monitoring()
                # 保留解析器实例，以便后续查询指标
            
            task_info['completed_at'] = get_current_timestamp()
            task_info['process'] = None
    
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
                    print(f"Error terminating process: {e}")
            
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
            # 按完成时间排序，删除最旧的
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
        
        # 获取任务开始时间，用于筛选日志文件夹
        started_at = task_info.get('started_at')
        if not started_at:
            return None
        
        try:
            # 将时间字符串转换为datetime对象
            task_start_time = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
            
            # 查找所有符合格式的日志文件夹
            log_folders = []
            for item in os.listdir(swanlog_dir):
                item_path = os.path.join(swanlog_dir, item)
                if os.path.isdir(item_path) and item.startswith('run-'):
                    # 获取文件夹创建时间
                    folder_create_time = datetime.fromtimestamp(os.path.getctime(item_path))
                    
                    # 如果文件夹创建时间在任务开始时间之后，则认为是该任务的日志文件夹
                    if folder_create_time >= task_start_time:
                        log_folders.append((item_path, folder_create_time))
            
            # 按创建时间排序，返回最新的一个
            if log_folders:
                log_folders.sort(key=lambda x: x[1])
                return log_folders[-1][0]
            
        except Exception as e:
            print(f"Error finding SwanLab log folder for task {task_id}: {e}")
        
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
            
            # 按创建时间排序（最新的在前）
            log_folders.sort(key=lambda x: x['created_at'], reverse=True)
            
        except Exception as e:
            print(f"Error listing SwanLab log folders: {e}")
        
        return log_folders
    
    def get_task_metrics(self, task_id: str, count: int = 100) -> Optional[Dict]:
        """获取任务的训练指标数据"""
        if task_id not in self.log_parsers:
            # 如果解析器不存在，尝试从文件加载
            metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
            if os.path.exists(metrics_file):
                try:
                    with open(metrics_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception as e:
                    print(f"读取指标文件失败: {e}")
                    return None
            return None
        
        try:
            # 获取最新指标
            latest_metrics = self.log_parsers[task_id].get_latest_metrics(count)
            summary = self.log_parsers[task_id].get_metrics_summary()
            
            return {
                "task_id": task_id,
                "summary": summary,
                "latest_metrics": latest_metrics
            }
        except Exception as e:
            print(f"获取任务指标失败: {e}")
            return None
    
    def get_task_metrics_file_path(self, task_id: str) -> Optional[str]:
        """获取任务指标文件路径"""
        metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
        return metrics_file if os.path.exists(metrics_file) else None
    
    def cleanup_task_metrics(self, task_id: str) -> bool:
        """清理任务的指标数据"""
        try:
            # 停止解析器
            if task_id in self.log_parsers:
                self.log_parsers[task_id].stop_monitoring()
                del self.log_parsers[task_id]
            
            # 删除指标文件
            metrics_file = os.path.join(self.metrics_dir, f"{task_id}_metrics.json")
            if os.path.exists(metrics_file):
                os.remove(metrics_file)
            
            return True
        except Exception as e:
            print(f"清理任务指标失败: {e}")
            return False
