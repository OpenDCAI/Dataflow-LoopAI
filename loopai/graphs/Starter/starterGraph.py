from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph

from loopai.states.base import UserState
from loopai.graphs import BaseGraph

class StarterGraph(BaseGraph):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Starter"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"
    
    def init_graph(self):
        builder = StateGraph(UserState)
        builder.add_node("llm_node", self.llm_node)
        builder.set_entry_point("llm_node")
        builder.set_finish_point("llm_node")
        self.graph = builder.compile()

    def __call__(self, content):
        """
        run invoke method
        """
        return self.graph.invoke(
            {"messages": [{"role": "user", "content": content}]}
        )
