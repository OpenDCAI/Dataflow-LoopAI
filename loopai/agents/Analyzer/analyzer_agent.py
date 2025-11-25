import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState
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
    
    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("eval_model", eval_model_node)
        builder.add_node("analyze_result", analyze_result_node)
        builder.add_node("draw_conclusion", draw_conclusion_node)
        builder.add_edge("eval_model", "analyze_result")
        builder.add_edge("analyze_result", "draw_conclusion")
        builder.set_entry_point("eval_model")
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
