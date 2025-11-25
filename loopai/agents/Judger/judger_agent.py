import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from .utils.oj.generate import generate_sample
from .utils.oj.evaluate import evaluate_sample

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
    
    @staticmethod
    def generate_node(state: LoopAIState) -> LoopAIState:
        generate_sample(model_path=state['eval_model_path'], base_url=state['eval_base_url'], api_key=state['eval_api_key'], temperature=state['eval_temperature'], top_p=state['eval_top_p'], test_case_path=state['eval_test_case_path'], problem_path=state['eval_problem_path'], batch_size=state['eval_batch_size'])
        return state
    
    @staticmethod
    def evaluate_node(state: LoopAIState) -> LoopAIState:
        evaluate_sample(K='1,10,100', n_workers=1, timeout=3.0, test_case_path=state['eval_test_case_path'], problem_path=state['eval_problem_path'], result_path=state['eval_result_path'], test_code_function_name='test_code_example', test_function_name='test_example', entry_point_function_name='entry_point_example')
        return state
    
    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("generate", self.generate_node)
        builder.add_node("evaluate", self.evaluate_node)
        builder.add_edge("generate", "evaluate")
        builder.set_entry_point("generate")
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
