#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
独立监控启动脚本
"""

import os
import sys
import argparse
import subprocess
from typing import Optional

def find_log_file(task_id: str, logs_dir: str = None) -> Optional[str]:
    """查找指定任务的日志文件"""
    if logs_dir is None:
        # 默认日志目录
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(base_dir, "logs")
    
    log_file = os.path.join(logs_dir, f"{task_id}.log")
    return log_file if os.path.exists(log_file) or True else None  # 允许预创建


def start_monitor_process(task_id: str, log_file_path: str) -> subprocess.Popen:
    """启动监控进程"""
    monitor_script = os.path.join(os.path.dirname(__file__), "monitor.py")
    cmd = [sys.executable, monitor_script, task_id, log_file_path]
    
    # 在Windows上，使用CREATE_NEW_PROCESS_GROUP标志
    if os.name == 'nt':
        process = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    else:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
    
    return process


def main():
    parser = argparse.ArgumentParser(description='启动训练监控')
    parser.add_argument('task_id', help='任务ID')
    parser.add_argument('--logs-dir', default=None, help='日志目录路径')
    parser.add_argument('--wait', action='store_true', help='等待监控进程结束')
    
    args = parser.parse_args()
    
    # 查找日志文件
    log_file_path = find_log_file(args.task_id, args.logs_dir)
    if not log_file_path:
        print(f"Error: Log file for task {args.task_id} not found")
        return 1
    
    print(f"Starting monitor for task: {args.task_id}")
    print(f"Log file: {log_file_path}")
    
    try:
        process = start_monitor_process(args.task_id, log_file_path)
        print(f"Monitor process started with PID: {process.pid}")
        
        if args.wait:
            process.wait()
        
        return 0
        
    except Exception as e:
        print(f"Error starting monitor: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
