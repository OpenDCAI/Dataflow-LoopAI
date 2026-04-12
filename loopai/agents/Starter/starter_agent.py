import json
import os
import time
from typing import Any, Dict, List, Optional, Type

from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from loopai.schema.states import LoopAIState, RuntimeContext
from loopai.schema.events import StreamEvent
from loopai.agents import BaseAgent
from loopai.agents.Configer import ConfigerAgent
from loopai.agents.Judger import JudgerAgent
from loopai.agents.Analyzer import AnalyzerAgent
from loopai.agents.Obtainer import ObtainerAgent
from loopai.agents.Constructor import ConstructorAgent
from loopai.agents.Trainer import TrainerAgent
from loopai.agents.WebCrawler import WebCrawlerAgent

from loopai.agents.Configer.tools.check_config import check_config

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
    @BaseAgent.set_current
    def evaluate_node(state: LoopAIState) -> LoopAIState:
        """Evaluate the model"""
        logger.info("Exec: Evaluate the model, next_to: query_node")
        return state

    @staticmethod
    @BaseAgent.set_current
    def query_node(state: LoopAIState) -> LoopAIState:
        """Chat with the user"""
        if "automated_query" in state and state["automated_query"]:
            value = state["automated_query"]
        else:
            value = interrupt('input the human query')
        writer = get_stream_writer()
        logger.info(f"Exec: Query node")
        if value == '> $resume$':
            writer(StreamEvent(
                current=state['current'],
                message=f"Exec: Query node with resume the saved chat."
            ).json())
            return {
                'next_to': 'query_node'
            }
        writer(StreamEvent(
            current=state['current'],
            message=f"Exec: Query node with common query."
        ).json())
        return {
            'messages': [{'role': 'user', 'content': value}],
            'automated_query': '',
            'next_to': 'llm_node'
        }

    @staticmethod
    @BaseAgent.set_current
    def feedback_node(state: LoopAIState) -> LoopAIState:
        """Get the last ToolMessage and decide the next node, if the tool is not called, go to query_node"""
        messages = state["messages"]
        if len(messages) < 3:
            state["next_to"] = "query_node"
            return state
        last_message = messages[-1]
        maybe_tool_message = messages[-2]
        if hasattr(maybe_tool_message, 'tool_call_id'):
            tool_res = json.loads(maybe_tool_message.content)
            state["next_to"] = tool_res.get("next_to", "query_node")
            last_message.content = '<cmd>根据用户指令执行: ' + \
                tool_res.get("motivation", "chat") + \
                '</cmd>\n' + last_message.content
        else:
            state["next_to"] = "query_node"
        logger.info(f'Messages: {state["messages"]}')
        logger.info(f"Exec: Feedback node, next_to: {state['next_to']}")
        return state

    @staticmethod
    @BaseAgent.set_current
    def route_node(state: LoopAIState) -> LoopAIState:
        """Route the model"""
        if "exception" in state and state["exception"] == 'ConfigerError':
            logger.info(f'Exception: {state["exception"]}')
            state['exception'] = ""
            state["next_to"] = "config_node"
        else:
            state["next_to"] = "query_node"
        return state

    @staticmethod
    @BaseAgent.set_current
    def end_node(state: LoopAIState) -> LoopAIState:
        """End the conversation"""
        return state

    @staticmethod
    def conditional_edge(state: LoopAIState):
        return state["next_to"]

    def init_graph(self, **kwargs):
        config_node = ConfigerAgent(model_name=self.model_name,
                                    base_url=self.base_url,
                                    tools=[check_config],
                                    api_key=self.api_key,
                                    checkpointer=self.checkpointer,
                                    store=self.store)(**kwargs)
        judge_node = JudgerAgent(checkpointer=self.checkpointer,
                                 store=self.store)(**kwargs)
        analyze_node = AnalyzerAgent(checkpointer=self.checkpointer,
                                     store=self.store)(**kwargs)
        train_node = TrainerAgent(checkpointer=self.checkpointer,
                                  store=self.store)(**kwargs)
        # ObtainerAgent will use model_name, base_url, api_key from StarterAgent
        # But it also needs to get these from state if not provided in constructor
        # So we pass them to ensure consistency
        obtainer_node = ObtainerAgent(
            model_name=self.model_name,
            base_url=self.base_url,
            api_key=self.api_key,
            checkpointer=self.checkpointer,
            store=self.store
        )(**kwargs)
        # ConstructorAgent will use model_name, base_url, api_key from StarterAgent
        # It processes the downloaded data from ObtainerAgent
        constructor_node = ConstructorAgent(
            model_name=self.model_name,
            base_url=self.base_url,
            api_key=self.api_key,
            checkpointer=self.checkpointer,
            store=self.store
        )(**kwargs)
        # WebCrawlerAgent for web crawling and dataset generation
        webcrawler_node = WebCrawlerAgent(
            model_name=self.model_name,
            base_url=self.base_url,
            api_key=self.api_key,
            checkpointer=self.checkpointer,
            store=self.store
        )(**kwargs)
        builder = StateGraph(LoopAIState, context_schema=RuntimeContext)
        builder.add_node("query_node", self.query_node)
        builder.add_node("llm_node", self.llm_node)
        builder.add_node("feedback_node", self.feedback_node)
        builder.add_node("route_node", self.route_node)
        builder.add_node("train_node", train_node)
        # Use ObtainerAgent subgraph
        builder.add_node("obtain_node", obtainer_node)
        # Use ConstructorAgent subgraph
        builder.add_node("constructor_node", constructor_node)
        builder.add_node("evaluate_node", self.evaluate_node)
        builder.add_node("config_node", config_node)
        builder.add_node("judge_node", judge_node)
        builder.add_node("analyze_node", analyze_node)
        builder.add_node("webcrawler_node", webcrawler_node)
        builder.add_node("end_node", self.end_node)

        builder.set_entry_point("query_node")
        builder.set_finish_point("end_node")

        builder.add_conditional_edges(
            "query_node",
            self.conditional_edge)
        builder.add_edge('llm_node', 'feedback_node')
        builder.add_edge('evaluate_node', 'query_node')
        builder.add_edge('train_node', 'route_node')
        # Obtainer -> route_node
        builder.add_edge('obtain_node', 'route_node')
        # Constructor -> route_node
        builder.add_edge('constructor_node', 'route_node')
        builder.add_edge('config_node', 'query_node')
        builder.add_edge('judge_node', 'route_node')
        builder.add_edge('analyze_node', 'route_node')
        builder.add_edge('webcrawler_node', 'route_node')
        builder.add_edge('webcrawler_node', 'route_node')
        builder.add_conditional_edges(
            "feedback_node",
            self.conditional_edge)
        builder.add_conditional_edges(
            "route_node",
            self.conditional_edge)

        self.graph = builder.compile(
            checkpointer=self.checkpointer, store=self.store, **kwargs)

    def start(self, default_state={}, **invoke_args):
        """
        start the graph
        """
        self.graph.invoke(default_state, **invoke_args)

    def get_state(self, config: dict, subgraphs=False):
        """
        get the state of the graph
        """
        return self.graph.get_state(config, subgraphs=subgraphs)

    def __call__(self, input, **invoke_args):
        """
        run invoke method
        """
        try:
            min_ms = float(os.getenv("LOOPAI_CUSTOM_YIELD_MIN_INTERVAL_MS", "0") or "0")
        except (TypeError, ValueError):
            min_ms = 0.0
        min_custom_yield_s = max(0.0, min_ms / 1000.0)
        last_custom_yield_mono: Optional[float] = None
        pending_custom_tail = None

        for res in self.graph.stream(
            Command(resume=input),
            subgraphs=True,
            stream_mode=["updates", "messages", "custom"],
            context={
                "exception_navigate": "route_node"
            },
            **invoke_args
        ):
            namespace_item, stream_mode, chunk_item = res
            self.agent_event.stream_mode = stream_mode
            # Receiving messages event, update stream_message
            if stream_mode == 'messages':
                msg_chunk = chunk_item[0]
                meta_data = chunk_item[1]
                # tags like ['Starter-LLM'] or ['Configer-LLM'], currently set as allowed all.
                if 'tags' in meta_data and (self.llm_tag in meta_data['tags'] or True):
                    self.agent_event.set_stream_message(msg_chunk)
                else:
                    continue # skip other messages event to prevent llm batch callback congestion.
            # Receiving custom event, update custom_info
            elif stream_mode == 'custom':
                if len(namespace_item) > 0:
                    key = namespace_item[0]

                    # if detect llm_node custom event and stream_message_state is finished, clear stream_message
                    # you should realize that there is a delay between update event and custom event
                    # so we need to clear the stream_message of the last turn, and you can fetch them in the llm_node.data.history
                    if chunk_item.get('data', {}) is not None and 'stream_message_state' in chunk_item.get('data', {}):
                        stream_message_state = chunk_item.get('data', {}).get(
                            'stream_message_state', 'not_ready')
                        if stream_message_state == 'start':
                            self.agent_event.clear_stream_message()
                else:
                    key = '__starter__'
                self.agent_event.set_custom_info(key, chunk_item)
                self.agent_event.set_running_tasks(self.get_state(
                        invoke_args['config'], subgraphs=True).tasks)
                if min_custom_yield_s > 0:
                    now = time.monotonic()
                    if (
                        last_custom_yield_mono is not None
                        and (now - last_custom_yield_mono) < min_custom_yield_s
                    ):
                        pending_custom_tail = res
                        continue
                    last_custom_yield_mono = now
                    pending_custom_tail = None
            # Receiving updates event, update state, and clear stream_message
            elif stream_mode == 'updates':
                if len(namespace_item) > 0:
                    continue
                for key in chunk_item:
                    if key == '__interrupt__':
                        continue
                    self.agent_event.node = key
                    self.agent_event.set_path(key)
                    self.agent_event.update_state(
                        self.get_state(invoke_args['config']).values)
                    self.agent_event.set_running_tasks(self.get_state(
                        invoke_args['config'], subgraphs=True).tasks)
                    self.agent_event.clear_stream_message()
            yield res
            pending_custom_tail = None

        if pending_custom_tail is not None:
            yield pending_custom_tail
