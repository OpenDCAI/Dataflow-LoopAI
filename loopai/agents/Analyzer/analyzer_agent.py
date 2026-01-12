import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState, RuntimeContext
from loopai.agents import BaseAgent
from .nodes import eval_model_node, analyze_result_node, draw_conclusion_node

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
            required_fields = {
                "analyzer": [
                    "analyze_model_path", "analyze_base_url", "analyze_api_key", "analyze_temperature", "analyze_top_p", 
                    "output_brief", "analyze_task_type",  "analyze_sampling_top_k", "output_suggestion", "analyze_batch_size"
                ],
                "judger": ["eval_result_path"],
                "default": ["output_dir"]
            }
            missing_fields = {}
            for key in required_fields:
                for field in required_fields[key]:
                    if key == 'default':
                        if field not in state:
                            missing_fields.setdefault(key, []).append(field)
                    else:
                        if field not in state.get(key, {}):
                            missing_fields.setdefault(key, []).append(field)
            if missing_fields:
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = self.prompt_loader("automated_query", "analyzer_missing_fields_prompt")
                state.setdefault('configer', {})['configer_error'] = missing_fields
                goto_node = runtime.context['exception_navigate']
                logger.info(f'found missing fields, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )
        return check_required_fields

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("eval_model", eval_model_node)
        builder.add_node("analyze_result", analyze_result_node)
        builder.add_node("draw_conclusion", draw_conclusion_node)
        builder.add_edge("check_required_fields", "eval_model")
        builder.add_edge("eval_model", "analyze_result")
        builder.add_edge("analyze_result", "draw_conclusion")
        builder.set_entry_point("check_required_fields")
        builder.set_finish_point("draw_conclusion")
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
