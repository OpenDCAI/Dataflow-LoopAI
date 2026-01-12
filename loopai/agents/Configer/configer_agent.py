import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState, get_state_config_schema
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent

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
        return "configer_prompt"

    def compute_prompt(self):
        """
        compute the prompt for LLM node

        Returns:
            the prompt string
        """
        system_prompt = self.prompt_loader(
            self.system_prompt_type, self.system_prompt_name)
        
        def annotations_to_str(ann: dict) -> dict:
            return {k: str(v) for k, v in ann.items()}
        state_dict = annotations_to_str(LoopAIState.__annotations__)

        fields_statement = get_state_config_schema()

        system_prompt = system_prompt.format(state_dict=json.dumps(state_dict, ensure_ascii=False), fields_statement=json.dumps(fields_statement, ensure_ascii=False))
        return system_prompt

    @staticmethod
    @BaseAgent.set_current
    def configer_query_node(state: LoopAIState):
        """
        configer query node
        """
        configer_error = state.get('configer', {}).get('configer_error', None)
        if configer_error is None or configer_error == '':
            query = interrupt("Input your requirement for configer.")
        else:
            query = f"System shows that I have a configer error: {json.dumps(configer_error, ensure_ascii=False)}"
        return {
            'messages': [
                {"role": "user", "content": query}
            ],
            'configer': {'configer_error': None}
        }

    @staticmethod
    def should_continue(state: LoopAIState) -> LoopAIState:
        """Get the last ToolMessage and decide the next node, if the tool is not called, go to configer_query_node"""
        messages = state["messages"]
        if len(messages) < 3:
            return "configer_query_node"
        maybe_tool_message = messages[-2]
        if hasattr(maybe_tool_message, 'tool_call_id'):
            tool_res = json.loads(maybe_tool_message.content)
            if tool_res.get('confirm', False):
                return "confirm_node"
            else:
                return "configer_query_node"
        return "configer_query_node"

    @staticmethod
    @BaseAgent.set_current
    def confirm_node(state: LoopAIState):
        """
        confirm node
        """
        messages = state["messages"]
        if len(messages) < 3:
            return "configer_query_node"
        maybe_tool_message = messages[-2]
        if hasattr(maybe_tool_message, 'tool_call_id'):
            tool_res = json.loads(maybe_tool_message.content)
            logger.info('revised:' + json.dumps(tool_res['revised_config']))
        return tool_res['revised_config']

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("configer_query_node", self.configer_query_node)
        builder.add_node("configer_llm_node", self.llm_node)
        builder.add_node("confirm_node", self.confirm_node)
        builder.set_entry_point("configer_query_node")
        builder.add_edge("configer_query_node", "configer_llm_node")
        builder.add_conditional_edges(
            "configer_llm_node",
            self.should_continue)
        builder.set_finish_point("confirm_node")

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
