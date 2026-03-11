import json
import os
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph, END
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from pathlib import Path

from loopai.schema.states import LoopAIState, RuntimeContext, get_missing_fields
from loopai.agents import BaseAgent
from .utils.oj.generate import generate_sample_code, generate_sample_text2sql
from .utils.oj.evaluate import evaluate_sample, evaluate_sample_sql
from .utils.oj.format import data_format
from .utils.oj.data import check_file
from .utils.oj.vllm_starter import start_vllm_openai_api_server
from .utils.oj.vllm_killer import kill_vllm_openai_api_server
from .utils.oj.vllm_check import check_vllm_running

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger

logger = get_logger()

def _isNotNone(value):
    return value != "" and value is not None

class JudgerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Judger"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"

    def get_check_required_fields_node(self):
        @BaseAgent.set_current
        def check_required_fields(state: LoopAIState, runtime: Runtime[RuntimeContext]):
            writer = get_stream_writer()
            required_fields = {
                'judger':["eval_model_path", "eval_api_key", "eval_temperature",
                        "eval_top_p", "eval_problem_path", "eval_batch_size", 
                        "eval_case_num", "eval_task_type", "output_dir"
                ]
            }
            missing_fields = get_missing_fields(required_fields, state)

            """vllm启动检查"""
            base_url = state.get("judger", {}).get("eval_base_url", None)
            if(_isNotNone(base_url)):
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=0.0,
                        message="已设置[eval_base_url]，正在进行vllm启动检查",
                        data={"msg": f""}
                    ).json())

                res = check_vllm_running(base_url)

                if(res is True):
                    logger.info("已启动vllm")
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm已启动",
                            data={"msg": f""}
                        ).json())
                else:
                    logger.info("未启动vllm")
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm未启动",
                            data={"msg": f""}
                        ).json())
                    missing_fields.setdefault("judger", []).append("eval_base_url")

            """数据有效检查"""
            check_result = check_file(state)
            for key, value in check_result.items():
                is_true = bool(value)
                if not is_true:
                    missing_fields.setdefault("judger", []).append(key)
                    
            """检查text2sql必要字段"""
            if not missing_fields:
                if state.get("judger", {}).get("eval_text2sql_dir", "") == "text2sql":
                    missing_fields = get_missing_fields({'judger':["eval_text2sql_dir"]}, state)
            
            if missing_fields:
                logger.info("$"*50)
                logger.info(missing_fields)
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = self.prompt_loader(
                    "automated_query", "judger_missing_fields_prompt")
                state.setdefault('configer',{})['configer_error'] = f'Missing required fields: {json.dumps({"missing_fields": missing_fields}, ensure_ascii=False)}'
                goto_node = runtime.context['exception_navigate']
                logger.info(f'found missing fields, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )
            state["judger"]["output_result_path"] = ""
            state["judger"]["output_case_path"] = ""
            state["judger"]["output_problem_path"] = ""
        return check_required_fields

    @staticmethod
    @BaseAgent.set_current
    def check_param_type_node(state: LoopAIState) -> LoopAIState:
        eval_temperature = state.get("judger", {}).get("eval_temperature", None)
        if _isNotNone(eval_temperature):
            state["judger"]["eval_temperature"] = float(eval_temperature)

        eval_top_p = state.get("judger", {}).get("eval_top_p", None)
        if _isNotNone(eval_top_p):
            state["judger"]["eval_top_p"] = float(eval_top_p)

        eval_batch_size = state.get("judger", {}).get("eval_batch_size", None)
        if _isNotNone(eval_batch_size):
            state["judger"]["eval_batch_size"] = int(eval_batch_size)
        
        eval_case_num = state.get("judger", {}).get("eval_case_num", None)
        if _isNotNone(eval_case_num):
            state["judger"]["eval_case_num"] = int(eval_case_num)
        
        eval_vllm_port = state.get("judger", {}).get("eval_vllm_port", None)
        if _isNotNone(eval_vllm_port):
            state["judger"]["eval_vllm_port"] = int(eval_vllm_port)

        eval_vllm_tensor_parallel_size = state.get("judger", {}).get("eval_vllm_tensor_parallel_size", None)
        if _isNotNone(eval_vllm_tensor_parallel_size):
            state["judger"]["eval_vllm_tensor_parallel_size"] = int(eval_vllm_tensor_parallel_size)
        
        eval_vllm_gpu_memory_utilization = state.get("judger", {}).get("eval_vllm_gpu_memory_utilization", None)
        if _isNotNone(eval_vllm_gpu_memory_utilization):
            state["judger"]["eval_vllm_gpu_memory_utilization"] = float(eval_vllm_gpu_memory_utilization)

        return state

    @staticmethod
    @BaseAgent.set_current
    def vllm_kill_node(state: LoopAIState) -> LoopAIState:
        env_configs = state.get("judger", {}).get("eval_env_configs", None)
        vllm_port = state.get("judger", {}).get("eval_vllm_port", None)
        base_url = state.get("judger", {}).get("eval_base_url", None)
        writer = get_stream_writer()
        # 未设置base_url才会进入本地开启程序才会先关闭本地的vllm服务
        if  (not _isNotNone(base_url)) or (base_url == f"http://localhost:{vllm_port}/v1"):
            if(_isNotNone(env_configs) and _isNotNone(vllm_port)):
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=0.0,
                        message="vllm关闭",
                        data={"msg": f""}
                    ).json())

                res = kill_vllm_openai_api_server(vllm_port)

                if res is True:
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm已关闭",
                            data={"msg": f""}
                        ).json())
                else:
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm关闭失败",
                            data={"msg": f""}
                        ).json())
            else:
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=1.0,
                        message="vllm开启结束",
                        data={"msg": f"因已开启自定义vllm服务而跳过该过程"}
                    ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def vllm_start_node(state: LoopAIState) -> LoopAIState:
        env_configs = state.get("judger", {}).get("eval_env_configs", None)
        vllm_port = state.get("judger", {}).get("eval_vllm_port", 8911)
        vllm_tensor_parallel_size = state.get("judger", {}).get("eval_vllm_tensor_parallel_size", 1)
        vllm_gpu_memory_utilization = state.get("judger", {}).get("eval_vllm_gpu_memory_utilization", 0.9)
        vllm_model = state.get("judger", {}).get("eval_model_path", None)
        vllm_env_path = state.get("judger", {}).get("eval_vllm_env_path", None)

        writer = get_stream_writer()
        if(_isNotNone(env_configs) and _isNotNone(vllm_port) and _isNotNone(vllm_tensor_parallel_size) and _isNotNone(vllm_gpu_memory_utilization)):
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=0.0,
                    message="vllm准备启动",
                    data={"msg": f""}
                ).json())
            
            if not _isNotNone(vllm_env_path):
                vllm_env_path = "python"

            start_vllm_openai_api_server(env_configs, vllm_env_path, vllm_port, vllm_tensor_parallel_size, vllm_gpu_memory_utilization, vllm_model)
            state["judger"]["eval_base_url"] = f"http://localhost:{vllm_port}/v1"
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="vllm已启动",
                    data={"msg": f""}
                ).json())
        else:
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="vllm开启结束",
                    data={"msg": f"因参数[env_configs]或[vllm_port]或[vllm_tensor_parallel_size]或[vllm_gpu_memory_utilization]未设置而跳过该过程"}
                ).json())
        return state
    
    @staticmethod
    @BaseAgent.set_current
    def data_format_node(state: LoopAIState) -> LoopAIState:
        problem_path = state.get("judger", {}).get("eval_problem_path", "")
        format_type = state.get("judger", {}).get("eval_format_type", None)
        state_task_id = state.get("task_id")
        output_dir = Path(state.get("judger", {}).get("output_dir", "/"))
        problem_file_name = str(Path(problem_path).stem)

        target_format_path = output_dir / str(state_task_id) / "judger" / (problem_file_name + "_format.jsonl")
        writer = get_stream_writer()
        if _isNotNone(format_type):
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=0.0,
                    message="任务数据格式化开始",
                    data={"msg": f"由[{problem_path}]以[{format_type}]格式转存为[{str(target_format_path)}]"}
                ).json())
            data_format(state)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化完成",
                    data={"msg": f"后续将以[{str(target_format_path)}]作为问题数据"}
                ).json())
            state["judger"]["eval_problem_path"] = str(target_format_path)
        else:
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化结束",
                    data={"msg": '未设置[eval_format_type]参数，跳过数据格式化过程'}
                ).json())
        state["judger"]["output_problem_path"] = state["judger"]["eval_problem_path"]
        return state

    @staticmethod
    @BaseAgent.set_current
    def generate_node(state: LoopAIState) -> LoopAIState:
        task_type = state.get("judger", {}).get("eval_task_type", "code")
        writer = get_stream_writer()
        match task_type:
            case "code":  # code
                res = generate_sample_code(state)
            case "text2sql":
                res = generate_sample_sql(state)
        state["judger"]["output_case_path"] = res
        return state

    @staticmethod
    @BaseAgent.set_current
    def evaluate_node(state: LoopAIState) -> LoopAIState:
        task_type = state.get("judger", {}).get("eval_task_type", "code")
        
        res = {}

        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message=f"{task_type}任务评测样本开始",
                data={"msg": ''}
            ).json())
        match task_type:
            case "code":  # code
                res = evaluate_sample(state)
            case "text2sql":
                res = evaluate_sample_sql(state)
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"{task_type}任务评测样本结果",
                data=res["pass_at_k"]
            ).json())
        state["judger"]["output_result_path"] = res["result_path"]
        return state

    @staticmethod
    @BaseAgent.set_current
    def check_param_type_next(state: LoopAIState) -> str:
        """
        如果已启动vllm则直接跳过本地启动阶段
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        if _isNotNone(base_url):
            return "to_data_format"
        else:
            return "to_vllm_kill"

    @staticmethod
    @BaseAgent.set_current
    def evaluate_next(state: LoopAIState) -> str:
        """
        如果已完成则结束未完成则开启本地vllm
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        vllm_port = state.get("judger", {}).get("eval_vllm_port", None)
        # 判断是否为空，为空则开启本地
        if not _isNotNone(base_url):
            return "to_vllm_kill"
        else:
            # 判断是否为本地路由，如果是则kill掉本地vllm，否则直接结束
            if state["judger"]["eval_base_url"] == f"http://localhost:{vllm_port}/v1":
                return "to_vllm_kill"
            else:
                return "to_end"

    @staticmethod
    @BaseAgent.set_current
    def vllm_kill_next(state: LoopAIState) -> str:
        """
        如果已完成则结束未完成则开启本地vllm
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        # 判断是否为空，为空则开启本地
        if not _isNotNone(base_url):
            return "to_vllm_start"
        else:
            return "to_end"

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("check_param_type", self.check_param_type_node)
        builder.add_node("vllm_kill", self.vllm_kill_node)
        builder.add_node("vllm_start", self.vllm_start_node)
        builder.add_node("data_format", self.data_format_node)
        builder.add_node("generate_code", self.generate_node)
        builder.add_node("generate_text2sql", generate_sample_text2sql)
        builder.add_node("evaluate", self.evaluate_node)
        builder.add_edge("check_required_fields", "check_param_type")
        builder.add_edge("vllm_start", "data_format")
        builder.add_edge("data_format", "generate_code")
        builder.add_edge("generate_code", "evaluate")
        builder.add_edge("generate_text2sql", "evaluate")
        builder.set_entry_point("check_required_fields")

        builder.add_conditional_edges(
            source="check_param_type",  # 来源节点（从哪个节点跳转出来）
            path=self.check_param_type_next,  # 路由函数（执行条件判断，返回路由键）
            path_map={  # 路由键-目标节点映射（路由键对应到具体节点）
                "to_vllm_kill": "vllm_kill",  # 路由键1 → 计算节点
                "to_data_format": "data_format",     # 路由键2 → 文本处理节点
            }
        )

        builder.add_conditional_edges(
            source="evaluate", 
            path=self.evaluate_next,
            path_map={
                "to_vllm_kill": "vllm_kill",
                "to_end": END,
            }
        )

        builder.add_conditional_edges(
            source="vllm_kill", 
            path=self.vllm_kill_next,
            path_map={
                "to_vllm_start": "vllm_start",
                "to_end": END,
            }
        )

        self.graph = builder.compile(
            checkpointer=self.checkpointer, store=self.store, **kwargs)

    def __call__(self, **kwargs):
        """
        build and return self.graph

        Args:
            kwargs: keyword arguments to pass to init_graph
        """
        self.init_graph(**kwargs)
        return self.graph
