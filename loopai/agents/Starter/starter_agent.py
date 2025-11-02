from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command

from loopai.states.base import UserState
from loopai.agents import BaseAgent

from loopai.logger import get_logger

logger = get_logger()


class StarterAgent(BaseAgent):
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

    @staticmethod
    def evaluate_node(state: UserState) -> UserState:
        """Evaluate the model"""
        state["current"] = "evaluate"
        state["next_to"] = "query_node"
        logger.info("Exec: Evaluate the model, next_to: query_node")
        return state

    @staticmethod
    def train_node(state: UserState) -> UserState:
        """Train the model"""
        state["current"] = "train"
        state["next_to"] = "query_node"
        logger.info("Exec: Train the model, next_to: query_node")
        return state

    @staticmethod
    def obtain_node(state: UserState) -> UserState:
        """Obtain the data"""
        state["current"] = "obtain"
        state["next_to"] = "query_node"
        logger.info("Exec: Obtain the data, next_to: query_node")
        return state

    @staticmethod
    def query_node(state: UserState) -> UserState:
        """Chat with the user"""
        value = interrupt('input the human query')
        state['current'] = 'query'
        state["next_to"] = "llm_node"
        logger.info(f"Exec: Query node, next_to: {state['next_to']}")
        return {
            'messages': [{'role': 'user', 'content': value}]
        }

    @staticmethod
    def feedback_node(state: UserState) -> UserState:
        """Get the last ToolMessage and decide the next node, if the tool is not called, go to query_node"""
        messages = state["messages"]
        if len(messages) < 3:
            state["next_to"] = "query_node"
            return state
        last_message = messages[-1]
        maybe_tool_message = messages[-2]
        if hasattr(maybe_tool_message, 'tool_call_id'):
            val = maybe_tool_message.content
            if val == 'train':
                state["next_to"] = "train_node"
            elif val == 'obtain':
                state["next_to"] = "obtain_node"
            elif val == 'evaluate':
                state["next_to"] = "evaluate_node"
            else:
                state["next_to"] = "query_node"
            last_message.content = '根据用户指令执行: ' + val
        else:
            state["next_to"] = "query_node"
        logger.info(f'Messages: {state["messages"]}')
        logger.info(f"Exec: Feedback node, next_to: {state['next_to']}")
        return state

    @staticmethod
    def end_node(state: UserState) -> UserState:
        """End the conversation"""
        state["current"] = "end"
        return state

    @staticmethod
    def conditional_edge(state: UserState):
        next_to = state["next_to"]
        if next_to == "llm_node":
            return "llm_node"
        elif next_to == "query_node":
            return "query_node"
        elif next_to == "train_node":
            return "train_node"
        elif next_to == "obtain_node":
            return "obtain_node"
        elif next_to == "evaluate_node":
            return "evaluate_node"
        elif next_to == "end_node":
            return "end_node"
        return "query_node"

    def init_graph(self, **kwargs):
        builder = StateGraph(UserState)
        builder.add_node("query_node", self.query_node)
        builder.add_node("llm_node", self.llm_node)
        builder.add_node("feedback_node", self.feedback_node)
        builder.add_node("train_node", self.train_node)
        builder.add_node("obtain_node", self.obtain_node)
        builder.add_node("evaluate_node", self.evaluate_node)
        builder.add_node("end_node", self.end_node)
        builder.set_entry_point("query_node")
        builder.set_finish_point("end_node")
        builder.add_edge('query_node', 'llm_node')
        builder.add_edge('llm_node', 'feedback_node')
        builder.add_edge('evaluate_node', 'query_node')
        builder.add_edge('train_node', 'query_node')
        builder.add_edge('obtain_node', 'query_node')
        builder.add_conditional_edges(
            "feedback_node",
            self.conditional_edge)

        self.graph = builder.compile(
            checkpointer=self.checkpointer, store=self.store, **kwargs)

    def __call__(self, **invoke_args):
        """
        run invoke method
        """
        self.graph.invoke({}, **invoke_args)
        return self.graph
