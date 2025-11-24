import os
import subprocess
import asyncio
from datetime import datetime
from typing import Dict, Optional
import threading
from concurrent.futures import ThreadPoolExecutor

from .models import TaskStatus
from .utils import ensure_directory_exists, get_current_timestamp

from dotenv import load_dotenv
load_dotenv()

class TaskManager:
    """训练任务管理器"""
    
    def __init__(self, configs_dir: str, logs_dir: str, runs_dir: str):
        self.configs_dir = configs_dir
        self.logs_dir = logs_dir
        self.runs_dir = runs_dir
        self.tasks: Dict[str, Dict] = {}
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.llamafactory_dir = "/jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory"
        
        # 确保目录存在
        for directory in [configs_dir, logs_dir, runs_dir, self.llamafactory_dir]:
            ensure_directory_exists(directory)
    
    def create_task(self, task_id: str, config_path: str, task_name: Optional[str] = None) -> Dict:
        """创建新的训练任务"""
        task_info = {
            'task_id': task_id,
            'task_name': task_name or task_id,
            'config_path': config_path,
            'status': TaskStatus.PENDING,
            'created_at': get_current_timestamp(),
            'started_at': None,
            'completed_at': None,
            'error_message': None,
            'process': None
        }
        
        self.tasks[task_id] = task_info
        return task_info
    
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
        env["CUDA_VISIBLE_DEVICES"] = os.getenv("CUDA_VISIBLE_DEVICES", "0,1,2,3")
        env["NCCL_ALGO"] = os.getenv("NCCL_ALGO", "Ring")
        
        # 检查必需的API密钥
        swanlab_key = os.getenv("SWANLAB_API_KEY")
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
        
        try:
            # 构建训练命令
            env = self._get_safe_env()
            cmd = ["llamafactory-cli", "train", config_path]
            
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
                
        except Exception as e:
            task_info['status'] = TaskStatus.FAILED
            task_info['error_message'] = str(e)
            
            # 记录错误到日志文件
            with open(log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n\nError: {str(e)}\n")
        
        finally:
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
