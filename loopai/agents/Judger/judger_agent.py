import json
import os
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState, RuntimeContext, get_missing_fields
from loopai.agents import BaseAgent
from .utils.oj.generate import generate_sample, generate_sample_sql
from .utils.oj.evaluate import evaluate_sample, evaluate_sample_sql
from .utils.oj.format import data_format
from .utils.oj.data import check_file
from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger

logger = get_logger()


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
            required_fields = {
                'judger':["eval_model_path", "eval_base_url", "eval_api_key", "eval_temperature",
                               "eval_top_p", "eval_problem_path", "eval_batch_size", "eval_case_num", "eval_task_type", "output_dir"]
            }
            missing_fields = get_missing_fields(required_fields, state)

            '''数据有效检查'''
            check_result = check_file(state)
            for key, value in check_result.items():
                is_true = bool(value)
                if not is_true:
                    missing_fields.setdefault("judger", []).append(key)
            '''检查text2sql必要字段'''
            if not missing_fields:
                if state.get("judger", {}).get("eval_text2sql_dir", "") == "text2sql":
                    missing_fields = get_missing_fields({'judger':["eval_text2sql_dir"]}, state)
            if missing_fields:
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
        return check_required_fields

    @staticmethod
    @BaseAgent.set_current
    def data_format_node(state: LoopAIState) -> LoopAIState:
        problem_path = state.get("judger", {}).get("eval_problem_path", "")
        format_type = state.get("judger", {}).get("eval_format_type", None)
        state_task_id = state.get("task_id")
        output_dir = state.get("judger", {}).get("output_dir", "/")
        problem_file_name = os.path.splitext(os.path.basename(problem_path))[0]
        writer = get_stream_writer()
        if(format_type != "" and format_type is not None):
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=0.0,
                    message="任务数据格式化开始",
                    data={"msg": f"由[{problem_path}]以[{format_type}]格式转存为[{output_dir}{state_task_id}/{problem_file_name}_format.jsonl]"}
                ).json())
            data_format(state)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化完成",
                    data={"msg": f"后续将以[{output_dir}{state_task_id}/{problem_file_name}_format.jsonl]作为问题数据"}
                ).json())
            state["judger"]["eval_problem_path"] = f"{output_dir}{state_task_id}/{problem_file_name}_format.jsonl"
        else:
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化结束",
                    data={"msg": '未设置[eval_format_type]参数，跳过数据格式化过程'}
                ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def generate_node(state: LoopAIState) -> LoopAIState:
        writer = get_stream_writer()
        problem_path = state.get("judger", {}).get("eval_problem_path", "")
        case_num = state.get("judger", {}).get("eval_case_num", 10)
        task_type = state.get("judger", {}).get("eval_task_type", "code")
        problem_file_name = os.path.splitext(os.path.basename(problem_path))[0]
        state_task_id = state.get("task_id")
        output_dir = state.get("judger", {}).get("output_dir", "/")

        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message=f"{task_type}任务样本合成开始",
                data={"msg": f"对[{problem_path}]每个问题生成[{case_num}]条样例数据"}
            ).json())

        match task_type:
            case "code":  # code
                generate_sample(state)
            case "text2sql":  # text2sql
                generate_sample_sql(state)

        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"{task_type}任务样本合成完成",
                data={"msg": f"结果保存为[{output_dir}{state_task_id}/{problem_file_name}_sample.jsonl]"}
            ).json())

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
                data=res
            ).json())
        return state
    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("data_format", self.data_format_node)
        builder.add_node("generate", self.generate_node)
        builder.add_node("evaluate", self.evaluate_node)
        builder.add_edge("check_required_fields", "data_format")
        builder.add_edge("data_format", "generate")
        builder.add_edge("generate", "evaluate")
        builder.set_entry_point("check_required_fields")
        builder.set_finish_point("evaluate")
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
