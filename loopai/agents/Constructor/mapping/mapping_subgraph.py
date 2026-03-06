"""
Mapping Subgraph - 映射子图

将中间格式数据转换为目标格式的完整子图。

节点流程:
0. entry_check_node: 检查是否有默认格式，有则跳过用户交互
1. inquiry_node: 询问用户需要什么格式
2. list_formats_node: 显示所有格式详情 (非LLM)
3. preset_format_node: 处理预设格式选择 (非LLM)
4. custom_format_node: 生成自定义格式 (LLM)
5. confirmation_node: 确认格式
6. script_mapping_node: 脚本映射 (非LLM, 用于预设格式)
7. llm_mapping_node: LLM映射 (用于自定义格式)
8. summary_node: 总结结果

State 参数:
- constructor_default_mapping_format: 默认映射格式ID（如 "alpaca"），设置后跳过用户交互
  如果为空或不设置，则走用户交互流程
"""
import os
from typing import Optional, Callable

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger

from .inquiry_node import inquiry_node
from .list_formats_node import list_formats_node
from .preset_format_node import preset_format_node
from .custom_format_node import custom_format_node
from .confirmation_node import confirmation_node
from .script_mapping_node import script_mapping_node
from .llm_mapping_node import llm_mapping_node
from .summary_node import summary_node
from loopai.agents.Constructor.tools.format_mapping_tools import PRESET_FORMATS

logger = get_logger()

# 各节点在映射子图中的进度区间 (start, end)，用于进度条
MAPPING_NODE_PROGRESS = {
    "inquiry_node": (0.08, 0.12),
    "list_formats_node": (0.12, 0.16),
    "preset_format_node": (0.16, 0.20),
    "custom_format_node": (0.20, 0.24),
    "confirmation_node": (0.24, 0.28),
    "script_mapping_node": (0.28, 0.55),
    "llm_mapping_node": (0.55, 0.82),
    "summary_node": (0.82, 0.95),
}


def _emit_mapping_progress(current: str, message: str, progress: Optional[float] = None, data: Optional[dict] = None) -> None:
    """发送映射子图进度事件"""
    try:
        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(current=current, message=message, progress=progress, data=data).json())
    except Exception as e:
        logger.debug(f"Could not send mapping progress: {e}")


class MappingSubgraph:
    """
    Mapping Subgraph 类
    
    管理数据格式映射的完整子图流程
    """
    
    def __init__(
        self,
        checkpointer: Optional[BaseCheckpointSaver] = None,
        store: Optional[BaseStore] = None
    ):
        """
        初始化 MappingSubgraph
        
        Args:
            checkpointer: 检查点保存器
            store: 状态存储
        """
        self.checkpointer = checkpointer
        self.store = store
        self.graph = None
    
    def build(self, **kwargs) -> StateGraph:
        """
        构建并编译子图
        
        Returns:
            编译后的 StateGraph
        """
        builder = StateGraph(LoopAIState)
        
        # 添加节点
        builder.add_node("entry_check_node", self._entry_check_node)  # 入口检查节点
        builder.add_node("inquiry_node", self._wrap_node(inquiry_node))
        builder.add_node("list_formats_node", self._wrap_node(list_formats_node))
        builder.add_node("preset_format_node", self._wrap_node(preset_format_node))
        builder.add_node("custom_format_node", self._wrap_node(custom_format_node))
        builder.add_node("confirmation_node", self._wrap_node(confirmation_node))
        builder.add_node("script_mapping_node", self._wrap_node(script_mapping_node))
        builder.add_node("llm_mapping_node", self._wrap_node(llm_mapping_node))
        builder.add_node("summary_node", self._wrap_node(summary_node))
        builder.add_node("end_node", self._end_node)
        
        # 设置入口点为 entry_check_node
        builder.set_entry_point("entry_check_node")
        
        # entry_check_node -> 根据是否有默认格式路由
        builder.add_conditional_edges(
            "entry_check_node",
            self._route_after_entry_check,
            {
                "script_mapping_node": "script_mapping_node",  # 自动模式：预设格式
                "llm_mapping_node": "llm_mapping_node",  # 自动模式：自定义格式
                "inquiry_node": "inquiry_node",  # 用户交互模式
                "end_node": "end_node",  # 出错时退出
            }
        )
        
        # 添加条件边: inquiry_node -> 根据意图路由
        builder.add_conditional_edges(
            "inquiry_node",
            self._route_by_intent,
            {
                "list_formats_node": "list_formats_node",
                "preset_format_node": "preset_format_node",
                "custom_format_node": "custom_format_node",
                "inquiry_node": "inquiry_node",  # 等待用户输入后重新进入
                "end_node": "end_node",  # 出错时退出
            }
        )
        
        # list_formats_node -> inquiry_node (返回继续选择)
        builder.add_edge("list_formats_node", "inquiry_node")
        
        # preset_format_node -> confirmation_node
        builder.add_conditional_edges(
            "preset_format_node",
            self._check_format_selected,
            {
                "confirmation_node": "confirmation_node",
                "inquiry_node": "inquiry_node",  # 选择失败，重新选择
            }
        )
        
        # custom_format_node -> confirmation_node
        builder.add_conditional_edges(
            "custom_format_node",
            self._check_format_generated,
            {
                "confirmation_node": "confirmation_node",
                "inquiry_node": "inquiry_node",  # 生成失败，重新选择
            }
        )
        
        # confirmation_node -> 根据确认结果路由
        builder.add_conditional_edges(
            "confirmation_node",
            self._route_by_confirmation,
            {
                "script_mapping_node": "script_mapping_node",
                "llm_mapping_node": "llm_mapping_node",
                "custom_format_node": "custom_format_node",  # 修改
                "inquiry_node": "inquiry_node",  # 重选
                "preset_format_node": "preset_format_node",  # 用户选择了其他预设格式
                "confirmation_node": "confirmation_node",  # 等待用户输入
            }
        )
        
        # 映射节点 -> summary_node
        builder.add_edge("script_mapping_node", "summary_node")
        builder.add_edge("llm_mapping_node", "summary_node")
        
        # summary_node -> end_node
        builder.add_edge("summary_node", "end_node")
        
        # 设置结束点
        builder.set_finish_point("end_node")
        
        # 编译图
        compile_kwargs = {}
        if self.checkpointer:
            compile_kwargs["checkpointer"] = self.checkpointer
        if self.store:
            compile_kwargs["store"] = self.store
        compile_kwargs.update(kwargs)
        
        self.graph = builder.compile(**compile_kwargs)
        return self.graph
    
    def _wrap_node(self, node_func: Callable) -> Callable:
        """
        包装节点函数，注入 store，并在执行前后发送进度事件
        """
        node_name = node_func.__name__
        progress_range = MAPPING_NODE_PROGRESS.get(node_name, (0.0, 1.0))

        def wrapped(state: LoopAIState) -> LoopAIState:
            current = state.get("current", "ConstructorAgent.mapping_subgraph")
            progress_start, progress_end = progress_range
            _emit_mapping_progress(
                current,
                f"Constructor: 格式映射 - {node_name} 开始",
                progress=progress_start,
                data={"node": node_name, "phase": "mapping"},
            )
            result = node_func(state, store=self.store)
            _emit_mapping_progress(
                current,
                f"Constructor: 格式映射 - {node_name} 完成",
                progress=progress_end,
                data={"node": node_name, "phase": "mapping"},
            )
            return result
        return wrapped
    
    def _end_node(self, state: LoopAIState) -> LoopAIState:
        """结束节点"""
        logger.info("=== Mapping Subgraph: End Node ===")
        current = state.get("current", "ConstructorAgent.mapping_subgraph")
        _emit_mapping_progress(current, "Constructor: 格式映射 - 子图结束", progress=1.0, data={"phase": "mapping"})
        return state
    
    def _entry_check_node(self, state: LoopAIState) -> LoopAIState:
        """
        入口检查节点 - 检查是否有默认格式设置
        
        如果设置了 constructor.default_mapping_format，则跳过用户交互直接使用该格式
        """
        logger.info("=== Mapping Subgraph: Entry Check Node ===")
        current = state.get("current", "ConstructorAgent.mapping_subgraph")
        _emit_mapping_progress(current, "Constructor: 格式映射 - 入口检查", progress=0.02, data={"phase": "mapping"})

        # 确保 constructor 字典存在
        if "constructor" not in state:
            state["constructor"] = {}
        constructor_state = state["constructor"]
        
        # 检查是否有默认格式设置；容错去除空格并统一小写
        raw_default_format = constructor_state.get("default_mapping_format", "")
        default_format = ""
        if raw_default_format:
            default_format = str(raw_default_format).strip().lower()
            state["constructor"]["default_mapping_format"] = default_format  # 回写标准化值
        logger.info(
            f"Mapping entry check: raw_default='{raw_default_format}', "
            f"normalized='{default_format}', "
            f"confirmed_format_exists={bool(constructor_state.get('confirmed_format'))}, "
            f"category='{constructor_state.get('category', '')}'"
        )
        
        if default_format and default_format in PRESET_FORMATS:
            logger.info(f"Default mapping format specified: {default_format}, skipping user interaction")
            
            # 设置格式选择相关的状态
            format_info = PRESET_FORMATS[default_format]
            state["constructor"]["confirmed_format"] = {
                "format_id": default_format,
                "format_name": format_info.get("name", ""),
                "description": format_info.get("description", ""),
                "schema": format_info.get("schema", {}),
                "example": format_info.get("example", {}),
                "is_preset": True
            }
            state["constructor"]["confirmation_result"] = "confirmed"
            state["constructor"]["mapping_auto_mode"] = True  # 标记为自动模式
            
            logger.info(f"Auto-confirmed format: {default_format}")
        elif default_format:
            # 设置了默认格式但不在预设列表中
            logger.warning(f"Default format '{default_format}' not found in preset formats, falling back to user interaction")
            state["constructor"]["mapping_auto_mode"] = False
        else:
            # 没有设置默认格式，走用户交互流程
            logger.info("No default format specified, proceeding with user interaction")
            state["constructor"]["mapping_auto_mode"] = False

        _emit_mapping_progress(current, "Constructor: 格式映射 - 入口检查完成", progress=0.06, data={"phase": "mapping"})
        return state
    
    @staticmethod
    def _route_after_entry_check(state: LoopAIState) -> str:
        """
        入口检查后的路由
        
        Returns:
            "script_mapping_node" 如果已确认默认格式
            "inquiry_node" 如果需要用户交互
            "end_node" 如果出错
        """
        # 检查是否有异常
        if state.get("exception"):
            logger.error(f"Exception in state: {state.get('exception')}")
            return "end_node"
        
        constructor_state = state.get("constructor", {})
        
        # 检查是否已经确认了格式（自动模式）
        if constructor_state.get("mapping_auto_mode") and constructor_state.get("confirmed_format"):
            confirmed_format = constructor_state.get("confirmed_format", {})
            format_id = confirmed_format.get("format_id", "")
            
            if format_id in PRESET_FORMATS:
                logger.info(f"Auto mode: routing to script_mapping_node for format: {format_id}")
                return "script_mapping_node"
            else:
                logger.info(f"Auto mode: routing to llm_mapping_node for custom format")
                return "llm_mapping_node"
        
        # 否则进入用户交互流程
        logger.info("Routing to inquiry_node for user interaction")
        return "inquiry_node"
    
    @staticmethod
    def _route_by_intent(state: LoopAIState) -> str:
        """
        根据用户意图路由
        """
        # 检查是否有异常
        if state.get("exception"):
            logger.error(f"Exception in state: {state.get('exception')}")
            return "end_node"
        
        constructor_state = state.get("constructor", {})
        intent = constructor_state.get("mapping_user_intent", "")
        logger.info(f"Routing by intent: {intent}")
        
        if intent == "list_formats":
            return "list_formats_node"
        elif intent == "preset_format":
            return "preset_format_node"
        elif intent == "custom_format":
            return "custom_format_node"
        elif intent == "unclear":
            # 意图不明确，返回询问节点
            logger.info("Intent unclear, returning to inquiry_node")
            return "inquiry_node"
        else:
            # 没有意图，说明在等待用户输入
            logger.info("No intent set, returning to inquiry_node")
            return "inquiry_node"
    
    @staticmethod
    def _check_format_selected(state: LoopAIState) -> str:
        """
        检查预设格式是否选择成功
        """
        constructor_state = state.get("constructor", {})
        pending_format = constructor_state.get("pending_format")
        if pending_format:
            return "confirmation_node"
        else:
            return "inquiry_node"
    
    @staticmethod
    def _check_format_generated(state: LoopAIState) -> str:
        """
        检查自定义格式是否生成成功
        """
        constructor_state = state.get("constructor", {})
        pending_format = constructor_state.get("pending_format")
        if pending_format:
            return "confirmation_node"
        else:
            return "inquiry_node"
    
    @staticmethod
    def _route_by_confirmation(state: LoopAIState) -> str:
        """
        根据确认结果路由
        """
        constructor_state = state.get("constructor", {})
        result = constructor_state.get("confirmation_result", "")
        
        if result == "confirmed":
            # 确认后，根据格式类型选择映射节点
            confirmed_format = constructor_state.get("confirmed_format", {})
            format_id = confirmed_format.get("format_id", "")
            is_preset = confirmed_format.get("is_preset", False)
            
            # 预设格式使用脚本映射，自定义格式使用 LLM 映射
            if is_preset and format_id in PRESET_FORMATS:
                logger.info(f"Routing to script_mapping_node for preset format: {format_id}")
                return "script_mapping_node"
            else:
                logger.info(f"Routing to llm_mapping_node for custom format")
                return "llm_mapping_node"
        
        elif result == "modify":
            # 修改，返回自定义格式节点
            return "custom_format_node"
        
        elif result == "restart":
            # 重选，检查是否已经选择了新的预设格式
            intent = constructor_state.get("mapping_user_intent", "")
            if intent == "preset_format":
                # 用户在确认阶段选择了其他预设格式，直接路由到 preset_format_node
                logger.info(f"User selected different preset format, routing to preset_format_node")
                return "preset_format_node"
            else:
                # 返回问询节点重新开始
                return "inquiry_node"
        
        else:
            # 没有结果，继续等待确认
            return "confirmation_node"
    
    def __call__(self, **kwargs):
        """
        构建并返回图
        """
        return self.build(**kwargs)


def create_mapping_subgraph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    store: Optional[BaseStore] = None,
    **kwargs
) -> StateGraph:
    """
    创建映射子图的便捷函数
    
    Args:
        checkpointer: 检查点保存器
        store: 状态存储
        **kwargs: 传递给 compile 的其他参数
    
    Returns:
        编译后的 StateGraph
    """
    subgraph = MappingSubgraph(checkpointer=checkpointer, store=store)
    return subgraph.build(**kwargs)


# 为了向后兼容，提供一个简单的 mapping_node 函数
def mapping_node(state: LoopAIState) -> LoopAIState:
    """
    向后兼容的 mapping_node 函数
    
    注意: 这个函数只是一个入口点，实际的映射逻辑在子图中实现。
    在 ObtainerAgent 中应该使用 MappingSubgraph 作为子图。
    
    如果直接调用这个函数（不在子图上下文中），它会检查状态并返回。
    """
    logger.warning("mapping_node called directly. Consider using MappingSubgraph for full functionality.")
    
    constructor_state = state.get("constructor", {})
    
    # 检查是否已经完成映射
    if constructor_state.get("mapping_results"):
        logger.info("Mapping already completed")
        return state
    
    # 检查中间数据是否存在（使用嵌套结构）
    intermediate_path = constructor_state.get("intermediate_data_path", "")
    if not intermediate_path or not os.path.exists(intermediate_path):
        logger.warning("No intermediate data found, skipping mapping")
        return state
    
    # 如果直接调用，提示用户使用子图
    logger.info("To use full mapping functionality, please use MappingSubgraph")
    return state

