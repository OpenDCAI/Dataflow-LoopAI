import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState, RuntimeContext
from loopai.agents import BaseAgent
from .nodes import (
    eval_model_node,
    eval_general_text_node,
    analyze_result_node,
    draw_conclusion_node
)

from loopai.logger import get_logger

logger = get_logger()


class AnalyzerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Analyzer"

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
            analyzer_cfg = state.get("analyzer", {}) or {}
            judger_cfg = state.get("judger", {}) or {}

            task_type = analyzer_cfg.get("analyze_task_type")
            has_bench = "bench" in state and state.get("bench") is not None

            missing_fields = {}

            # 1. 通用必填
            if "output_dir" not in state:
                missing_fields.setdefault("default", []).append("output_dir")

            if "analyze_task_type" not in analyzer_cfg:
                missing_fields.setdefault("analyzer", []).append("analyze_task_type")

            # 2. code / text2sql 的 analyzer 必填参数
            if task_type in {"code", "text2sql"}:
                analyzer_required = [
                    "analyze_model_path", "analyze_base_url", "analyze_api_key",
                    "analyze_temperature", "analyze_top_p",
                    "output_brief", "analyze_sampling_top_k",
                    "output_suggestion", "analyze_batch_size", "analyze_max_concurrency",
                    "analyze_chunk_size", "quick_brief", "quick_brief_limit"
                ]

                for field in analyzer_required:
                    if field not in analyzer_cfg:
                        missing_fields.setdefault("analyzer", []).append(field)

                # judger.out_result_path 或 analyzer.eval_result_path 二选一
                has_result_path = (
                    bool(judger_cfg.get("out_result_path"))
                    or bool(analyzer_cfg.get("eval_result_path"))
                )
                if not has_result_path:
                    missing_fields.setdefault("analyzer", []).append("eval_result_path")

            else:
                # 非 code / text2sql 时，只有在没有 bench 的情况下才要求结果路径
                if not has_bench:
                    has_result_path = (
                        bool(judger_cfg.get("out_result_path"))
                        or bool(analyzer_cfg.get("eval_result_path"))
                    )
                    if not has_result_path:
                        missing_fields.setdefault("analyzer", []).append("eval_result_path")

            if missing_fields:
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = self.prompt_loader("automated_query", "analyzer_missing_fields_prompt")
                state.setdefault('configer', {})['configer_error'] = missing_fields
                goto_node = runtime.context['exception_navigate'] if runtime and runtime.context else 'config_node'
                logger.info(f'found missing fields, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )

        return check_required_fields

    def get_route_eval_node(self):
        @BaseAgent.set_current
        def route_eval(state: LoopAIState, runtime: Runtime[RuntimeContext]):
            analyzer_cfg = state.get("analyzer", {})
            task_type = analyzer_cfg.get("analyze_task_type")

            if task_type not in {"code", "text2sql"}:
                goto = "eval_general_text"
            else:
                goto = "eval_model"

            return Command(goto=goto)

        return route_eval

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)

        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("route_eval", self.get_route_eval_node())
        builder.add_node("eval_model", eval_model_node)
        builder.add_node("eval_general_text", eval_general_text_node)
        builder.add_node("analyze_result", analyze_result_node)
        builder.add_node("draw_conclusion", draw_conclusion_node)

        builder.add_edge("check_required_fields", "route_eval")
        builder.add_edge("eval_model", "analyze_result")
        builder.add_edge("analyze_result", "draw_conclusion")
        builder.set_finish_point("eval_general_text")

        builder.set_entry_point("check_required_fields")
        builder.set_finish_point("draw_conclusion")

        self.graph = builder.compile(
            checkpointer=self.checkpointer,
            store=self.store,
            **kwargs
        )

    def __call__(self, **kwargs):
        """
        build and return self.graph

        Args:
            kwargs: keyword arguments to pass to init_graph
        """
        self.init_graph(**kwargs)
        return self.graph