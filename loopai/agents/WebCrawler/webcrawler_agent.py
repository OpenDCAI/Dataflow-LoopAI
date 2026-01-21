import json
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from .nodes import start_node as _start_node, crawl_node as _crawl_node, webcrawler_dataset_node as _webcrawler_dataset_node, end_node as _end_node

logger = get_logger()


class WebCrawlerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "WebCrawler"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"
    
    @staticmethod
    @BaseAgent.set_current
    def start_node(state: LoopAIState) -> LoopAIState:
        return _start_node(state)
    
    @staticmethod
    @BaseAgent.set_current
    def end_node(state: LoopAIState) -> LoopAIState:
        return _end_node(state)
    
    @staticmethod
    @BaseAgent.set_current
    def crawl_node(state: LoopAIState) -> LoopAIState:
        return _crawl_node(state)
    
    @staticmethod
    @BaseAgent.set_current
    def webcrawler_dataset_node(state: LoopAIState) -> LoopAIState:
        return _webcrawler_dataset_node(state)

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.start_node)
        builder.add_node("crawl_node", self.crawl_node)
        builder.add_node("webcrawler_dataset_node", self.webcrawler_dataset_node)
        builder.add_node("end_node", self.end_node)
        
        builder.set_entry_point("start_node")
        builder.add_edge("start_node", "crawl_node")
        builder.add_edge("crawl_node", "webcrawler_dataset_node")
        builder.add_edge("webcrawler_dataset_node", "end_node")
        builder.set_finish_point("end_node")
        
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