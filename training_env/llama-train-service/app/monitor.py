#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
训练监控模块 - 实时解析训练日志并绘制指标曲线
"""

import os
import sys
import re
import json
import time
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import queue

import pyqtgraph as pg
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QGridLayout
from PyQt5.QtCore import QTimer, QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont


class LogParser:
    """日志解析器"""
    
    def __init__(self):
        # 匹配训练指标的正则表达式
        self.train_pattern = re.compile(r"\{'loss': ([0-9.]+), 'grad_norm': ([0-9.]+), 'learning_rate': ([0-9.e-]+), 'epoch': ([0-9.]+)\}")
        # 匹配评估指标的正则表达式  
        self.eval_pattern = re.compile(r"\{'eval_loss': ([0-9.]+), 'eval_runtime': ([0-9.]+), 'eval_samples_per_second': ([0-9.]+), 'eval_steps_per_second': ([0-9.]+), 'epoch': ([0-9.]+)\}")
    
    def parse_line(self, line: str) -> Optional[Dict]:
        """解析单行日志，返回指标字典"""
        line = line.strip()
        
        # 尝试匹配训练指标
        train_match = self.train_pattern.search(line)
        if train_match:
            return {
                'type': 'train',
                'loss': float(train_match.group(1)),
                'grad_norm': float(train_match.group(2)),
                'learning_rate': float(train_match.group(3)),
                'epoch': float(train_match.group(4)),
                'timestamp': time.time()
            }
        
        # 尝试匹配评估指标
        eval_match = self.eval_pattern.search(line)
        if eval_match:
            return {
                'type': 'eval',
                'eval_loss': float(eval_match.group(1)),
                'eval_runtime': float(eval_match.group(2)),
                'eval_samples_per_second': float(eval_match.group(3)),
                'eval_steps_per_second': float(eval_match.group(4)),
                'epoch': float(eval_match.group(5)),
                'timestamp': time.time()
            }
        
        return None


class LogWatcher(QThread):
    """日志文件监控线程"""
    
    data_updated = pyqtSignal(dict)
    
    def __init__(self, log_file_path: str):
        super().__init__()
        self.log_file_path = log_file_path
        self.parser = LogParser()
        self.running = True
        self.last_position = 0
    
    def run(self):
        """监控日志文件变化"""
        while self.running:
            try:
                if os.path.exists(self.log_file_path):
                    with open(self.log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        f.seek(self.last_position)
                        new_lines = f.readlines()
                        self.last_position = f.tell()
                        
                        for line in new_lines:
                            metrics = self.parser.parse_line(line)
                            if metrics:
                                self.data_updated.emit(metrics)
                
                time.sleep(0.5)  # 每500ms检查一次
                
            except Exception as e:
                print(f"Log watching error: {e}")
                time.sleep(1)
    
    def stop(self):
        """停止监控"""
        self.running = False
        self.wait()


class TrainingMonitor(QMainWindow):
    """训练监控主窗口"""
    
    def __init__(self, task_id: str, log_file_path: str):
        super().__init__()
        self.task_id = task_id
        self.log_file_path = log_file_path
        
        # 数据存储
        self.train_data = {
            'epochs': [],
            'loss': [],
            'grad_norm': [],
            'learning_rate': [],
            'timestamps': []
        }
        
        self.eval_data = {
            'epochs': [],
            'eval_loss': [],
            'eval_runtime': [],
            'eval_samples_per_second': [],
            'eval_steps_per_second': [],
            'timestamps': []
        }
        
        self.init_ui()
        self.setup_plots()
        
        # 启动日志监控
        self.log_watcher = LogWatcher(log_file_path)
        self.log_watcher.data_updated.connect(self.update_data)
        self.log_watcher.start()
    
    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle(f'训练监控 - Task: {self.task_id}')
        self.setGeometry(100, 100, 1400, 900)
        
        # 创建中央窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 标题
        title_label = QLabel(f'实时训练监控 - Task ID: {self.task_id}')
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont('Arial', 16, QFont.Bold))
        main_layout.addWidget(title_label)
        
        # 图表区域 - 使用网格布局
        plots_widget = QWidget()
        plots_layout = QGridLayout()
        plots_widget.setLayout(plots_layout)
        main_layout.addWidget(plots_widget)
        
        # 创建图表区域
        self.plots_layout = plots_layout
    
    def setup_plots(self):
        """设置绘图区域"""
        # 训练指标图表
        self.loss_plot = pg.PlotWidget(title="Loss")
        self.loss_plot.setLabel('left', 'Loss')
        self.loss_plot.setLabel('bottom', 'Epoch')
        self.loss_curve = self.loss_plot.plot(pen='r', symbol='o', symbolSize=4)
        
        self.grad_norm_plot = pg.PlotWidget(title="Gradient Norm")
        self.grad_norm_plot.setLabel('left', 'Gradient Norm')
        self.grad_norm_plot.setLabel('bottom', 'Epoch')
        self.grad_norm_curve = self.grad_norm_plot.plot(pen='g', symbol='s', symbolSize=4)
        
        self.lr_plot = pg.PlotWidget(title="Learning Rate")
        self.lr_plot.setLabel('left', 'Learning Rate')
        self.lr_plot.setLabel('bottom', 'Epoch')
        self.lr_curve = self.lr_plot.plot(pen='b', symbol='^', symbolSize=4)
        
        # 评估指标图表
        self.eval_loss_plot = pg.PlotWidget(title="Evaluation Loss")
        self.eval_loss_plot.setLabel('left', 'Eval Loss')
        self.eval_loss_plot.setLabel('bottom', 'Epoch')
        self.eval_loss_curve = self.eval_loss_plot.plot(pen='m', symbol='d', symbolSize=6)
        
        self.eval_runtime_plot = pg.PlotWidget(title="Evaluation Runtime")
        self.eval_runtime_plot.setLabel('left', 'Runtime (s)')
        self.eval_runtime_plot.setLabel('bottom', 'Epoch')
        self.eval_runtime_curve = self.eval_runtime_plot.plot(pen='c', symbol='t', symbolSize=6)
        
        self.eval_sps_plot = pg.PlotWidget(title="Evaluation Samples/Second")
        self.eval_sps_plot.setLabel('left', 'Samples/Second')
        self.eval_sps_plot.setLabel('bottom', 'Epoch')
        self.eval_sps_curve = self.eval_sps_plot.plot(pen='y', symbol='p', symbolSize=6)
        
        self.eval_stps_plot = pg.PlotWidget(title="Evaluation Steps/Second")
        self.eval_stps_plot.setLabel('left', 'Steps/Second')
        self.eval_stps_plot.setLabel('bottom', 'Epoch')
        self.eval_stps_curve = self.eval_stps_plot.plot(pen='w', symbol='h', symbolSize=6)
        
        # 布局安排 (2行4列)
        self.plots_layout.addWidget(self.loss_plot, 0, 0)
        self.plots_layout.addWidget(self.grad_norm_plot, 0, 1)
        self.plots_layout.addWidget(self.lr_plot, 0, 2)
        self.plots_layout.addWidget(self.eval_loss_plot, 0, 3)
        self.plots_layout.addWidget(self.eval_runtime_plot, 1, 0)
        self.plots_layout.addWidget(self.eval_sps_plot, 1, 1)
        self.plots_layout.addWidget(self.eval_stps_plot, 1, 2)
        
        # 在最后一个位置添加状态信息
        self.status_label = QLabel("等待训练数据...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(QFont('Arial', 12))
        self.plots_layout.addWidget(self.status_label, 1, 3)
    
    def update_data(self, metrics: Dict):
        """更新数据并刷新图表"""
        try:
            if metrics['type'] == 'train':
                # 更新训练数据
                self.train_data['epochs'].append(metrics['epoch'])
                self.train_data['loss'].append(metrics['loss'])
                self.train_data['grad_norm'].append(metrics['grad_norm'])
                self.train_data['learning_rate'].append(metrics['learning_rate'])
                self.train_data['timestamps'].append(metrics['timestamp'])
                
                # 更新训练图表
                self.loss_curve.setData(self.train_data['epochs'], self.train_data['loss'])
                self.grad_norm_curve.setData(self.train_data['epochs'], self.train_data['grad_norm'])
                self.lr_curve.setData(self.train_data['epochs'], self.train_data['learning_rate'])
                
                # 更新状态
                self.status_label.setText(f"训练中...\nEpoch: {metrics['epoch']:.2f}\nLoss: {metrics['loss']:.4f}")
            
            elif metrics['type'] == 'eval':
                # 更新评估数据
                self.eval_data['epochs'].append(metrics['epoch'])
                self.eval_data['eval_loss'].append(metrics['eval_loss'])
                self.eval_data['eval_runtime'].append(metrics['eval_runtime'])
                self.eval_data['eval_samples_per_second'].append(metrics['eval_samples_per_second'])
                self.eval_data['eval_steps_per_second'].append(metrics['eval_steps_per_second'])
                self.eval_data['timestamps'].append(metrics['timestamp'])
                
                # 更新评估图表
                self.eval_loss_curve.setData(self.eval_data['epochs'], self.eval_data['eval_loss'])
                self.eval_runtime_curve.setData(self.eval_data['epochs'], self.eval_data['eval_runtime'])
                self.eval_sps_curve.setData(self.eval_data['epochs'], self.eval_data['eval_samples_per_second'])
                self.eval_stps_curve.setData(self.eval_data['epochs'], self.eval_data['eval_steps_per_second'])
                
                # 更新状态
                self.status_label.setText(f"评估完成\nEpoch: {metrics['epoch']:.2f}\nEval Loss: {metrics['eval_loss']:.4f}")
        
        except Exception as e:
            print(f"Update data error: {e}")
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        self.log_watcher.stop()
        event.accept()


def start_monitor(task_id: str, log_file_path: str) -> Optional[TrainingMonitor]:
    """启动监控窗口"""
    try:
        # 检查是否已经有QApplication实例
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        # 创建监控窗口
        monitor = TrainingMonitor(task_id, log_file_path)
        monitor.show()
        
        return monitor
        
    except Exception as e:
        print(f"Failed to start monitor: {e}")
        return None


def run_monitor_standalone(task_id: str, log_file_path: str):
    """独立运行监控器（用于测试）"""
    app = QApplication(sys.argv)
    monitor = TrainingMonitor(task_id, log_file_path)
    monitor.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    # 测试用法
    if len(sys.argv) > 2:
        task_id = sys.argv[1]
        log_file_path = sys.argv[2]
        run_monitor_standalone(task_id, log_file_path)
    else:
        print("Usage: python monitor.py <task_id> <log_file_path>")
