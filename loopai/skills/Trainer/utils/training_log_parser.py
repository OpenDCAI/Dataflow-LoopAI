"""
训练日志解析工具
用于解析训练日志中的进度信息
"""

import os
import re
from typing import Optional, Tuple, Dict


class TrainingLogParser:
    """训练日志解析器"""
    
    def __init__(self):
        # 匹配训练进度的正则表达式
        # 格式：| 101/339 [01:48<21:21,  5.39s/it]
        self.progress_pattern = re.compile(
            r'\|\s*(\d+)/(\d+)\s*\[(\d{2}:\d{2})<(\d{2}:\d{2}),\s*[\d.]+s/it\]'
        )
        self.total_steps = None  # 用于区分训练和评估进度
        
    def parse_training_progress(self, log_path: str) -> Optional[Dict[str, str]]:
        """
        解析训练日志中的进度信息
        
        Args:
            log_path: 日志文件路径
            
        Returns:
            包含进度信息的字典，格式：
            {
                'current_step': '101',
                'total_steps': '339', 
                'elapsed_time': '01:48',
                'remaining_time': '21:21',
                'progress_text': '101/339',
                'time_text': '01:48<21:21'
            }
            如果没有找到进度信息返回 None
        """
        
        if not os.path.exists(log_path):
            return None
            
        try:
            # 逆序读取文件，获取最新的进度信息
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            # 从后往前查找进度信息
            for line in reversed(lines):
                match = self.progress_pattern.search(line)
                if match:
                    current_step = match.group(1)
                    total_steps = match.group(2)
                    elapsed_time = match.group(3)
                    remaining_time = match.group(4)
                    
                    # 如果是第一次解析，记录总步数用于区分训练和评估
                    if self.total_steps is None:
                        self.total_steps = total_steps
                    
                    # 只返回与训练总步数匹配的进度（忽略评估进度）
                    if total_steps == self.total_steps:
                        return {
                            'current_step': current_step,
                            'total_steps': total_steps,
                            'elapsed_time': elapsed_time,
                            'remaining_time': remaining_time,
                            'progress_text': f"{current_step}/{total_steps}",
                            'time_text': f"{elapsed_time}<{remaining_time}"
                        }
                        
        except Exception as e:
            print(f"解析训练日志失败: {e}")
            return None
            
        return None
        
    def get_progress_percentage(self, progress_info: Dict[str, str]) -> float:
        """
        根据进度信息计算百分比
        
        Args:
            progress_info: parse_training_progress 返回的进度信息
            
        Returns:
            进度百分比 (0.0 - 1.0)
        """
        
        if not progress_info:
            return 0.0
            
        try:
            current = int(progress_info['current_step'])
            total = int(progress_info['total_steps'])
            return min(current / total, 1.0) if total > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    
    def reset(self):
        """重置解析器状态，用于新的训练任务"""
        self.total_steps = None


def parse_task_training_progress(task_id: str, logs_dir: str = None) -> Optional[Dict[str, str]]:
    """
    解析指定任务的训练进度
    
    Args:
        task_id: 任务ID
        logs_dir: 日志文件所在目录。如果不指定，默认使用 ./output/trainer/logs

    Returns:
        进度信息字典，如果没有找到则返回 None
    """
    if logs_dir is None:
        logs_dir = os.path.join(".", "output", "trainer", "logs")

    log_path = os.path.join(logs_dir, f"{task_id}.log")
    parser = TrainingLogParser()
    return parser.parse_training_progress(log_path)


def get_task_progress_percentage(task_id: str, logs_dir: str = None) -> float:
    """
    获取指定任务的训练进度百分比
    
    Args:
        task_id: 任务ID
        logs_dir: 日志文件所在目录
        
    Returns:
        进度百分比 (0.0 - 1.0)
    """
    
    progress_info = parse_task_training_progress(task_id, logs_dir=logs_dir)
    if progress_info:
        parser = TrainingLogParser()
        return parser.get_progress_percentage(progress_info)
    return 0.0