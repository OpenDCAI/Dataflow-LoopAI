"""
Trainer Agent
负责执行模型训练任务的智能代理

该 Agent 包含三个主要节点：
1. 数据检查节点 - 验证数据集格式是否符合 LlamaFactory 要求
2. 配置生成节点 - 根据任务描述生成合理的YAML训练配置
3. 训练执行节点 - 调用远程训练服务执行训练任务
"""

import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from .nodes import data_check_node, config_generation_node, training_execution_node

from loopai.logger import get_logger

logger = get_logger()


class TrainerAgent(BaseAgent):
    """
    Trainer Agent - 模型训练智能代理
    
    功能特性：
    - 自动验证数据集格式
    - 智能生成YAML训练配置
    - 调用远程训练服务执行训练
    - 支持 LoRA 微调
    - 提供详细的训练报告和日志
    """
    
    @property
    def role_name(self) -> str:
        """角色名称"""
        return "Trainer"

    @property
    def system_prompt_type(self) -> str:
        """系统提示类型"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """系统提示名称"""
        return "default_prompt"
    
    @staticmethod
    def data_check_node_wrapper(state: LoopAIState) -> LoopAIState:
        """数据检查节点包装器"""
        logger.info("执行数据检查节点")
        return data_check_node(state)
    
    @staticmethod
    def config_generation_node_wrapper(state: LoopAIState) -> LoopAIState:
        """配置生成节点包装器"""
        logger.info("执行配置生成节点")
        return config_generation_node(state)
    
    @staticmethod
    def training_execution_node_wrapper(state: LoopAIState) -> LoopAIState:
        """训练执行节点包装器"""
        logger.info("执行训练节点")
        return training_execution_node(state)
    
    @staticmethod
    def should_continue_after_data_check(state: LoopAIState) -> str:
        """数据检查后的条件判断"""
        print("before check:\n")
        print(state)
        if state.get('data_check_passed', False):
            logger.info("数据检查通过，继续配置生成")
            return "config_generation"
        else:
            logger.error("数据检查未通过，训练流程终止")
            return "end"
    
    @staticmethod 
    def should_continue_after_config_generation(state: LoopAIState) -> str:
        """配置生成后的条件判断"""
        if state.get('config_generation_success', False):
            logger.info("配置生成成功，继续训练执行")
            return "training_execution"
        else:
            logger.error("配置生成失败，训练流程终止")
            return "end"
    
    def init_graph(self, **kwargs):
        """
        初始化训练图（状态机）
        
        图结构：
        数据检查 -> 配置生成 -> 训练执行
               ↓           ↓
              结束        结束
        """
        builder = StateGraph(LoopAIState)
        
        # 添加节点
        builder.add_node("data_check", self.data_check_node_wrapper)
        builder.add_node("config_generation", self.config_generation_node_wrapper) 
        builder.add_node("training_execution", self.training_execution_node_wrapper)
        builder.add_node("end", lambda state: state)
        
        # 设置边
        builder.add_conditional_edges(
            "data_check",
            self.should_continue_after_data_check,
            {
                "config_generation": "config_generation",
                "end": "end"
            }
        )
        
        builder.add_conditional_edges(
            "config_generation", 
            self.should_continue_after_config_generation,
            {
                "training_execution": "training_execution",
                "end": "end"
            }
        )
        
        builder.add_edge("training_execution", "end")
        
        # 设置入口点和结束点
        builder.set_entry_point("data_check")
        builder.set_finish_point("end")
        
        # 编译图
        self.graph = builder.compile(
            checkpointer=self.checkpointer, 
            store=self.store, 
            **kwargs
        )
        
        logger.info("TrainerAgent 图初始化完成")
    
    def __call__(self, **kwargs):
        """
        构建并返回图实例

        Args:
            kwargs: 传递给 init_graph 的关键字参数
        
        Returns:
            编译后的图实例
        """
        self.init_graph(**kwargs)
        return self.graph
    
    def validate_input_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证输入状态
        
        Args:
            state: 输入状态字典
            
        Returns:
            验证结果字典
        """
        
        result = {
            "valid": True,
            "errors": [],
            "warnings": []
        }
        
        # 检查必需的字段
        required_fields = [
            'train_dataset_path',
            'train_task_description'
        ]
        
        for field in required_fields:
            if not state.get(field):
                result["errors"].append(f"缺少必需的字段: {field}")
                result["valid"] = False
        
        # 检查可选字段并设置默认值
        defaults = {
            'train_model_name': 'qwen2.5-7b-instruct',
            'train_output_dir': './output/training',
            'train_use_swanlab': True,
            'train_swanlab_project': 'llamafactory_training',
            'training_service_url': 'http://localhost:8000',
            'output_dir': './output/trainer'
        }
        
        for field, default_value in defaults.items():
            if field not in state:
                state[field] = default_value
                result["warnings"].append(f"字段 {field} 使用默认值: {default_value}")
        
        return result
    
    def get_training_summary(self, state: LoopAIState) -> Dict[str, Any]:
        """
        获取训练摘要信息
        
        Args:
            state: 状态对象
            
        Returns:
            训练摘要字典
        """
        
        summary = {
            "agent_name": self.role_name,
            "execution_time": None,
            "stages": {
                "data_check": {
                    "passed": state.get('data_check_passed', False),
                    "report_path": state.get('data_check_report_path'),
                    "error": state.get('data_check_error')
                },
                "config_generation": {
                    "success": state.get('config_generation_success', False),
                    "config_path": state.get('train_config_output_path'),
                    "explanation_path": state.get('config_explanation_path'),
                    "error": state.get('config_generation_error')
                },
                "training_execution": {
                    "success": state.get('training_success', False),
                    "training_time": state.get('training_execution_time'),
                    "task_id": state.get('training_task_id'),
                    "final_status": state.get('training_final_status'),
                    "log_path": state.get('training_log_path'),
                    "report_path": state.get('training_report_path'),
                    "error": state.get('training_error')
                }
            },
            "final_status": "success" if state.get('training_success', False) else "failed",
            "output_files": []
        }
        
        # 收集输出文件
        file_paths = [
            state.get('data_check_report_path'),
            state.get('train_config_output_path'),
            state.get('config_explanation_path'),
            state.get('training_log_path'),
            state.get('training_report_path')
        ]
        
        summary["output_files"] = [path for path in file_paths if path]
        
        return summary
