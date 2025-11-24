#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
训练监控工具 - 便于使用的启动脚本
"""

import os
import sys
import argparse
import glob
from typing import List


def find_all_tasks(logs_dir: str = None) -> List[tuple]:
    """查找所有可用的任务"""
    if logs_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(base_dir, "logs")
    
    if not os.path.exists(logs_dir):
        return []
    
    log_files = glob.glob(os.path.join(logs_dir, "*.log"))
    tasks = []
    
    for log_file in log_files:
        task_id = os.path.splitext(os.path.basename(log_file))[0]
        file_size = os.path.getsize(log_file)
        mtime = os.path.getmtime(log_file)
        tasks.append((task_id, log_file, file_size, mtime))
    
    # 按修改时间排序（最新的在前面）
    tasks.sort(key=lambda x: x[3], reverse=True)
    return tasks


def main():
    parser = argparse.ArgumentParser(
        description='训练监控工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python monitor_tool.py                    # 列出所有可用的任务
  python monitor_tool.py <task_id>         # 监控指定任务
  python monitor_tool.py --test            # 运行测试模式
        """
    )
    
    parser.add_argument('task_id', nargs='?', help='要监控的任务ID')
    parser.add_argument('--logs-dir', default=None, help='日志目录路径')
    parser.add_argument('--test', action='store_true', help='运行测试模式')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有任务')
    
    args = parser.parse_args()
    
    if args.test:
        # 运行测试
        print("🧪 Running test mode...")
        try:
            from test_monitor import test_monitor
            return 0 if test_monitor() else 1
        except ImportError:
            print("❌ Test module not found")
            return 1
    
    # 查找所有任务
    tasks = find_all_tasks(args.logs_dir)
    
    if not tasks:
        print("❌ No training tasks found in logs directory")
        return 1
    
    # 如果没有指定任务ID或者要求列出任务，显示所有任务
    if not args.task_id or args.list:
        print("\n📋 Available training tasks:")
        print("-" * 80)
        print(f"{'Task ID':<20} {'File Size':<12} {'Last Modified':<20} {'Log File'}")
        print("-" * 80)
        
        import datetime
        for task_id, log_file, file_size, mtime in tasks:
            size_str = f"{file_size/1024:.1f}KB" if file_size < 1024*1024 else f"{file_size/(1024*1024):.1f}MB"
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{task_id:<20} {size_str:<12} {mtime_str:<20} {os.path.basename(log_file)}")
        
        if not args.task_id:
            print(f"\n💡 Use 'python {os.path.basename(__file__)} <task_id>' to start monitoring")
            return 0
    
    # 启动监控
    task_id = args.task_id
    
    # 查找指定的任务
    task_found = False
    log_file_path = None
    
    for t_id, t_log_file, _, _ in tasks:
        if t_id == task_id:
            task_found = True
            log_file_path = t_log_file
            break
    
    if not task_found:
        print(f"❌ Task '{task_id}' not found")
        print("Available tasks:")
        for t_id, _, _, _ in tasks[:5]:  # 显示最近5个任务
            print(f"  - {t_id}")
        return 1
    
    print(f"🚀 Starting monitor for task: {task_id}")
    print(f"📁 Log file: {log_file_path}")
    
    try:
        from monitor import run_monitor_standalone
        run_monitor_standalone(task_id, log_file_path)
        return 0
    except Exception as e:
        print(f"❌ Error starting monitor: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
