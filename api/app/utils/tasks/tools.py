import os
import uuid
import shutil
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path


def generate_task_id() -> str:
    """生成唯一的任务ID"""
    return str(uuid.uuid4())


def ensure_directory_exists(directory: str) -> None:
    """确保目录存在，不存在则创建"""
    Path(directory).mkdir(parents=True, exist_ok=True)


def get_current_timestamp() -> str:
    """获取当前时间戳"""
    return datetime.now().isoformat()


def save_yaml_config(task_id: str, config_content: str, configs_dir: str) -> str:
    """保存YAML配置文件"""
    ensure_directory_exists(configs_dir)
    config_path = os.path.join(configs_dir, f"{task_id}.yaml")
    
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(config_content)
    
    return config_path


def read_log_file(log_path: str, max_lines: Optional[int] = None) -> tuple[str, int]:
    """读取日志文件内容"""
    if not os.path.exists(log_path):
        return "", 0
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        
        if max_lines and max_lines > 0:
            lines = lines[-max_lines:]
        
        return ''.join(lines), total_lines
    
    except Exception as e:
        return f"Error reading log file: {str(e)}", 0


def cleanup_task_files(task_id: str, configs_dir: str, logs_dir: str) -> None:
    """清理任务相关文件"""
    config_path = os.path.join(configs_dir, f"{task_id}.yaml")
    log_path = os.path.join(logs_dir, f"{task_id}.log")
    
    for file_path in [config_path, log_path]:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Warning: Failed to remove {file_path}: {e}")


def validate_yaml_content(content: str) -> bool:
    """验证YAML内容格式"""
    try:
        import yaml
        yaml.safe_load(content)
        return True
    except Exception:
        return False
