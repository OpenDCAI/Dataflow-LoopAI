"""
TrainerAgent 测试文件
测试数据检查、配置生成和训练执行三个节点的可用性
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loopai.states.base import LoopAIState
from loopai.agents.Trainer.trainer_agent import TrainerAgent
from loopai.agents.Trainer.nodes import data_check_node, config_generation_node, training_execution_node


class TestTrainerAgentNodes(unittest.TestCase):
    """TrainerAgent 节点测试类"""
    
    def setUp(self):
        """测试前置设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_dataset_path = os.path.join(self.temp_dir, "test_dataset.jsonl")
        self.test_config_path = os.path.join(self.temp_dir, "test_config.json")
        self.output_dir = os.path.join(self.temp_dir, "output")
        
        # 创建测试数据集文件
        self._create_test_dataset()
        
        # 创建基础状态
        self.base_state = LoopAIState(
            task_id="test_task_001",
            train_dataset_path=self.test_dataset_path,
            train_task_description="测试任务：微调一个用于代码生成的模型",
            train_model_name="qwen2.5-7b-instruct",
            train_output_dir=os.path.join(self.temp_dir, "training"),
            output_dir=self.output_dir,
            train_use_swanlab=False,  # 测试时关闭 SwanLab
            train_swanlab_project="test_project"
        )
    
    def tearDown(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def _create_test_dataset(self):
        """创建测试数据集"""
        test_data = [
            {
                "instruction": "编写一个计算阶乘的函数",
                "input": "",
                "output": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n-1)"
            },
            {
                "instruction": "编写一个排序算法",
                "input": "",
                "output": "def bubble_sort(arr):\n    n = len(arr)\n    for i in range(n):\n        for j in range(0, n-i-1):\n            if arr[j] > arr[j+1]:\n                arr[j], arr[j+1] = arr[j+1], arr[j]\n    return arr"
            },
            {
                "instruction": "编写一个二分查找函数",
                "input": "",
                "output": "def binary_search(arr, x):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == x:\n            return mid\n        elif arr[mid] < x:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1"
            }
        ]
        
        with open(self.test_dataset_path, 'w', encoding='utf-8') as f:
            for item in test_data:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
    
    def test_data_check_node_success(self):
        """测试数据检查节点 - 成功场景"""
        print("\n=== 测试数据检查节点 - 成功场景 ===")
        
        with patch('loopai.agents.Trainer.utils.data_checker.check_data_format') as mock_check, \
             patch('loopai.agents.Trainer.utils.data_checker.generate_format_report') as mock_report:
            
            # Mock 返回值
            mock_check.return_value = {
                'valid': True,
                'total_samples': 3,
                'format_errors': [],
                'warnings': []
            }
            mock_report.return_value = "数据检查通过，共3个样本"
            
            # 执行测试
            result_state = data_check_node(self.base_state)
            
            # 验证结果
            self.assertTrue(result_state.get('data_check_passed'))
            self.assertIsNotNone(result_state.get('data_check_report_path'))
            self.assertEqual(result_state.get('data_check_total_samples'), 3)
            
            print(f"✅ 数据检查通过")
            print(f"   - 样本数量: {result_state.get('data_check_total_samples')}")
            print(f"   - 报告路径: {result_state.get('data_check_report_path')}")
    
    def test_data_check_node_failure(self):
        """测试数据检查节点 - 失败场景"""
        print("\n=== 测试数据检查节点 - 失败场景 ===")
        
        with patch('loopai.agents.Trainer.utils.data_checker.check_data_format') as mock_check, \
             patch('loopai.agents.Trainer.utils.data_checker.generate_format_report') as mock_report:
            
            # Mock 返回值 - 数据格式错误
            mock_check.return_value = {
                'valid': False,
                'total_samples': 2,
                'format_errors': ['缺少必需字段: instruction', '输出格式不正确'],
                'warnings': ['建议增加更多样本']
            }
            mock_report.return_value = "数据检查失败，存在格式错误"
            
            # 执行测试
            result_state = data_check_node(self.base_state)
            
            # 验证结果
            self.assertFalse(result_state.get('data_check_passed'))
            self.assertIsNotNone(result_state.get('data_check_error'))
            self.assertEqual(len(result_state.get('data_check_format_errors', [])), 2)
            
            print(f"❌ 数据检查失败")
            print(f"   - 错误数量: {len(result_state.get('data_check_format_errors', []))}")
            print(f"   - 错误信息: {result_state.get('data_check_error')}")
    
    def test_data_check_node_missing_path(self):
        """测试数据检查节点 - 缺少数据集路径"""
        print("\n=== 测试数据检查节点 - 缺少数据集路径 ===")
        
        # 创建缺少数据集路径的状态
        invalid_state = LoopAIState(
            task_id="test_invalid",
            output_dir=self.output_dir
        )
        
        # 执行测试
        result_state = data_check_node(invalid_state)
        
        # 验证结果
        self.assertIsNotNone(result_state.get('data_check_error'))
        self.assertFalse(result_state.get('data_check_passed', False))
        
        print(f"❌ 预期错误: {result_state.get('data_check_error')}")
    
    def test_config_generation_node_success(self):
        """测试配置生成节点 - 成功场景"""
        print("\n=== 测试配置生成节点 - 成功场景 ===")
        
        # 准备前置状态（数据检查通过）
        state_with_data_check = self.base_state.copy()
        state_with_data_check.update({
            'data_check_passed': True,
            'data_check_total_samples': 3
        })
        
        with patch('loopai.agents.Trainer.utils.config_generator.ConfigGenerator') as MockConfigGenerator, \
             patch('loopai.agents.Trainer.utils.config_generator.generate_config_explanation') as mock_explanation:
            
            # Mock ConfigGenerator
            mock_generator = MockConfigGenerator.return_value
            mock_generator.generate_config.return_value = {
                'model_name': 'qwen2.5-7b-instruct',
                'dataset_path': self.test_dataset_path,
                'output_dir': './output/training',
                'num_train_epochs': 3,
                'learning_rate': 5e-5,
                'per_device_train_batch_size': 4
            }
            mock_explanation.return_value = "配置解释：使用 LoRA 微调方法，学习率 5e-5"
            
            # 执行测试
            result_state = config_generation_node(state_with_data_check)
            
            # 验证结果
            self.assertTrue(result_state.get('config_generation_success'))
            self.assertIsNotNone(result_state.get('train_config_output_path'))
            self.assertIsNotNone(result_state.get('config_explanation_path'))
            
            print(f"✅ 配置生成成功")
            print(f"   - 配置文件: {result_state.get('train_config_output_path')}")
            print(f"   - 解释文件: {result_state.get('config_explanation_path')}")
    
    def test_config_generation_node_no_data_check(self):
        """测试配置生成节点 - 数据检查未通过"""
        print("\n=== 测试配置生成节点 - 数据检查未通过 ===")
        
        # 使用未通过数据检查的状态
        result_state = config_generation_node(self.base_state)
        
        # 验证结果
        self.assertFalse(result_state.get('config_generation_success', False))
        self.assertIsNotNone(result_state.get('config_generation_error'))
        
        print(f"❌ 预期错误: {result_state.get('config_generation_error')}")
    
    def test_training_execution_node_success(self):
        """测试训练执行节点 - 成功场景"""
        print("\n=== 测试训练执行节点 - 成功场景 ===")
        
        # 准备前置状态（配置生成成功）
        state_with_config = self.base_state.copy()
        state_with_config.update({
            'data_check_passed': True,
            'config_generation_success': True,
            'train_config_output_path': self.test_config_path
        })
        
        # 创建测试配置文件
        test_config = {
            'model_name': 'qwen2.5-7b-instruct',
            'dataset_path': self.test_dataset_path,
            'output_dir': './output/training'
        }
        with open(self.test_config_path, 'w', encoding='utf-8') as f:
            json.dump(test_config, f, indent=2)
        
        with patch('loopai.agents.Trainer.utils.training_executor.TrainingExecutor') as MockExecutor, \
             patch('loopai.agents.Trainer.utils.training_executor.validate_training_environment') as mock_validate, \
             patch('loopai.agents.Trainer.utils.training_executor.generate_training_report') as mock_report:
            
            # Mock 训练执行器
            mock_executor = MockExecutor.return_value
            mock_executor.execute_training.return_value = {
                'success': True,
                'training_started': True,
                'training_time': 120.5,
                'log_file': os.path.join(self.temp_dir, 'training.log'),
                'swanlab_url': None
            }
            
            mock_validate.return_value = {
                'valid': True,
                'errors': [],
                'warnings': []
            }
            
            mock_report.return_value = "训练成功完成"
            
            # 执行测试
            result_state = training_execution_node(state_with_config)
            
            # 验证结果
            self.assertTrue(result_state.get('training_success'))
            self.assertIsNotNone(result_state.get('training_log_path'))
            self.assertIsNotNone(result_state.get('training_execution_time'))
            
            print(f"✅ 训练执行成功")
            print(f"   - 训练时间: {result_state.get('training_execution_time')} 秒")
            print(f"   - 日志文件: {result_state.get('training_log_path')}")
    
    def test_training_execution_node_no_config(self):
        """测试训练执行节点 - 配置生成未成功"""
        print("\n=== 测试训练执行节点 - 配置生成未成功 ===")
        
        # 使用未通过配置生成的状态
        result_state = training_execution_node(self.base_state)
        
        # 验证结果
        self.assertFalse(result_state.get('training_success', False))
        self.assertIsNotNone(result_state.get('training_error'))
        
        print(f"❌ 预期错误: {result_state.get('training_error')}")
    
    def test_trainer_agent_initialization(self):
        """测试 TrainerAgent 初始化"""
        print("\n=== 测试 TrainerAgent 初始化 ===")
        
        # 创建 TrainerAgent 实例
        trainer_agent = TrainerAgent()
        
        # 验证基本属性
        self.assertEqual(trainer_agent.role_name, "Trainer")
        self.assertEqual(trainer_agent.system_prompt_type, "system")
        self.assertEqual(trainer_agent.system_prompt_name, "default_prompt")
        
        print(f"✅ TrainerAgent 初始化成功")
        print(f"   - 角色名称: {trainer_agent.role_name}")
        print(f"   - 系统提示类型: {trainer_agent.system_prompt_type}")
    
    def test_trainer_agent_input_validation(self):
        """测试 TrainerAgent 输入验证"""
        print("\n=== 测试 TrainerAgent 输入验证 ===")
        
        trainer_agent = TrainerAgent()
        
        # 测试有效输入
        valid_state = {
            'train_dataset_path': self.test_dataset_path,
            'train_task_description': '测试任务描述'
        }
        
        validation_result = trainer_agent.validate_input_state(valid_state)
        self.assertTrue(validation_result['valid'])
        
        print(f"✅ 有效输入验证通过")
        print(f"   - 默认值数量: {len(validation_result.get('warnings', []))}")
        
        # 测试无效输入
        invalid_state = {}
        validation_result = trainer_agent.validate_input_state(invalid_state)
        self.assertFalse(validation_result['valid'])
        self.assertGreater(len(validation_result['errors']), 0)
        
        print(f"❌ 无效输入验证失败 (预期)")
        print(f"   - 错误数量: {len(validation_result['errors'])}")
    
    def test_trainer_agent_training_summary(self):
        """测试 TrainerAgent 训练摘要"""
        print("\n=== 测试 TrainerAgent 训练摘要 ===")
        
        trainer_agent = TrainerAgent()
        
        # 创建模拟的完整状态
        complete_state = LoopAIState(
            task_id="test_complete",
            data_check_passed=True,
            data_check_report_path=os.path.join(self.temp_dir, "report.txt"),
            config_generation_success=True,
            train_config_output_path=os.path.join(self.temp_dir, "config.json"),
            training_success=True,
            training_execution_time=150.0,
            training_log_path=os.path.join(self.temp_dir, "training.log")
        )
        
        # 获取摘要
        summary = trainer_agent.get_training_summary(complete_state)
        
        # 验证摘要
        self.assertEqual(summary['agent_name'], 'Trainer')
        self.assertEqual(summary['final_status'], 'success')
        self.assertTrue(summary['stages']['data_check']['passed'])
        self.assertTrue(summary['stages']['config_generation']['success'])
        self.assertTrue(summary['stages']['training_execution']['success'])
        
        print(f"✅ 训练摘要生成成功")
        print(f"   - 最终状态: {summary['final_status']}")
        print(f"   - 输出文件数量: {len(summary['output_files'])}")
        
        # 打印详细摘要
        print("\n--- 详细摘要 ---")
        for stage_name, stage_info in summary['stages'].items():
            status = "✅" if stage_info.get('passed') or stage_info.get('success') else "❌"
            print(f"   {status} {stage_name}: {stage_info}")


def run_integration_test():
    """运行集成测试"""
    print("\n" + "="*60)
    print("运行 TrainerAgent 集成测试")
    print("="*60)
    
    # 创建测试实例
    test_instance = TestTrainerAgentNodes()
    test_instance.setUp()
    
    try:
        # 运行所有测试
        test_methods = [
            'test_trainer_agent_initialization',
            'test_trainer_agent_input_validation',
            'test_data_check_node_success',
            'test_data_check_node_failure',
            'test_data_check_node_missing_path',
            'test_config_generation_node_success',
            'test_config_generation_node_no_data_check',
            'test_training_execution_node_success', 
            'test_training_execution_node_no_config',
            'test_trainer_agent_training_summary'
        ]
        
        passed = 0
        failed = 0
        
        for method_name in test_methods:
            try:
                method = getattr(test_instance, method_name)
                method()
                passed += 1
            except Exception as e:
                print(f"❌ {method_name} 失败: {str(e)}")
                failed += 1
        
        print(f"\n" + "="*60)
        print(f"测试完成")
        print(f"通过: {passed}, 失败: {failed}")
        print("="*60)
        
    finally:
        test_instance.tearDown()


if __name__ == "__main__":
    # 运行单元测试
    if len(sys.argv) > 1 and sys.argv[1] == "--unittest":
        unittest.main(argv=[sys.argv[0]])
    else:
        # 运行集成测试
        run_integration_test()
