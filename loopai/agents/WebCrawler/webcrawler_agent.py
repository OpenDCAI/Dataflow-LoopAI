import json
from typing import Any, Dict, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger

from .nodes import start_node, crawl_node, webcrawler_dataset_node, end_node

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
    
    def get_start_node(self):
        """
        Get start node function that can access self
        """
        @BaseAgent.set_current
        def _start_node(state: LoopAIState):
            return start_node(state, self)
        return _start_node
    
    def get_end_node(self):
        """
        Get end node function that can access self
        """
        @BaseAgent.set_current
        def _end_node(state: LoopAIState):
            return end_node(state)
        return _end_node
    
    def get_crawl_node(self):
        """
        Get crawl node function that can access self
        """
        @BaseAgent.set_current
        def _crawl_node(state: LoopAIState):
            return crawl_node(state)
        return _crawl_node
    
    def get_webcrawler_dataset_node(self):
        """
        Get webcrawler dataset node function that can access self
        """
        @BaseAgent.set_current
        def _webcrawler_dataset_node(state: LoopAIState):
            return webcrawler_dataset_node(state)
        return _webcrawler_dataset_node

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("start_node", self.get_start_node())
        builder.add_node("crawl_node", self.get_crawl_node())
        builder.add_node("webcrawler_dataset_node", self.get_webcrawler_dataset_node())
        builder.add_node("end_node", self.get_end_node())
        
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