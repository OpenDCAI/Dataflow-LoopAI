# %%
"""
TrainerAgent 测试脚本
演示如何使用 TrainerAgent 进行模型训练

新功能特性：
1. 配置生成：直接生成YAML格式的配置文件，基于qwen2.5-coder模板
2. 远程训练：调用远程训练服务而不是本地训练
3. 任务监控：支持训练任务ID跟踪和状态监控
4. 服务检查：自动检查训练服务可用性

使用说明：
- 确保训练服务已启动: cd api && python start.py
- 脚本会生成YAML格式的训练配置文件
- 训练任务将提交到远程服务执行
"""

from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store
from rich.console import Console
from rich.live import Live
from rich.text import Text
import json
import os
import requests

console = Console()

def check_training_service(service_url: str = "http://localhost:8000") -> bool:
    """检查训练服务是否可用"""
    try:
        response = requests.get(service_url, timeout=5)
        return response.status_code == 200
    except Exception:
        return False

# 创建 TrainerAgent 实例
trainer = TrainerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "trainer_test_1"}}

# 构建图
graph = trainer()

# 准备训练状态
training_state = {
    # 必需字段
    'train_input_dataset_path': "/jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory/data/alpaca_en_demo.json",  # 使用 JSON 格式数据集
    'train_input_task_description': '训练一个能够回答简单问题和进行对话的AI助手模型，主要用于日常对话和基础问答任务',
    'train_input_config_template_path': "loopai/agents/Trainer/templates/qwen2_5_coder_bird_full_sft.yaml",
    'train_input_model_name': '/jizhicfs/hymiezhao/models/Qwen2.5-1.5B',
    'train_output_dir': './output/training_test',

    # 可选字段（如果不提供将使用默认值）
    'train_input_use_swanlab': True,
    'train_input_swanlab_project': 'test_llamafactory_training',
    'training_service_url': 'http://localhost:8000',  # 远程训练服务地址
    'output_dir': './output/trainer_test'
}

console.print("[bold blue]开始 TrainerAgent 测试...[/bold blue]")

# 检查训练服务状态
training_service_url = training_state.get('training_service_url', 'http://localhost:8000')
service_available = check_training_service(training_service_url)
console.print(f"\n[yellow]训练服务状态:[/yellow]")
console.print(f"  服务地址: {training_service_url}")
console.print(f"  服务状态: {'🟢 可用' if service_available else '🔴 不可用'}")

if not service_available:
    console.print(f"[orange]⚠️  注意: 训练服务不可用，训练将会失败[/orange]")
    console.print(f"[orange]   请确保训练服务已启动: cd api && python start.py[/orange]")

console.print("\n[yellow]训练配置:[/yellow]")
for key, value in training_state.items():
    console.print(f"  {key}: {value}")

# %%
# 验证输入状态
validation_result = trainer.validate_input_state(training_state)

console.print(f"\n[yellow]输入验证结果:[/yellow]")
console.print(f"  有效: {'✅' if validation_result['valid'] else '❌'}")

if validation_result.get('errors'):
    console.print("  [red]错误:[/red]")
    for error in validation_result['errors']:
        console.print(f"    - {error}")

if validation_result.get('warnings'):
    console.print("  [orange]警告:[/orange]")
    for warning in validation_result['warnings']:
        console.print(f"    - {warning}")

# %%
# 执行训练（如果验证通过）
if validation_result['valid']:
    try:
        console.print(f"\n[bold green]🚀 开始执行训练流程...[/bold green]")
        
        # 执行图
        result = graph.invoke(training_state, config=config)
        
        console.print(f"\n[bold green]✅ 训练流程执行完成![/bold green]")
        
        # 获取训练摘要
        summary = trainer.get_training_summary(result)
        
        console.print(f"\n[yellow]训练摘要:[/yellow]")
        console.print(f"  最终状态: {'✅ 成功' if summary['final_status'] == 'success' else '❌ 失败'}")
        
        console.print(f"\n[yellow]各阶段执行情况:[/yellow]")
        for stage_name, stage_info in summary['stages'].items():
            if stage_name == 'data_check':
                status = stage_info.get('passed')
            elif stage_name == 'config_generation':
                status = stage_info.get('success')
            elif stage_name == 'training_execution':
                status = stage_info.get('success')
            else:
                status = stage_info.get('passed') or stage_info.get('success')
            
            console.print(f"  {stage_name}: {'✅' if status else '❌'}")
            
            # 显示阶段特定信息
            if stage_name == 'config_generation' and status:
                config_path = stage_info.get('config_path')
                if config_path and config_path.endswith('.yaml'):
                    console.print(f"    📄 生成YAML配置: {config_path}")
            
            elif stage_name == 'training_execution' and status:
                task_id = stage_info.get('task_id')
                final_status = stage_info.get('final_status')
                train_output_swanlab_log_path = stage_info.get('train_output_swanlab_log_path')
                if task_id:
                    console.print(f"    🆔 训练任务ID: {task_id}")
                if final_status:
                    status_text = final_status.get('status', '未知')
                    console.print(f"    📊 最终状态: {status_text}")
                if train_output_swanlab_log_path:
                    console.print(f"    📜 SwanLab 日志: {train_output_swanlab_log_path}")

            if stage_info.get('error'):
                console.print(f"    ❌ 错误: {stage_info['error']}")
        
        console.print(f"\n[yellow]生成的文件:[/yellow]")
        for file_path in summary['output_files']:
            file_type = "YAML配置" if file_path.endswith('.yaml') else "文件"
            console.print(f"  📄 {file_type}: {file_path}")
        
        # 显示远程训练信息
        training_info = summary['stages'].get('training_execution', {})
        if training_info.get('task_id'):
            console.print(f"\n[bold cyan]🚀 远程训练信息:[/bold cyan]")
            console.print(f"  任务ID: {training_info['task_id']}")
            console.print(f"  训练服务: {training_state.get('training_service_url', 'http://localhost:8000')}")
            
            if training_info.get('training_time'):
                console.print(f"  执行时间: {training_info['training_time']:.2f} 秒")
            
            # 如果训练失败，显示日志路径
            if training_info.get('log_path'):
                console.print(f"  📋 训练日志: {training_info['log_path']}")
        
        # 显示 SwanLab 链接（如果有，虽然现在主要通过远程服务）
        if result.get('swanlab_url'):
            console.print(f"\n[bold cyan]📊 SwanLab 监控: {result['swanlab_url']}[/bold cyan]")
        
    except Exception as e:
        console.print(f"[bold red]❌ 训练流程执行失败: {str(e)}[/bold red]")

else:
    console.print(f"[red]❌ 输入验证失败，无法执行训练[/red]")

# %%
console.print("[bold yellow]测试完成![/bold yellow]")
