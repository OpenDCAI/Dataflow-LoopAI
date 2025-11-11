# %%
"""
TrainerAgent 测试脚本
演示如何使用 TrainerAgent 进行模型训练
"""

from loopai.agents import TrainerAgent
from loopai.memory import checkpointer, store
from rich.console import Console
from rich.live import Live
from rich.text import Text
import json
import os

console = Console()

# 创建测试数据集（示例）
def create_sample_dataset():
    """创建一个示例数据集用于测试"""
    sample_data = [
        {
            "instruction": "请计算 2 + 2 的结果",
            "input": "",
            "output": "2 + 2 = 4"
        },
        {
            "instruction": "解释什么是机器学习",
            "input": "", 
            "output": "机器学习是一种人工智能技术，它使计算机能够从数据中学习并做出预测或决策，而无需显式编程。"
        },
        {
            "instruction": "将以下句子翻译成英文",
            "input": "你好，世界！",
            "output": "Hello, world!"
        },
        {
            "conversations": [
                {"from": "human", "value": "你是谁？"},
                {"from": "gpt", "value": "我是一个AI助手，很高兴为您提供帮助。"}
            ]
        },
        {
            "conversations": [
                {"from": "human", "value": "今天天气如何？"},
                {"from": "gpt", "value": "抱歉，我无法获取实时天气信息。建议您查看当地天气预报。"}
            ]
        }
    ]
    
    # 创建测试目录
    test_dir = "./test_data"
    os.makedirs(test_dir, exist_ok=True)
    
    # 保存 JSON 格式数据集
    json_path = os.path.join(test_dir, "sample_dataset.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(sample_data, f, ensure_ascii=False, indent=2)
    
    # 保存 JSONL 格式数据集
    jsonl_path = os.path.join(test_dir, "sample_dataset.jsonl")
    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for item in sample_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    console.print(f"[green]✅ 测试数据集已创建:[/green]")
    console.print(f"  JSON 格式: {json_path}")
    console.print(f"  JSONL 格式: {jsonl_path}")
    
    return json_path, jsonl_path

# 创建示例数据集
json_dataset_path, jsonl_dataset_path = create_sample_dataset()

# 创建 TrainerAgent 实例
trainer = TrainerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "trainer_test_1"}}

# 构建图
graph = trainer()

# 准备训练状态
training_state = {
    # 必需字段
    'train_dataset_path': json_dataset_path,  # 使用 JSON 格式数据集
    'train_task_description': '训练一个能够回答简单问题和进行对话的AI助手模型，主要用于日常对话和基础问答任务',
    
    # 可选字段（如果不提供将使用默认值）
    'train_model_name': 'qwen2.5-7b-instruct',
    'train_output_dir': './output/training_test',
    'train_use_swanlab': True,
    'train_swanlab_project': 'test_llamafactory_training',
    'output_dir': './output/trainer_test'
}

console.print("[bold blue]开始 TrainerAgent 测试...[/bold blue]")
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
            status = stage_info.get('passed') or stage_info.get('success')
            console.print(f"  {stage_name}: {'✅' if status else '❌'}")
            
            if stage_info.get('error'):
                console.print(f"    错误: {stage_info['error']}")
        
        console.print(f"\n[yellow]生成的文件:[/yellow]")
        for file_path in summary['output_files']:
            console.print(f"  📄 {file_path}")
        
        # 显示 SwanLab 链接（如果有）
        if result.get('swanlab_url'):
            console.print(f"\n[bold cyan]📊 SwanLab 监控: {result['swanlab_url']}[/bold cyan]")
        
    except Exception as e:
        console.print(f"[bold red]❌ 训练流程执行失败: {str(e)}[/bold red]")

else:
    console.print(f"[red]❌ 输入验证失败，无法执行训练[/red]")

# %%
console.print("[bold yellow]测试完成![/bold yellow]")
