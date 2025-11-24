import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState, RuntimeContext
from loopai.agents import BaseAgent
from .utils.oj.data_format import data_format
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

    def get_check_required_fields_node(self):
        @BaseAgent.set_current
        def check_required_fields(state: LoopAIState, runtime: Runtime[RuntimeContext]):
            required_fields = ["eval_model_path", "eval_base_url", "eval_api_key", "eval_temperature",
                               "eval_top_p", "eval_test_case_path", "eval_problem_path", "eval_result_path", "eval_batch_size"]
            missing_fields = []
            for field in required_fields:
                if field not in state:
                    missing_fields.append(field)
            if missing_fields:
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = self.prompt_loader(
                    "automated_query", "judger_missing_fields_prompt")
                state['configer_error'] = f'Missing required fields: {json.dumps({"missing_fields": missing_fields}, ensure_ascii=False)}'
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
        data_format()
        return state

    @staticmethod
    @BaseAgent.set_current
    def generate_node(state: LoopAIState) -> LoopAIState:
        generate_sample(model_path=state['eval_model_path'], base_url=state['eval_base_url'], api_key=state['eval_api_key'], temperature=state['eval_temperature'],
                        top_p=state['eval_top_p'], test_case_path=state['eval_test_case_path'], problem_path=state['eval_problem_path'], batch_size=state['eval_batch_size'])
        return state

    @staticmethod
    @BaseAgent.set_current
    def evaluate_node(state: LoopAIState) -> LoopAIState:
        evaluate_sample(K='1,10,100', n_workers=1, timeout=3.0, test_case_path=state['eval_test_case_path'], problem_path=state['eval_problem_path'], result_path=state[
                        'eval_result_path'])
        return state

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("data_format", self.data_format_node)
        builder.add_node("generate", self.generate_node)
        builder.add_node("evaluate", self.evaluate_node)
        builder.add_edge("check_required_fields", "generate")
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
