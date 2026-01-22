"""
Data Cleaning Subgraph - 数据清洗子图

在 mapping_subgraph 之前对中间格式数据进行清洗。
包含两个核心节点：
1. planner_node: 规划需要调用的工具（基础工具强制 + LLM判断领域工具）
2. process_node: 按顺序执行工具计划
"""
import os
import json
import re
from typing import Dict, Any, List, Optional

from langgraph.graph import StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger
from loopai.agents.BaseAgent.base_agent import BaseAgent
from loopai.agents.Constructor.tools.data_filter_tools import (
    basic_data_flitter,
    domain_text2sql_cleaner,
    domain_code_gen_cleaner,
    domain_normal_data_cleaner
)

logger = get_logger()


_cleaning_agent_instance: Optional['CleaningSubgraph'] = None


def _get_cleaning_agent() -> 'CleaningSubgraph':
    """获取全局 CleaningSubgraph 实例（单例模式）"""
    global _cleaning_agent_instance
    if _cleaning_agent_instance is None:
        _cleaning_agent_instance = CleaningSubgraph()
    return _cleaning_agent_instance


# 工具映射字典：将工具名称映射到对应的工具函数
TOOL_MAP = {
    "basic_data_flitter": basic_data_flitter,
    "text2sql": domain_text2sql_cleaner,
    "code_generate": domain_code_gen_cleaner,
    "normal_data": domain_normal_data_cleaner,
}


def _read_jsonl_file(filepath: str) -> List[Dict[str, Any]]:
    """读取JSONL文件"""
    records = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON line in {filepath}: {e}")
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
    return records


def _extract_user_query(state: LoopAIState) -> str:
    """从state中提取user_query"""
    # 优先从 automated_query 获取
    if state.get("automated_query"):
        return state.get("automated_query", "")
    
    # 从 messages 中提取最后一个 HumanMessage
    if state.get("messages") and len(state["messages"]) > 0:
        from langchain_core.messages import HumanMessage
        
        # 从后往前搜索最后一个 HumanMessage
        for message in reversed(state["messages"]):
            if isinstance(message, HumanMessage):
                if hasattr(message, "content"):
                    return message.content
            elif isinstance(message, dict):
                msg_type = message.get("type", "")
                msg_role = message.get("role", "")
                if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                    content = message.get("content", "")
                    if content:
                        return content
            elif hasattr(message, "type") and message.type == "human":
                if hasattr(message, "content"):
                    return message.content
    
    return ""


def _parse_json_list(response_text: str) -> List[str]:
    """解析LLM返回的JSON列表"""
    try:
        # 尝试直接解析JSON
        parsed = json.loads(response_text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    
    # 尝试提取代码块中的JSON
    pattern = r'```(?:json)?\s*(\[[\s\S]*?\])```'
    match = re.search(pattern, response_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    # 尝试查找方括号内的内容
    start_idx = response_text.find('[')
    end_idx = response_text.rfind(']')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            parsed = json.loads(response_text[start_idx:end_idx + 1])
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    logger.warning(f"Could not parse JSON list from LLM response: {response_text}")
    return []


async def planner_node(state: LoopAIState) -> LoopAIState:
    """
    规划节点：决定需要调用哪些工具
    
    逻辑：
    1. 先执行 basic_data_flitter 进行初始过滤
    2. 基于过滤后的数据调用LLM判断是否需要领域工具
    3. 添加领域工具（如果有）
    """
    logger.info("=== Cleaning Subgraph: Planner Node ===")
    
    try:
        # 1. 初始化 tool_plan，先添加基础工具 basic_data_flitter（始终执行，用于基础数据过滤）
        tool_plan: List[str] = ["basic_data_flitter"]
        
        # 2. 获取输入数据（从 state.obtainer 中获取）
        obtainer_state = state.get("obtainer", {})
        intermediate_data_path = obtainer_state.get("intermediate_data_path", "")
        if not intermediate_data_path:
            logger.warning("No intermediate data path found, skipping domain tool planning")
            if "obtainer" not in state:
                state["obtainer"] = {}
            state["obtainer"]["cleaning_tool_plan"] = tool_plan
            return state
        
        if not os.path.exists(intermediate_data_path):
            logger.warning(f"Intermediate data path does not exist: {intermediate_data_path}")
            if "obtainer" not in state:
                state["obtainer"] = {}
            state["obtainer"]["cleaning_tool_plan"] = tool_plan
            return state
        
        # 获取 user_query 和 category（从 state.obtainer 中获取）
        user_query = _extract_user_query(state)
        category = obtainer_state.get("category", "PT").upper()
        
        logger.info(f"Planning tools for data path: {intermediate_data_path}")
        logger.info(f"User query: {user_query[:100] if user_query else 'N/A'}...")
        logger.info(f"Category: {category}")
        
        # 3. 基于 user_query 和 datasets_background 使用LLM判断领域工具
        # 从 state.obtainer 中获取 user_query 和 datasets_background（obtainer_state 已在上面获取）
        user_query_from_state = obtainer_state.get("user_query", "")
        datasets_background = obtainer_state.get("datasets_background", "")
        
        # 如果 user_query 为空，尝试从其他地方获取
        if not user_query_from_state:
            user_query_from_state = user_query
        
        logger.info(f"User query from state: {user_query_from_state[:100] if user_query_from_state else 'N/A'}...")
        logger.info(f"Datasets background: {datasets_background[:100] if datasets_background else 'N/A'}...")
        
        # 使用LLM判断应该使用哪个领域工具
        domain_tools = []
        
        try:
            # 获取 CleaningSubgraph 实例以使用 BaseAgent 的方法
            cleaning_agent = _get_cleaning_agent()
            
            # 从 state 创建 LLM 实例（使用 BaseAgent 的方式）
            llm = cleaning_agent.create_llm_from_state(state)
            
            if llm:
                # 使用 BaseAgent 的 prompt_loader 加载 prompt（规范调用）
                system_prompt = cleaning_agent.get_prompt("system", "domain_tool_planner_prompt")
                task_prompt = cleaning_agent.get_prompt("task", "domain_tool_planner_prompt")
                
                # 构建用户提示词，包含用户需求和数据集背景
                user_prompt = task_prompt.format(
                    user_query=user_query_from_state if user_query_from_state else "Not provided",
                    datasets_background=datasets_background if datasets_background else "Not provided"
                )
                
                # 构建消息
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ]
                
                # 调用LLM
                response = await llm.ainvoke(messages)
                response_text = response.content.strip()
                
                logger.debug(f"LLM response for domain tool planning: {response_text}")
                
                # 解析JSON响应
                domain_tools = _parse_json_list(response_text)
                
                # 验证工具名称是否有效
                valid_domain_tools = [
                    tool for tool in domain_tools
                    if tool in ["text2sql", "code_generate", "normal_data"]
                ]
                
                if valid_domain_tools:
                    tool_plan.extend(valid_domain_tools)
                    logger.info(f"LLM suggested domain tools based on user_query and datasets_background: {valid_domain_tools}")
                else:
                    # 如果LLM没有返回有效工具，使用默认的 normal_data
                    tool_plan.append("normal_data")
                    logger.info("LLM did not suggest any valid domain tools, using default normal_data")
            else:
                logger.warning("Missing LLM configuration, using default normal_data tool")
                tool_plan.append("normal_data")
                
        except Exception as e:
            logger.error(f"Error calling LLM for domain tool planning: {e}", exc_info=True)
            # LLM调用失败时，使用默认的 normal_data
            tool_plan.append("normal_data")
            logger.info("Using default normal_data tool due to LLM error")
        
        # 4. 更新State（确保 tool_plan 被正确设置到 state.obtainer 中）
        if "obtainer" not in state:
            state["obtainer"] = {}
        state["obtainer"]["cleaning_tool_plan"] = tool_plan
        logger.info(f"Final tool_plan: {tool_plan}")
        logger.debug(f"State after planner_node: cleaning_tool_plan = {state.get('obtainer', {}).get('cleaning_tool_plan')}")
        
    except Exception as e:
        logger.error(f"Error in planner_node: {e}", exc_info=True)
        # 发生错误时，至少保证基础工具被执行
        if "obtainer" not in state:
            state["obtainer"] = {}
        state["obtainer"]["cleaning_tool_plan"] = ["basic_data_flitter"]
        state["exception"] = f"Error in planner_node: {str(e)}"
    
    logger.info("=== Cleaning Subgraph: Planner Node Completed ===")
    # 确保返回的 state 包含 tool_plan
    logger.debug(f"Returning state with cleaning_tool_plan: {state.get('obtainer', {}).get('cleaning_tool_plan')}")
    return state


async def process_node(state: LoopAIState) -> LoopAIState:
    """
    执行节点：按顺序执行工具计划
    
    逻辑：
    1. 获取 tool_plan
    2. 遍历执行每个工具
    3. 更新数据路径（链式调用）
    4. 更新State
    """
    logger.info("=== Cleaning Subgraph: Process Node ===")
    
    try:
        # 1. 获取 tool_plan（从 state.obtainer 中获取）
        obtainer_state = state.get("obtainer", {})
        tool_plan = obtainer_state.get("cleaning_tool_plan", [])
        logger.debug(f"Process node received state with cleaning_tool_plan: {tool_plan}")
        logger.debug(f"Full state keys: {list(state.keys())}")
        logger.debug(f"Obtainer state keys: {list(obtainer_state.keys())}")
        
        if not tool_plan:
            logger.warning("No tool_plan found, skipping process node")
            logger.warning(f"Available state keys: {list(state.keys())}")
            logger.warning(f"Obtainer state: {obtainer_state}")
            return state
        
        logger.info(f"Executing tool_plan: {tool_plan}")
        
        # 2. 获取数据路径（从 state.obtainer 中获取）
        obtainer_state = state.get("obtainer", {})
        current_data_path = obtainer_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("No intermediate data path found, skipping tool execution")
            return state
        
        if not os.path.exists(current_data_path):
            logger.warning(f"Data path does not exist: {current_data_path}")
            return state
        
        # 3. 遍历执行工具
        cleaning_results = {
            "tools_executed": [],
            "tools_failed": [],
            "final_data_path": current_data_path
        }
        
        for tool_name in tool_plan:
            try:
                logger.info(f"Executing tool: {tool_name}")
                
                # 从TOOL_MAP获取工具函数
                if tool_name not in TOOL_MAP:
                    logger.warning(f"Unknown tool: {tool_name}, skipping")
                    cleaning_results["tools_failed"].append({
                        "tool": tool_name,
                        "error": "Tool not found in TOOL_MAP"
                    })
                    continue
                
                tool_func = TOOL_MAP[tool_name]
                
                # 调用工具函数
                result = await tool_func(current_data_path, state)
                
                # 更新数据路径（链式调用）
                new_data_path = result.cleaned_data_path or current_data_path
                if new_data_path != current_data_path:
                    logger.info(f"Tool {tool_name} updated data path: {current_data_path} -> {new_data_path}")
                    current_data_path = new_data_path
                
                # 记录执行结果
                cleaning_results["tools_executed"].append({
                    "tool": tool_name,
                    "result": result
                })
                
                logger.info(f"Tool {tool_name} completed successfully")
                
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                cleaning_results["tools_failed"].append({
                    "tool": tool_name,
                    "error": str(e)
                })
                # 继续执行下一个工具，不中断流程
        
        # 4. 更新State（更新 state.obtainer 中的数据路径和清洗结果）
        if "obtainer" not in state:
            state["obtainer"] = {}
        state["obtainer"]["intermediate_data_path"] = current_data_path
        state["obtainer"]["cleaning_results"] = cleaning_results
        
        logger.info(f"Process node completed. Final data path: {current_data_path}")
        logger.info(f"Tools executed: {len(cleaning_results['tools_executed'])}, "
                   f"Tools failed: {len(cleaning_results['tools_failed'])}")
        
    except Exception as e:
        logger.error(f"Error in process_node: {e}", exc_info=True)
        state["exception"] = f"Error in process_node: {str(e)}"
    
    logger.info("=== Cleaning Subgraph: Process Node Completed ===")
    return state


class CleaningSubgraph(BaseAgent):
    """
    数据清洗子图类
    
    继承 BaseAgent 以使用统一的 LLM 创建和 prompt 加载机制
    管理数据清洗的完整子图流程
    """
    
    @property
    def role_name(self) -> str:
        """Role name"""
        return "CleaningSubgraph"
    
    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"
    
    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "domain_tool_planner_prompt"
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = 'empty',
        temperature: float = 0.7,
        top_p: float = 0.95,
        max_completion_tokens: int = 4096,
        prompt_template_dir: Optional[str] = None,
        checkpointer: Optional[BaseCheckpointSaver] = None,
        store: Optional[BaseStore] = None
    ):
        """
        初始化 CleaningSubgraph
        
        Args:
            model_name: 模型名称
            base_url: LLM 服务器地址
            api_key: API 密钥
            temperature: 温度参数
            top_p: top_p 参数
            max_completion_tokens: 最大生成 token 数
            prompt_template_dir: prompt 模板目录
            checkpointer: 检查点保存器
            store: 状态存储
        """
        # 初始化 BaseAgent（不自动创建 LLM，因为我们需要从 state 动态获取配置）
        super().__init__(
            tools=[],
            model_name=None,  # 不在这里创建 LLM，从 state 动态获取
            base_url=None,
            api_key='empty',
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
            prompt_template_dir=prompt_template_dir,
            checkpointer=checkpointer,
            store=store
        )
        self.checkpointer = checkpointer
        self.store = store
        self.graph = None
    
    def create_llm_from_state(self, state: LoopAIState) -> Optional[ChatOpenAI]:
        """
        从 state 中获取配置并创建 LLM 实例
        
        Args:
            state: 当前状态
            
        Returns:
            ChatOpenAI 实例，如果配置不完整则返回 None
        """
        # 从 state.obtainer 中获取配置
        obtainer_state = state.get("obtainer", {})
        model_name = obtainer_state.get("model_path") or state.get("analyze_model_path")
        base_url = obtainer_state.get("base_url") or state.get("analyze_base_url")
        api_key = obtainer_state.get("api_key") or state.get("analyze_api_key")
        temperature = obtainer_state.get("temperature", self.temperature)
        top_p = obtainer_state.get("top_p", self.top_p)
        max_completion_tokens = obtainer_state.get("max_completion_tokens", self.max_completion_tokens)
        
        if not (model_name and base_url and api_key):
            return None
        
        # 使用 BaseAgent 的方式创建 LLM
        if base_url is None:
            logger.error(f'Undefined base_url in {self.role_name}')
            raise AssertionError(f'Undefined base_url in {self.role_name}')
        
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
            tags=[self.llm_tag]
        )
        
        return llm
    
    def get_prompt(self, prompt_type: str, prompt_name: str) -> str:
        """
        获取 prompt（使用 BaseAgent 的 prompt_loader）
        
        Args:
            prompt_type: prompt 类型
            prompt_name: prompt 名称
            
        Returns:
            prompt 字符串
        """
        return self.prompt_loader(prompt_type, prompt_name)
    
    def init_graph(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def __call__(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def build(self, **kwargs) -> StateGraph:
        """
        构建并编译清洗子图
        
        Returns:
            编译后的 StateGraph
        """
        builder = StateGraph(LoopAIState)
        
        # 添加节点
        builder.add_node("planner_node", planner_node)
        builder.add_node("process_node", process_node)
        
        # 设置入口点
        builder.set_entry_point("planner_node")
        
        # 连线: planner -> process -> END
        builder.add_edge("planner_node", "process_node")
        builder.set_finish_point("process_node")
        
        # 编译图
        compile_kwargs = {}
        if self.checkpointer:
            compile_kwargs["checkpointer"] = self.checkpointer
        if self.store:
            compile_kwargs["store"] = self.store
        compile_kwargs.update(kwargs)
        
        self.graph = builder.compile(**compile_kwargs)
        return self.graph


def create_cleaning_subgraph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    store: Optional[BaseStore] = None,
    **kwargs
) -> StateGraph:
    """
    创建清洗子图的便捷函数
    
    Args:
        checkpointer: 检查点保存器
        store: 状态存储
        **kwargs: 传递给 compile 的其他参数
    
    Returns:
        编译后的 StateGraph
    """
    subgraph = CleaningSubgraph(checkpointer=checkpointer, store=store)
    return subgraph.build(**kwargs)


def filter_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    映射后的过滤节点（用于 mapping_subgraph）
    
    这是一个占位符函数，用于 mapping_subgraph 中的 filter_node。
    实际的清洗逻辑在 CleaningSubgraph 中实现。
    
    Args:
        state: 当前状态
        store: 状态存储（可选）
    
    Returns:
        更新后的状态
    """
    logger.info("=== Mapping Filter Node: Starting ===")
    # 占位符实现：直接返回状态，不做任何处理
    # 如果需要映射后的过滤逻辑，可以在这里实现
    logger.info("=== Mapping Filter Node: Completed ===")
    return state
