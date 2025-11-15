from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

from loopai.common.prompts import PromptLoader
from loopai.schema.events import AgentEvent
from loopai.logger import get_logger

logger = get_logger()

class BaseAgent(ABC):

    def __init__(self,
                 tools: Optional[List] = [],
                 model_name: Optional[str] = None,
                 base_url: Optional[str] = None,
                 api_key: Optional[str] = 'empty',
                 temperature: float = 0.0,
                 top_p: float = 0.95,
                 max_completion_tokens: int = 4096,
                 prompt_template_dir: str = None,
                 checkpointer = None,
                 store = None
                 ):
        '''
        Init BaseAgent

        Args:
            tools: the list of tool function
            model_name: the name of the LLM
            base_url: the base_url of LLM server
            api_key: the api_key of LLM server
            temperature: the temperature of LLM
            top_p: the top_p of LLM
            max_completion_tokens: the max new tokens of LLM
            prompt_template_dir: the directory of prompt config file: endswith `_prompt.json`.
            checkpointer: the checkpointer function
            store: the store function
        '''
        self.tools = tools
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.top_p = top_p
        self.llm_tag = f"{self.role_name}-LLM"
        self.max_completion_tokens = max_completion_tokens
        self.prompt_template_dir = prompt_template_dir
        self.checkpointer = checkpointer
        self.store = store
        self.agent_event = AgentEvent()

        self.prompt_loader = PromptLoader(prompt_template_dir)
        if self.model_name is not None:
            self.create_llm_node()

    @property
    @abstractmethod
    def role_name(self) -> str:
        """Role name"""
        pass

    @property
    @abstractmethod
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    @abstractmethod
    def system_prompt_name(self) -> str:
        """System prompt name"""
        pass

    @staticmethod
    def set_current(func):
        def wrapper(state: Dict, *args, **kwargs):
            state['current'] = func.__qualname__
            return func(state, *args, **kwargs)
        return wrapper
    
    def create_llm_node(self):
        if self.base_url is None:
            logger.error(f'Undefined base_url in {self.role_name}-Graph')
            raise AssertionError(f'Undefined base_url in {self.role_name}-Graph')
        self.llm = ChatOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model_name,
            temperature=self.temperature,
            top_p=self.top_p,
            max_completion_tokens=self.max_completion_tokens,
            tags=[self.llm_tag]
        )

        logger.info(f'{self.role_name}-Graph use prompt: {self.prompt_loader(self.system_prompt_type, self.system_prompt_name)}')
        self.llm_node = create_react_agent(
            model=self.llm,
            tools=self.tools,
            prompt=self.prompt_loader(self.system_prompt_type, self.system_prompt_name)
        )
    
    @abstractmethod
    def init_graph(self):
        """
        define nodes and edges in this graph, and compile the graph
        """
        pass

    @abstractmethod
    def __call__(self):
        """
        - run invoke method
        - or run `init_graph` and return the `graph`
        """
        pass
