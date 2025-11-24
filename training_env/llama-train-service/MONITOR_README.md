# 训练监控功能

本模块为 `llama-train-service` 提供了实时训练监控功能，能够从训练日志中解析出关键指标并实时绘制曲线。

## 功能特性

- 🔄 **实时监控**: 实时解析训练日志，无需手动刷新
- 📊 **多指标展示**: 支持8个关键训练指标的可视化
- 🎨 **美观界面**: 使用PyQtGraph绘制高质量的实时曲线图
- 🚀 **自动启动**: 与训练任务同步启动，无需手动操作
- 💻 **独立进程**: 监控窗口在独立进程中运行，不影响主服务

## 支持的指标

### 训练指标
- **Loss**: 训练损失
- **Gradient Norm**: 梯度范数  
- **Learning Rate**: 学习率
- **Epoch**: 训练轮数

### 评估指标
- **Eval Loss**: 验证损失
- **Eval Runtime**: 评估运行时间
- **Eval Samples/Second**: 每秒评估样本数
- **Eval Steps/Second**: 每秒评估步数

## 安装依赖

### 自动安装
```batch
cd training_env\llama-train-service
setup_monitor.bat
```

### 手动安装
```bash
pip install PyQt5==5.15.9 pyqtgraph==0.13.3
```

## 使用方法

### 1. 自动启动（推荐）

当你通过API启动训练任务时，监控窗口会自动弹出：

```python
# 发送训练请求到API
response = requests.post("http://localhost:8000/train", json={
    "config": "...",  # 你的训练配置
    "task_name": "my_training_task"
})
```

监控窗口会自动启动并开始监控该任务的训练日志。

### 2. 手动启动监控

#### 列出所有可用任务
```bash
python monitor_tool.py --list
```

#### 监控特定任务
```bash
python monitor_tool.py <task_id>
```

#### 运行测试
```bash
python monitor_tool.py --test
```

### 3. 高级用法

#### 指定日志目录
```bash
python monitor_tool.py <task_id> --logs-dir /path/to/logs
```

#### 独立启动监控进程
```bash
python app/start_monitor.py <task_id>
```

## 日志格式要求

监控器能够解析以下格式的日志行：

### 训练指标格式
```
{'loss': 0.1624, 'grad_norm': 1.0218281571925996, 'learning_rate': 1.8018902370984524e-05, 'epoch': 1.89}
```

### 评估指标格式  
```
{'eval_loss': 0.22724655270576477, 'eval_runtime': 44.5956, 'eval_samples_per_second': 21.146, 'eval_steps_per_second': 5.292, 'epoch': 1.89}
```

## 界面说明

监控窗口采用2行4列的网格布局：

**第一行：训练指标**
- Loss曲线 (红色)
- Gradient Norm曲线 (绿色)  
- Learning Rate曲线 (蓝色)
- Eval Loss曲线 (紫色)

**第二行：评估指标**
- Eval Runtime曲线 (青色)
- Eval Samples/Second曲线 (黄色)
- Eval Steps/Second曲线 (白色)
- 状态信息面板

## 故障排除

### 1. 监控窗口没有出现
- 检查是否安装了PyQt5和pyqtgraph
- 确认系统支持GUI显示
- 查看控制台错误信息

### 2. 没有数据显示
- 确认日志文件存在且有内容
- 检查日志格式是否正确
- 确认训练任务正在运行

### 3. 图表显示异常
- 重启监控窗口
- 检查数据是否包含异常值
- 确认PyQtGraph版本兼容性

## 文件结构

```
app/
├── monitor.py          # 核心监控模块
├── start_monitor.py    # 独立监控启动器
└── test_monitor.py     # 测试模块

monitor_tool.py         # 便捷工具脚本
setup_monitor.bat       # 自动安装脚本
```

## 技术实现

- **日志解析**: 使用正则表达式实时解析训练日志
- **数据更新**: 通过Qt信号机制实现线程安全的数据更新
- **图表绘制**: PyQtGraph提供高性能的实时绘图能力
- **进程管理**: 独立进程运行，避免阻塞主服务

## 许可证

本项目遵循与主项目相同的许可证。
