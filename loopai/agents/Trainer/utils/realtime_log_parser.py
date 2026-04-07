"""
实时日志解析器
用于从训练日志中实时解析关键指标并保存到JSON文件
从 api/app/utils/train/realtime_log_parser.py 迁移
"""

import os
import json
import re
import threading
import time
from typing import Dict, List, Optional, Any
from datetime import datetime


class MetricsExtractor:
    """指标提取器，负责从日志行中提取各种训练指标"""

    def __init__(self, total_steps: int = 0, total_epochs: int = 0):
        self.total_steps = total_steps
        self.total_epochs = total_epochs
        # JSON格式指标的正则表达式，匹配像 {'loss': 0.1469, 'epoch': 2.92} 这样的内容
        self.json_pattern = re.compile(r'\{[^}]*\}')

        # 更完善的数字匹配模式，支持科学计数法、负数等
        number_pattern = r'[-+]?(?:\d*\.?\d+(?:[eE][-+]?\d+)?)'

        # 单个指标的正则表达式
        self.loss_pattern = re.compile(rf'loss[:\s=]+({number_pattern})', re.IGNORECASE)
        self.epoch_pattern = re.compile(rf'epoch[:\s=]+({number_pattern})', re.IGNORECASE)
        self.lr_pattern = re.compile(rf'(?:learning_rate|lr)[:\s=]+({number_pattern})', re.IGNORECASE)
        self.grad_norm_pattern = re.compile(rf'grad_norm[:\s=]+({number_pattern})', re.IGNORECASE)
        self.eval_loss_pattern = re.compile(rf'eval_loss[:\s=]+({number_pattern})', re.IGNORECASE)

        # step通常是整数，但也可能有小数点
        self.step_pattern = re.compile(rf'step[:\s=]+({number_pattern})', re.IGNORECASE)

        # 更多常见的训练指标
        self.accuracy_pattern = re.compile(rf'(?:accuracy|acc)[:\s=]+({number_pattern})', re.IGNORECASE)
        self.perplexity_pattern = re.compile(rf'(?:perplexity|ppl)[:\s=]+({number_pattern})', re.IGNORECASE)

    def extract_metrics(self, line: str) -> Dict[str, Any]:
        """从日志行中提取指标（同时支持 LlamaFactory 和 verl 日志格式）"""
        metrics = {}

        # verl file logger 格式: {"step": N, "data": {"metric/name": value}}
        line_stripped = line.strip()
        if line_stripped.startswith('{') and '"step"' in line_stripped and '"data"' in line_stripped:
            try:
                parsed = json.loads(line_stripped)
                if isinstance(parsed, dict) and "step" in parsed and "data" in parsed:
                    metrics["step"] = parsed["step"]
                    data = parsed["data"]
                    if isinstance(data, dict):
                        for key, value in data.items():
                            if isinstance(value, (int, float)):
                                # verl 指标格式: "train/loss" -> "loss", "train/lr" -> "lr"
                                short_key = key.split("/")[-1] if "/" in key else key
                                metrics[short_key] = value
                                # 也保留原始 key 方便后续使用
                                metrics[key] = value
                    return metrics
            except (json.JSONDecodeError, KeyError):
                pass

        # 首先尝试提取JSON格式的指标（LlamaFactory 格式）
        json_matches = self.json_pattern.findall(line)
        for json_str in json_matches:
            try:
                cleaned_json = json_str.replace("'", '"')
                parsed = json.loads(cleaned_json)
                if isinstance(parsed, dict):
                    for key, value in parsed.items():
                        if isinstance(value, (int, float)):
                            metrics[key] = value
                            if 'epoch' in metrics and 'step' not in metrics:
                                metrics['step'] = int(metrics['epoch'] * self.total_steps / self.total_epochs) if self.total_epochs > 0 else None
            except json.JSONDecodeError:
                continue

        # 如果没有找到JSON格式，尝试单独提取各个指标
        if not metrics:
            patterns = {
                'loss': self.loss_pattern,
                'epoch': self.epoch_pattern,
                'learning_rate': self.lr_pattern,
                'grad_norm': self.grad_norm_pattern,
                'step': self.step_pattern,
                'eval_loss': self.eval_loss_pattern,
                'accuracy': self.accuracy_pattern,
                'perplexity': self.perplexity_pattern,
            }

            for metric_name, pattern in patterns.items():
                match = pattern.search(line)
                if match:
                    try:
                        value_str = match.group(1)
                        if metric_name == 'step':
                            value = int(float(value_str))
                        else:
                            value = float(value_str)
                        metrics[metric_name] = value
                    except (ValueError, OverflowError) as e:
                        print(f"无法解析 {metric_name} 的值 '{value_str}': {e}")
                        continue

        return metrics


class RealTimeLogParser:
    """实时日志解析器"""

    def __init__(self, log_path: str, metrics_file: str):
        self.log_path = log_path
        self.metrics_file = metrics_file
        self.metrics_data = []
        self.running = False
        self.thread = None
        self.file_position = 0
        self.total_steps = 0
        self.if_total_steps_recorded = False
        self.total_epochs = 0
        self.if_total_epochs_recorded = False

        # 确保指标文件目录存在
        os.makedirs(os.path.dirname(metrics_file), exist_ok=True)

    def _find_total_steps(self) -> Optional[int]:
        """尝试从日志文件中找到训练的总步数"""
        try:
            if not os.path.exists(self.log_path):
                return None

            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    step_match = re.search(r'Total optimization steps = (\d+)', line)
                    epoch_match = re.search(r'Num Epochs = (\d+)', line)
                    if step_match:
                        self.total_steps = int(step_match.group(1))
                        self.if_total_steps_recorded = True
                    if epoch_match:
                        self.total_epochs = int(epoch_match.group(1))
                        self.if_total_epochs_recorded = True
        except Exception as e:
            print(f"读取日志文件以获取总步数时出错: {e}")

        return None

    def _initialize_metrics_file(self):
        """初始化指标文件"""
        initial_data = {
            "task_info": {
                "log_path": self.log_path,
                "total_steps": self.total_steps,
                "total_epochs": self.total_epochs,
                "start_time": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat()
            },
            "metrics": []
        }

        with open(self.metrics_file, 'w', encoding='utf-8') as f:
            json.dump(initial_data, f, ensure_ascii=False, indent=2)

    def _save_metrics(self, new_metrics: List[Dict[str, Any]]):
        """保存新的指标数据到文件"""
        try:
            with open(self.metrics_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data["metrics"].extend(new_metrics)
            data["task_info"]["last_updated"] = datetime.now().isoformat()
            data["task_info"]["total_metrics"] = len(data["metrics"])

            with open(self.metrics_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"保存指标数据时出错: {e}")

    def _parse_new_lines(self) -> List[Dict[str, Any]]:
        """解析新的日志行"""
        new_metrics = []

        try:
            if not os.path.exists(self.log_path):
                return new_metrics

            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                f.seek(self.file_position)
                new_lines = f.readlines()
                self.file_position = f.tell()

                for line in new_lines:
                    line = line.strip()
                    if line:
                        metrics = self.extractor.extract_metrics(line)
                        if metrics:
                            metrics['timestamp'] = datetime.now().isoformat()
                            metrics['log_line'] = line
                            new_metrics.append(metrics)

        except Exception as e:
            print(f"解析日志行时出错: {e}")

        return new_metrics

    def _monitoring_loop(self):
        """监控循环"""
        while self.running:
            try:
                new_metrics = self._parse_new_lines()
                if new_metrics:
                    self._save_metrics(new_metrics)

                time.sleep(1)  # 每秒检查一次

            except Exception as e:
                print(f"监控循环中出错: {e}")
                time.sleep(5)

    def start_monitoring(self):
        """开始监控"""
        if self.running:
            return

        self.running = True
        while not self.if_total_steps_recorded:
            self._find_total_steps()
            time.sleep(1)
        self._initialize_metrics_file()
        self.extractor = MetricsExtractor(total_steps=self.total_steps, total_epochs=self.total_epochs)
        self.thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.thread.start()
        print(f"开始监控日志文件: {self.log_path}")

    def stop_monitoring(self):
        """停止监控"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        print("已停止日志监控")

    def get_latest_metrics(self, count: int = 10) -> List[Dict[str, Any]]:
        """获取最新的指标"""
        try:
            with open(self.metrics_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                metrics = data.get("metrics", [])
                return metrics[-count:] if count > 0 else metrics
        except Exception as e:
            print(f"读取指标数据时出错: {e}")
            return []

    def get_metrics_summary(self) -> Dict[str, Any]:
        """获取指标汇总"""
        try:
            with open(self.metrics_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                metrics = data.get("metrics", [])

                if not metrics:
                    return {"total_count": 0}

                summary = {
                    "total_count": len(metrics),
                    "first_timestamp": metrics[0].get("timestamp"),
                    "last_timestamp": metrics[-1].get("timestamp"),
                    "available_metrics": set()
                }

                latest_values = {}
                for metric in metrics:
                    for key, value in metric.items():
                        if key not in ['timestamp', 'log_line']:
                            summary["available_metrics"].add(key)
                            latest_values[key] = value

                summary["available_metrics"] = list(summary["available_metrics"])
                summary["latest_values"] = latest_values

                return summary

        except Exception as e:
            print(f"生成指标汇总时出错: {e}")
            return {"error": str(e)}
