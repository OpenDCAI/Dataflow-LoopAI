"""
训练日志解析工具
用于解析训练日志中的进度信息
"""

import os
import json
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
        self.verl_step_pattern = re.compile(r'(?:^|\s-\s)step:(\d+)')
        self.verl_total_pattern = re.compile(
            r'(?:total_training_steps|total steps)\s*[:=]\s*([\d,]+)',
            re.IGNORECASE,
        )
        self.tqdm_step_pattern = re.compile(
            r'Training Progress:.*?\|\s*(\d+)/(\d+)\s*\[',
            re.IGNORECASE,
        )

    def parse_verl_metrics_progress(
        self,
        log_path: str,
        metrics_path: str,
    ) -> Optional[Dict[str, str]]:
        """Read Verl's structured JSONL metrics instead of its tqdm output."""
        if not os.path.isfile(metrics_path):
            return None

        total_steps = None
        if os.path.isfile(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
                    for line in log_file:
                        for match in self.verl_total_pattern.finditer(line):
                            total_steps = int(match.group(1).replace(",", ""))
                        for match in self.tqdm_step_pattern.finditer(line):
                            total_steps = int(match.group(2))
            except OSError:
                total_steps = None

        try:
            with open(metrics_path, "r", encoding="utf-8", errors="ignore") as metrics_file:
                lines = metrics_file.readlines()
        except OSError:
            return None

        current_step = None
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            data = record.get("data") if isinstance(record.get("data"), dict) else record
            raw_step = data.get("training/global_step", data.get("global_step", record.get("step")))
            try:
                current_step = int(float(raw_step))
            except (TypeError, ValueError, OverflowError):
                continue
            break

        if current_step is None or not total_steps:
            return None
        return {
            "current_step": str(current_step),
            "total_steps": str(total_steps),
            "elapsed_time": "",
            "remaining_time": "",
            "progress_text": f"{current_step}/{total_steps}",
            "time_text": "Verl metrics",
        }
        
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
            
            # tqdm uses carriage returns, so many refreshes can occupy one
            # logical line. Pick the last match for the largest total (the
            # training loop) instead of the first match such as 1/58.
            progress_matches = [
                match
                for line in lines
                for match in self.progress_pattern.finditer(line)
            ]
            if progress_matches:
                training_total = max(int(match.group(2)) for match in progress_matches)
                match = next(
                    match
                    for match in reversed(progress_matches)
                    if int(match.group(2)) == training_total
                )
                current_step = match.group(1)
                total_steps = match.group(2)
                elapsed_time = match.group(3)
                remaining_time = match.group(4)
                self.total_steps = total_steps
                return {
                    'current_step': current_step,
                    'total_steps': total_steps,
                    'elapsed_time': elapsed_time,
                    'remaining_time': remaining_time,
                    'progress_text': f"{current_step}/{total_steps}",
                    'time_text': f"{elapsed_time}<{remaining_time}"
                }

            total_steps = None
            for line in lines:
                total_match = self.verl_total_pattern.search(line)
                if total_match:
                    total_steps = int(total_match.group(1).replace(',', ''))
            if total_steps:
                for line in reversed(lines):
                    step_match = self.verl_step_pattern.search(line)
                    if step_match:
                        current_step = int(step_match.group(1))
                        return {
                            'current_step': str(current_step),
                            'total_steps': str(total_steps),
                            'elapsed_time': '',
                            'remaining_time': '',
                            'progress_text': f"{current_step}/{total_steps}",
                            'time_text': 'Verl GRPO',
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
