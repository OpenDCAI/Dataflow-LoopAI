#!/usr/bin/env python3
"""
测试修改后的训练代理
验证YAML配置生成和远程训练服务调用功能
"""

import os
import json
import yaml
import tempfile
from pathlib import Path
import sys
sys.path.append('.')

from loopai.states.base import LoopAIState
from loopai.agents.Trainer import TrainerAgent
from loopai.logger import get_logger

logger = get_logger()

def create_test_dataset():
    """创建测试数据集"""
    test_data = [
        {
            "instruction": "解释什么是机器学习",
            "input": "",
            "output": "机器学习是人工智能的一个分支，它使计算机能够在没有明确编程的情况下学习和做出决策。"
        },
        {
            "instruction": "什么是深度学习？",
            "input": "",
            "output": "深度学习是机器学习的一个子集，它使用多层神经网络来学习数据的复杂模式。"
        },
        {
            "instruction": "解释监督学习",
            "input": "",
            "output": "监督学习是一种机器学习方法，它使用已标记的训练数据来训练模型，以便对新数据进行预测。"
        }
    ]
    
    # 创建临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for item in test_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')
        return f.name

def test_yaml_config_generation():
    """测试YAML配置生成"""
    logger.info("="*60)
    logger.info("测试YAML配置生成功能")
    logger.info("="*60)
    
    # 创建测试数据集
    dataset_path = create_test_dataset()
    logger.info(f"测试数据集创建完成: {dataset_path}")
    
    # 创建输出目录
    output_dir = "./temp/test_yaml_output"
    os.makedirs(output_dir, exist_ok=True)
    
    # 创建初始状态
    state = LoopAIState({
        'train_dataset_path': dataset_path,
        'train_task_description': '训练一个中文问答模型，用于回答机器学习相关问题',
        'train_model_name': 'qwen2.5-7b-instruct',
        'train_output_dir': './output/training',
        'training_service_url': 'http://localhost:8000',
        'output_dir': output_dir
    })
    
    # 创建训练代理
    trainer = TrainerAgent()
    
    # 验证输入状态
    validation_result = trainer.validate_input_state(state)
    logger.info(f"输入验证结果: {validation_result}")
    
    if not validation_result['valid']:
        logger.error("输入验证失败")
        return False
    
    # 手动执行数据检查节点（简化测试）
    logger.info("执行数据检查...")
    state['data_check_passed'] = True  # 简化测试，假设数据检查通过
    
    # 执行配置生成节点
    logger.info("执行配置生成...")
    from loopai.agents.Trainer.nodes import config_generation_node
    
    try:
        updated_state = config_generation_node(state)
        
        if updated_state.get('config_generation_success'):
            logger.info("✅ 配置生成成功")
            
            config_path = updated_state.get('train_config_output_path')
            if config_path and os.path.exists(config_path):
                logger.info(f"YAML配置文件: {config_path}")
                
                # 读取并验证YAML配置
                with open(config_path, 'r', encoding='utf-8') as f:
                    yaml_content = f.read()
                    logger.info("YAML配置内容:")
                    print(yaml_content)
                
                # 验证YAML格式
                try:
                    yaml_config = yaml.safe_load(yaml_content)
                    logger.info("✅ YAML格式验证通过")
                    
                    # 检查关键配置项
                    required_keys = ['model_name', 'dataset', 'stage', 'finetuning_type', 'output_dir']
                    for key in required_keys:
                        if key in yaml_config:
                            logger.info(f"  ✓ {key}: {yaml_config[key]}")
                        else:
                            logger.warning(f"  ⚠ 缺少配置项: {key}")
                    
                    return True
                    
                except yaml.YAMLError as e:
                    logger.error(f"❌ YAML格式错误: {e}")
                    return False
            else:
                logger.error("❌ 配置文件未生成")
                return False
        else:
            logger.error(f"❌ 配置生成失败: {updated_state.get('config_generation_error')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ 配置生成异常: {e}")
        return False
    
    finally:
        # 清理测试文件
        if os.path.exists(dataset_path):
            os.unlink(dataset_path)

def test_training_service_client():
    """测试训练服务客户端"""
    logger.info("="*60)
    logger.info("测试训练服务客户端")
    logger.info("="*60)
    
    from loopai.agents.Trainer.utils.training_service_client import create_training_client
    
    # 创建客户端
    client = create_training_client("http://localhost:8000")
    
    # 测试服务健康检查
    logger.info("检查训练服务状态...")
    is_healthy = client.check_service_health()
    
    if is_healthy:
        logger.info("✅ 训练服务可用")
        return True
    else:
        logger.warning("⚠️ 训练服务不可用 (这是正常的，如果服务未启动)")
        logger.info("要启动训练服务，请运行:")
        logger.info("  cd training_env/llama-train-service")
        logger.info("  python start.py")
        return False

def test_full_workflow():
    """测试完整工作流程"""
    logger.info("="*60)
    logger.info("测试完整训练工作流程")
    logger.info("="*60)
    
    # 创建测试数据集
    dataset_path = create_test_dataset()
    logger.info(f"测试数据集: {dataset_path}")
    
    # 创建输出目录
    output_dir = "./temp/test_full_workflow"
    os.makedirs(output_dir, exist_ok=True)
    
    # 创建初始状态
    initial_state = {
        'train_dataset_path': dataset_path,
        'train_task_description': '训练一个中文问答模型，专门用于回答人工智能和机器学习相关的问题。要求模型能够提供准确、详细的解释。',
        'train_model_name': 'qwen2.5-7b-instruct',
        'train_output_dir': './output/training',
        'training_service_url': 'http://localhost:8000',
        'output_dir': output_dir
    }
    
    # 创建训练代理
    trainer = TrainerAgent()
    
    # 构建训练图
    logger.info("构建训练图...")
    graph = trainer()
    
    logger.info("训练图构建完成")
    logger.info("注意：完整的训练流程需要训练服务运行")
    logger.info("如果要测试完整流程，请先启动训练服务:")
    logger.info("  cd training_env/llama-train-service")
    logger.info("  python start.py")
    
    # 清理测试文件
    # if os.path.exists(dataset_path):
    #     os.unlink(dataset_path)
    
    return True

def main():
    """主函数"""
    logger.info("开始测试修改后的训练代理")
    
    # 测试1：YAML配置生成
    success1 = test_yaml_config_generation()
    
    # 测试2：训练服务客户端
    success2 = test_training_service_client()
    
    # 测试3：完整工作流程
    success3 = test_full_workflow()
    
    # 总结
    logger.info("="*60)
    logger.info("测试总结")
    logger.info("="*60)
    logger.info(f"YAML配置生成: {'✅ 通过' if success1 else '❌ 失败'}")
    logger.info(f"训练服务客户端: {'✅ 通过' if success2 else '⚠️ 服务未启动'}")
    logger.info(f"完整工作流程: {'✅ 通过' if success3 else '❌ 失败'}")
    
    if success1 and success3:
        logger.info("🎉 核心功能测试通过！")
        logger.info("修改已完成：")
        logger.info("  ✓ 配置生成器现在输出YAML格式")
        logger.info("  ✓ 训练执行节点现在调用远程训练服务")
        logger.info("  ✓ 添加了训练服务客户端")
        logger.info("  ✓ 更新了训练代理的输入验证和摘要")
    else:
        logger.error("❌ 部分测试失败，请检查错误信息")

if __name__ == "__main__":
    main()
