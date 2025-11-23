"""
TrainerAgent 简化测试脚本
快速测试三个节点的基本功能
"""

import os
import sys
import json
import tempfile
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loopai.states.base import LoopAIState


def create_test_environment():
    """创建测试环境"""
    # temp_dir = tempfile.mkdtemp()
    temp_dir = "D:\\MyProject\\Dataflow-LoopAI\\temp"
    os.makedirs(temp_dir, exist_ok=True)
    
    # 创建测试数据集
    test_dataset_path = os.path.join(temp_dir, "test_dataset.jsonl")
    test_data = [
        {
            "instruction": "编写一个计算阶乘的函数",
            "input": "",
            "output": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n-1)"
        },
        {
            "instruction": "实现快速排序算法",
            "input": "",
            "output": "def quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + middle + quicksort(right)"
        }
    ]
    
    with open(test_dataset_path, 'w', encoding='utf-8') as f:
        for item in test_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    return temp_dir, test_dataset_path


def test_data_check_node():
    """测试数据检查节点"""
    print("\n" + "="*50)
    print("测试数据检查节点")
    print("="*50)
    
    temp_dir, dataset_path = create_test_environment()
    
    try:
        from loopai.agents.Trainer.nodes import data_check_node
        
        # 创建测试状态
        state = LoopAIState(
            task_id="test_data_check",
            train_dataset_path=dataset_path,
            output_dir=os.path.join(temp_dir, "output")
        )
        
        print(f"📁 数据集路径: {dataset_path}")
        print(f"📂 输出目录: {state.get('output_dir')}")
        
        # 执行数据检查
        result_state = data_check_node(state)
        
        # 显示结果
        if result_state.get('data_check_passed'):
            print("✅ 数据检查通过")
            print(f"   - 样本数量: {result_state.get('data_check_total_samples', 'N/A')}")
            print(f"   - 报告文件: {result_state.get('data_check_report_path', 'N/A')}")
        else:
            print("❌ 数据检查失败")
            print(f"   - 错误信息: {result_state.get('data_check_error', 'N/A')}")
            
        return result_state
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
    finally:
        # 清理临时文件
        import shutil
        # shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_generation_node():
    """测试配置生成节点"""
    print("\n" + "="*50)
    print("测试配置生成节点")
    print("="*50)
    
    temp_dir, dataset_path = create_test_environment()
    
    try:
        from loopai.agents.Trainer.nodes import config_generation_node
        
        # 创建测试状态（模拟数据检查已通过）
        state = LoopAIState(
            task_id="test_config_gen",
            train_dataset_path=dataset_path,
            train_task_description="微调一个用于代码生成的模型，专注于算法实现",
            train_model_name="qwen2.5-7b-instruct",
            train_output_dir=os.path.join(temp_dir, "training"),
            output_dir=os.path.join(temp_dir, "output"),
            data_check_passed=True,  # 模拟数据检查通过
            data_check_total_samples=2
        )
        
        print(f"📝 任务描述: {state.get('train_task_description')}")
        print(f"🤖 基础模型: {state.get('train_model_name')}")
        print(f"📊 数据检查状态: {'通过' if state.get('data_check_passed') else '未通过'}")
        
        # 执行配置生成
        result_state = config_generation_node(state)
        
        # 显示结果
        if result_state.get('config_generation_success'):
            print("✅ 配置生成成功")
            print(f"   - 配置文件: {result_state.get('train_config_output_path', 'N/A')}")
            print(f"   - 解释文件: {result_state.get('config_explanation_path', 'N/A')}")
            
            # 尝试读取生成的配置
            config_path = result_state.get('train_config_output_path')
            if config_path and os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_content = f.read()
                    print(f"   - 配置预览: {config_content[:200]}...")
        else:
            print("❌ 配置生成失败")
            print(f"   - 错误信息: {result_state.get('config_generation_error', 'N/A')}")
            
        return result_state
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
    finally:
        # 清理临时文件
        import shutil
        # shutil.rmtree(temp_dir, ignore_errors=True)


def test_training_execution_node():
    """测试训练执行节点"""
    print("\n" + "="*50)
    print("测试训练执行节点")
    print("="*50)
    
    temp_dir, dataset_path = create_test_environment()
    
    try:
        from loopai.agents.Trainer.nodes import training_execution_node
        
        # 创建测试配置文件
        config_path = os.path.join(temp_dir, "test_config.json")
        test_config = {
            "model_name": "qwen2.5-7b-instruct",
            "dataset_path": dataset_path,
            "output_dir": os.path.join(temp_dir, "training"),
            "num_train_epochs": 1,
            "learning_rate": 5e-5,
            "per_device_train_batch_size": 2
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(test_config, f, indent=2, ensure_ascii=False)
        
        # 创建测试状态（模拟前面步骤已完成）
        state = LoopAIState(
            task_id="test_training",
            train_dataset_path=dataset_path,
            train_config_output_path=config_path,
            train_output_dir=os.path.join(temp_dir, "training"),
            train_use_swanlab=False,  # 测试时关闭 SwanLab
            output_dir=os.path.join(temp_dir, "output"),
            data_check_passed=True,
            config_generation_success=True
        )
        
        print(f"⚙️ 配置文件: {config_path}")
        print(f"📂 训练输出目录: {state.get('train_output_dir')}")
        print(f"🔬 SwanLab 监控: {'启用' if state.get('train_use_swanlab') else '禁用'}")
        
        # 执行训练（注意：这里会实际尝试运行训练，但由于缺少真实模型，预期会失败）
        result_state = training_execution_node(state)
        
        # 显示结果
        if result_state.get('training_success'):
            print("✅ 训练执行成功")
            print(f"   - 训练时间: {result_state.get('training_execution_time', 'N/A')} 秒")
            print(f"   - 日志文件: {result_state.get('training_log_path', 'N/A')}")
            print(f"   - SwanLab URL: {result_state.get('swanlab_url', 'N/A')}")
        else:
            print("❌ 训练执行失败 (预期结果，因为没有真实的训练环境)")
            print(f"   - 错误信息: {result_state.get('training_error', 'N/A')}")
            
        return result_state
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    
    finally:
        # 清理临时文件
        import shutil
        # shutil.rmtree(temp_dir, ignore_errors=True)


def test_trainer_agent_complete():
    """测试完整的 TrainerAgent"""
    print("\n" + "="*50)
    print("测试完整的 TrainerAgent")
    print("="*50)
    
    try:
        from loopai.agents.Trainer.trainer_agent import TrainerAgent
        
        # 创建 TrainerAgent 实例
        trainer_agent = TrainerAgent()
        
        print(f"🤖 Agent 名称: {trainer_agent.role_name}")
        print(f"💬 系统提示类型: {trainer_agent.system_prompt_type}")
        print(f"📝 系统提示名称: {trainer_agent.system_prompt_name}")
        
        # 测试输入验证
        test_input = {
            'train_dataset_path': '/path/to/dataset.jsonl',
            'train_task_description': '测试任务描述'
        }
        
        validation_result = trainer_agent.validate_input_state(test_input)
        
        if validation_result['valid']:
            print("✅ 输入验证通过")
            print(f"   - 警告数量: {len(validation_result.get('warnings', []))}")
            for warning in validation_result.get('warnings', []):
                print(f"     ⚠️ {warning}")
        else:
            print("❌ 输入验证失败")
            for error in validation_result.get('errors', []):
                print(f"     ❌ {error}")
        
        print("✅ TrainerAgent 基本功能测试完成")
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """主测试函数"""
    print("开始 TrainerAgent 节点可用性测试")
    print("这个测试将检查三个主要节点的基本功能")
    
    # 依次测试各个节点
    test_results = {}
    
    # 1. 测试数据检查节点
    test_results['data_check'] = test_data_check_node()
    
    # 2. 测试配置生成节点
    test_results['config_generation'] = test_config_generation_node()
    
    # 3. 测试训练执行节点
    test_results['training_execution'] = test_training_execution_node()
    
    # 4. 测试完整的 TrainerAgent
    test_trainer_agent_complete()
    
    # 总结测试结果
    print("\n" + "="*60)
    print("测试结果总结")
    print("="*60)
    
    for test_name, result in test_results.items():
        if result:
            print(f"✅ {test_name}: 节点功能正常")
        else:
            print(f"❌ {test_name}: 节点存在问题")
    
    print("\n📝 注意事项:")
    print("   - 训练执行节点预期会失败，因为需要真实的训练环境")
    print("   - 数据检查和配置生成节点应该能正常工作")
    print("   - 如果看到导入错误，请检查相关工具类是否存在")
    
    print("\n🎯 测试完成！")


if __name__ == "__main__":
    main()
