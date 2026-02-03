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
from langgraph.runtime import Runtime
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.schema.states import RuntimeContext, get_missing_fields
from loopai.schema.events import StreamEvent
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
    @BaseAgent.set_current
    def data_check_node_wrapper(state: LoopAIState) -> LoopAIState:
        """数据检查节点包装器"""
        writer = get_stream_writer()
        
        # 开始数据检查
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="开始数据格式检查",
                data={"dataset_path": state.get('trainer', {}).get('train_input_dataset_path')}
            ).json())
        
        logger.info("执行数据检查节点")
        result_state = data_check_node(state)
        
        # 完成数据检查
        if writer:
            check_passed = result_state.get('trainer', {}).get('trainer_data_check_passed', False)
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"数据检查{'通过' if check_passed else '失败'}",
                data={
                    "passed": check_passed,
                    "total_samples": result_state.get('trainer', {}).get('trainer_data_check_result', {}).get('total_samples'),
                    "errors_count": len(result_state.get('trainer', {}).get('trainer_data_check_result', {}).get('errors', [])),
                    "warnings_count": len(result_state.get('trainer', {}).get('trainer_data_check_result', {}).get('warnings', []))
                }
            ).json())
        
        return result_state
    
    @staticmethod
    @BaseAgent.set_current
    def config_generation_node_wrapper(state: LoopAIState) -> LoopAIState:
        """配置生成节点包装器"""
        writer = get_stream_writer()
        
        # 开始配置生成
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="开始生成训练配置",
                data={
                    "model_name": state.get('trainer', {}).get('train_input_model_name'),
                    "task_description": state.get('trainer', {}).get('train_input_task_description')
                }
            ).json())
        
        logger.info("执行配置生成节点")
        result_state = config_generation_node(state)
        
        # 完成配置生成
        if writer:
            success = result_state.get('trainer', {}).get('trainer_config_generation_success', False)
            config = result_state.get('trainer', {}).get('train_config', {})
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"配置生成{'成功' if success else '失败'}",
                data={
                    "success": success,
                    "model_name": config.get('model_name'),
                    "finetuning_type": config.get('finetuning_type'),
                    "learning_rate": config.get('learning_rate'),
                    "num_train_epochs": config.get('num_train_epochs'),
                    "config_path": result_state.get('trainer', {}).get('train_output_config_path')
                }
            ).json())
        
        return result_state
    
    @staticmethod
    @BaseAgent.set_current
    def training_execution_node_wrapper(state: LoopAIState) -> LoopAIState:
        """训练执行节点包装器"""
        writer = get_stream_writer()
        
        # 开始训练执行
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message="开始提交训练任务",
                data={
                    "config_path": state.get('trainer', {}).get('train_output_config_path'),
                    "service_url": state.get('trainer', {}).get('training_service_url')
                }
            ).json())
        
        logger.info("执行训练节点")
        result_state = training_execution_node(state, writer)
        
        # 完成训练执行
        if writer:
            success = result_state.get('trainer', {}).get('trainer_training_success', False)
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"训练任务{'成功完成' if success else '执行失败'}",
                data={
                    "success": success,
                    "task_id": result_state.get('trainer', {}).get('trainer_training_task_id'),
                    "execution_time": result_state.get('trainer', {}).get('trainer_training_execution_time'),
                    "final_status": result_state.get('trainer', {}).get('trainer_training_final_status'),
                    "report_path": result_state.get('trainer', {}).get('train_output_training_report_path')
                }
            ).json())
        
        return result_state
    
    @staticmethod
    def should_continue_after_data_check(state: LoopAIState) -> str:
        """数据检查后的条件判断"""
        if state.get('trainer', {}).get('trainer_data_check_passed', False):
            logger.info("数据检查通过，继续配置生成")
            return "config_generation"
        else:
            logger.error("数据检查未通过，训练流程终止")
            return "end"
    
    @staticmethod 
    def should_continue_after_config_generation(state: LoopAIState) -> str:
        """配置生成后的条件判断"""
        if state.get('trainer', {}).get('trainer_config_generation_success', False):
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
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
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
        
        # 从前置检查进入数据检查
        builder.add_edge("check_required_fields", "data_check")

        builder.add_conditional_edges(
            "config_generation", 
            self.should_continue_after_config_generation,
            {
                "training_execution": "training_execution",
                "end": "end"
            }
        )
        
        builder.add_edge("training_execution", "end")
        
        # 将前置检查设为入口点，这样可以在缺少配置时触发 Configer 子图
        builder.set_entry_point("check_required_fields")
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
                    "passed": state.get('trainer', {}).get('trainer_data_check_passed', False),
                    "report_path": state.get('trainer', {}).get('train_output_data_check_report_path'),
                    "error": state.get('trainer', {}).get('trainer_data_check_error')
                },
                "config_generation": {
                    "success": state.get('trainer', {}).get('trainer_config_generation_success', False),
                    "config_path": state.get('trainer', {}).get('train_output_config_path'),
                    "explanation_path": state.get('trainer', {}).get('trainer_config_explanation_path'),
                    "error": state.get('trainer', {}).get('trainer_config_generation_error')
                },
                "training_execution": {
                    "success": state.get('trainer', {}).get('trainer_training_success', False),
                    "training_time": state.get('trainer', {}).get('trainer_training_execution_time'),
                    "task_id": state.get('trainer', {}).get('trainer_training_task_id'),
                    "final_status": state.get('trainer', {}).get('trainer_training_final_status'),
                    "log_path": state.get('trainer', {}).get('train_output_training_log_path'),
                    "report_path": state.get('trainer', {}).get('train_output_training_report_path'),
                    "error": state.get('trainer', {}).get('train_output_training_error'),
                    "train_output_swanlab_log_path": state.get('trainer', {}).get('train_output_swanlab_log_path')
                }
            },
            "final_status": "success" if state.get('trainer', {}).get('trainer_training_success', False) else "failed",
            "output_files": []
        }
        
        # 收集输出文件
        file_paths = [
            state.get('trainer', {}).get('train_output_data_check_report_path'),
            state.get('trainer', {}).get('train_output_config_path'),
            state.get('trainer', {}).get('trainer_config_explanation_path'),
            state.get('trainer', {}).get('train_output_training_log_path'),
            state.get('trainer', {}).get('train_output_training_report_path')
        ]
        summary["output_files"] = [path for path in file_paths if path]
        
        return summary

    def get_check_required_fields_node(self):
        @BaseAgent.set_current
        def check_required_fields(state: LoopAIState, runtime: Runtime[RuntimeContext]):
            writer = get_stream_writer()
            
            # 进度：开始检查必需字段
            if writer:
                writer(StreamEvent(
                    current="Trainer.check_required_fields",
                    progress=0.0,
                    message="正在检查训练所需的配置字段...",
                    data={"stage": "field_validation"}
                ).json())
            # Trainer 运行前需要的字段，如果缺失则触发 Configer 子图来补全配置
            required_fields = {
                "trainer": [
                    'train_framework',
                    'train_input_dataset_path',
                    'train_input_task_description',
                    'train_input_config_template_path',
                    'train_output_dir',
                    'train_input_model_name'
                ]
            }
            
            # 如果使用 LlamaFactory 框架，则需要额外的字段
            framework = state.get('trainer', {}).get('train_framework')
            if framework == 'llamafactory':
                required_fields["trainer"].append('llamafactory_dir')
            
            missing_fields = get_missing_fields(required_fields, state)
                    
            if missing_fields:
                # 进度：发现缺失字段
                if writer:
                    writer(StreamEvent(
                        current="Trainer.check_required_fields",
                        progress=0.5,
                        message=f"发现缺失字段，将转至配置补全: {', '.join(missing_fields)}",
                        data={
                            "missing_fields": missing_fields,
                            "total_required": len(required_fields),
                            "missing_count": len(missing_fields)
                        }
                    ).json())
                
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                # 使用 PromptLoader 生成引导自动化填充的 query
                state['automated_query'] = self.prompt_loader(
                    "automated_query", "trainer_missing_fields_prompt")
                state.setdefault('configer', {})['configer_error'] = missing_fields
                goto_node = runtime.context['exception_navigate']
                logger.info(f'found missing fields, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )
            else:
                # 进度：所有字段检查通过
                if writer:
                    writer(StreamEvent(
                        current="Trainer.check_required_fields",
                        progress=1.0,
                        message="所有必需字段检查通过，开始训练流程",
                        data={
                            "all_fields_present": True,
                            "total_required": len(required_fields)
                        }
                    ).json())
        return check_required_fields
