import json
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
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
        return "configer_prompt"
    
    def get_custom_llm_node(self):
        """
        get the llm node
        """
        def custom_llm_node(state: LoopAIState):
            """
            call LLM
            """
            system_prompt = self.prompt_loader(self.system_prompt_type, self.system_prompt_name)
            responses = self.llm.batch([[{"role": "system", "content": system_prompt}, {"role": "user", "content": state['configer_error']}]])
            state['configer_error'] = responses[0].content
            logger.info(f"LLM response: {responses[0].content}")
            return state
        return custom_llm_node
    
    @staticmethod
    @BaseAgent.set_current
    def graph_statement_node(state: LoopAIState):
        """
        配置器图的声明节点，作为状态图的入口点。
        
        此函数负责初始化配置过程，确保状态中包含必要的错误信息字段，
        并使用流写入器输出当前状态和配置器错误信息。
        
        Args:
            state: LoopAIState 类型的状态对象，包含当前工作流程的状态信息
            
        Returns:
            LoopAIState: 更新后的状态对象，确保包含 'configer_error' 字段
        """
        if 'configer_error' not in state:
            state['configer_error'] = 'None'
        return state

    @staticmethod
    @BaseAgent.set_current
    def update_config_node(state: LoopAIState):
        """
        update the config node
        """
        not_allow_config_keys = ["task_id", "current", "next_to", "configer_error", "configer_statement", "eval_result_path", "analyze_output_result_path", "analyze_output_summary_path", "analyze_output_report_json_path", "analyze_output_report_text_path", "analyze_output_suggestion_path"]
        allow_config_keys = [
            key for key in LoopAIState.__annotations__ if key not in not_allow_config_keys]
        value = interrupt(
            f"input config value, format as json with keys: {', '.join(allow_config_keys)}")
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
        
        writer = get_stream_writer()
        writer(StreamEvent(current=state['current'],
               message=state['configer_error']).json())

        return state

    def init_graph(self, **kwargs):
        custom_llm_node = self.get_custom_llm_node()
        builder = StateGraph(LoopAIState)
        builder.add_node("graph_statement_node", self.graph_statement_node)
        builder.add_node("configer_llm_node", custom_llm_node)
        builder.add_node("update_config_node", self.update_config_node)
        builder.set_entry_point("graph_statement_node")
        builder.add_edge("graph_statement_node", "configer_llm_node")
        builder.add_edge("configer_llm_node", "update_config_node")
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