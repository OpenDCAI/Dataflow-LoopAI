#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试监控功能的脚本
"""

import os
import sys
import time
import threading
import tempfile

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

def create_fake_log(log_file_path: str):
    """创建模拟训练日志用于测试"""
    
    training_logs = [
        "Starting training...",
        "{'loss': 2.3456, 'grad_norm': 1.2345, 'learning_rate': 2e-05, 'epoch': 0.1}",
        "{'loss': 2.1234, 'grad_norm': 1.1234, 'learning_rate': 1.9e-05, 'epoch': 0.2}",
        "{'loss': 1.9876, 'grad_norm': 1.0123, 'learning_rate': 1.8e-05, 'epoch': 0.3}",
        "{'loss': 1.8543, 'grad_norm': 0.9876, 'learning_rate': 1.7e-05, 'epoch': 0.4}",
        "{'loss': 1.7321, 'grad_norm': 0.9543, 'learning_rate': 1.6e-05, 'epoch': 0.5}",
        "Evaluating model...",
        "{'eval_loss': 1.6543, 'eval_runtime': 45.23, 'eval_samples_per_second': 22.1, 'eval_steps_per_second': 5.5, 'epoch': 0.5}",
        "{'loss': 1.6123, 'grad_norm': 0.9321, 'learning_rate': 1.5e-05, 'epoch': 0.6}",
        "{'loss': 1.5432, 'grad_norm': 0.9123, 'learning_rate': 1.4e-05, 'epoch': 0.7}",
        "{'loss': 1.4567, 'grad_norm': 0.8945, 'learning_rate': 1.3e-05, 'epoch': 0.8}",
        "{'loss': 1.3789, 'grad_norm': 0.8765, 'learning_rate': 1.2e-05, 'epoch': 0.9}",
        "{'loss': 1.2987, 'grad_norm': 0.8543, 'learning_rate': 1.1e-05, 'epoch': 1.0}",
        "{'eval_loss': 1.2345, 'eval_runtime': 44.56, 'eval_samples_per_second': 22.5, 'eval_steps_per_second': 5.6, 'epoch': 1.0}",
        "{'loss': 1.2234, 'grad_norm': 0.8321, 'learning_rate': 1.0e-05, 'epoch': 1.1}",
        "{'loss': 1.1567, 'grad_norm': 0.8123, 'learning_rate': 9.5e-06, 'epoch': 1.2}",
        "{'loss': 1.0987, 'grad_norm': 0.7945, 'learning_rate': 9.0e-06, 'epoch': 1.3}",
        "{'loss': 1.0432, 'grad_norm': 0.7765, 'learning_rate': 8.5e-06, 'epoch': 1.4}",
        "{'loss': 0.9876, 'grad_norm': 0.7543, 'learning_rate': 8.0e-06, 'epoch': 1.5}",
        "{'eval_loss': 0.9543, 'eval_runtime': 43.78, 'eval_samples_per_second': 22.8, 'eval_steps_per_second': 5.7, 'epoch': 1.5}",
    ]
    
    def write_logs():
        with open(log_file_path, 'w', encoding='utf-8') as f:
            for log_line in training_logs:
                f.write(log_line + '\n')
                f.flush()
                time.sleep(2)  # 每2秒写入一行，模拟实时训练
        
        print(f"✅ Finished writing fake logs to {log_file_path}")
    
    # 在新线程中写入日志
    thread = threading.Thread(target=write_logs, daemon=True)
    thread.start()
    return thread


def test_monitor():
    """测试监控功能"""
    print("🧪 Testing training monitor...")
    
    # 创建临时日志文件
    temp_dir = tempfile.mkdtemp()
    log_file_path = os.path.join(temp_dir, "test_task.log")
    
    print(f"📁 Temp log file: {log_file_path}")
    
    # 启动模拟日志写入
    log_thread = create_fake_log(log_file_path)
    
    # 启动监控
    try:
        from monitor import run_monitor_standalone
        print("🚀 Starting monitor window...")
        run_monitor_standalone("test_task", log_file_path)
    except ImportError:
        print("❌ Failed to import monitor module")
        return False
    except Exception as e:
        print(f"❌ Error running monitor: {e}")
        return False
    
    return True


if __name__ == "__main__":
    test_monitor()
