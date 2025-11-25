import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger

logger = get_logger()


class ConfigerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Configer"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"

    @staticmethod
    def update_config_node(state: LoopAIState):
        """
        update the config node
        """
        allow_config_keys = ["model_path", "eval_data_path", "mined_data", "update_model_path"]
        value = interrupt(f"input config value, format as json with keys: {', '.join(allow_config_keys)}")
        logger.info(f"input config value: {value}")
        if type(value) == str:
            try:
                value = json.loads(value)
            except:
                value = {}
            logger.info(f"parse config value: {value}")
        for key in value:
            if key in allow_config_keys:
                state[key] = value[key]
        
        return state

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("update_config_node", self.update_config_node)
        builder.set_entry_point("update_config_node")
        builder.set_finish_point("update_config_node")

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
