"""
Mapping Subgraph - 映射子图

将中间格式数据转换为目标格式的完整子图。

节点流程:
1. inquiry_node: 询问用户需要什么格式
2. list_formats_node: 显示所有格式详情 (非LLM)
3. preset_format_node: 处理预设格式选择 (非LLM)
4. custom_format_node: 生成自定义格式 (LLM)
5. confirmation_node: 确认格式
6. script_mapping_node: 脚本映射 (非LLM, 用于预设格式)
7. llm_mapping_node: LLM映射 (用于自定义格式)
8. summary_node: 总结结果
"""
import os
from typing import Optional, Callable

from langgraph.graph import StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

from .inquiry_node import inquiry_node
from .list_formats_node import list_formats_node
from .preset_format_node import preset_format_node
from .custom_format_node import custom_format_node
from .confirmation_node import confirmation_node
from .script_mapping_node import script_mapping_node
from .llm_mapping_node import llm_mapping_node
from .summary_node import summary_node
from loopai.agents.Obtainer.tools.format_mapping_tools import PRESET_FORMATS

logger = get_logger()


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
        builder.add_node("inquiry_node", self._wrap_node(inquiry_node))
        builder.add_node("list_formats_node", self._wrap_node(list_formats_node))
        builder.add_node("preset_format_node", self._wrap_node(preset_format_node))
        builder.add_node("custom_format_node", self._wrap_node(custom_format_node))
        builder.add_node("confirmation_node", self._wrap_node(confirmation_node))
        builder.add_node("script_mapping_node", self._wrap_node(script_mapping_node))
        builder.add_node("llm_mapping_node", self._wrap_node(llm_mapping_node))
        builder.add_node("summary_node", self._wrap_node(summary_node))
        builder.add_node("end_node", self._end_node)
        
        # 设置入口点
        builder.set_entry_point("inquiry_node")
        
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
        包装节点函数，注入 store
        """
        def wrapped(state: LoopAIState) -> LoopAIState:
            return node_func(state, store=self.store)
        return wrapped
    
    def _end_node(self, state: LoopAIState) -> LoopAIState:
        """结束节点"""
        logger.info("=== Mapping Subgraph: End Node ===")
        return state
    
    @staticmethod
    def _route_by_intent(state: LoopAIState) -> str:
        """
        根据用户意图路由
        """
        # 检查是否有异常
        if state.get("exception"):
            logger.error(f"Exception in state: {state.get('exception')}")
            return "end_node"
        
        intent = state.get("obtainer_mapping_user_intent", "")
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
        pending_format = state.get("obtainer_pending_format")
        if pending_format:
            return "confirmation_node"
        else:
            return "inquiry_node"
    
    @staticmethod
    def _check_format_generated(state: LoopAIState) -> str:
        """
        检查自定义格式是否生成成功
        """
        pending_format = state.get("obtainer_pending_format")
        if pending_format:
            return "confirmation_node"
        else:
            return "inquiry_node"
    
    @staticmethod
    def _route_by_confirmation(state: LoopAIState) -> str:
        """
        根据确认结果路由
        """
        result = state.get("obtainer_confirmation_result", "")
        
        if result == "confirmed":
            # 确认后，根据格式类型选择映射节点
            confirmed_format = state.get("obtainer_confirmed_format", {})
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
            intent = state.get("obtainer_mapping_user_intent", "")
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
    
    # 检查是否已经完成映射
    if state.get("obtainer_mapping_results"):
        logger.info("Mapping already completed")
        return state
    
    # 检查中间数据是否存在
    intermediate_path = state.get("obtainer_intermediate_data_path", "")
    if not intermediate_path or not os.path.exists(intermediate_path):
        logger.warning("No intermediate data found, skipping mapping")
        return state
    
    # 如果直接调用，提示用户使用子图
    logger.info("To use full mapping functionality, please use MappingSubgraph")
    return state

