"""
Summary Node - 总结节点

功能:
1. 汇总映射执行结果
2. 生成用户友好的报告
3. 添加 AI 消息到 messages
4. 将总结写入 store (memory)
"""
import json
from typing import Dict, Any

from langchain_core.messages import AIMessage
from langgraph.store.base import BaseStore

from loopai.schema.states import LoopAIState
from loopai.logger import get_logger

logger = get_logger()


def summary_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    总结节点 - 汇总映射结果并生成报告
    
    Args:
        state: 当前状态
        store: LangGraph store
    
    Returns:
        更新后的状态，包含总结消息
    """
    logger.info("=== Summary Node: Starting ===")
    
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    constructor_state = state.get("constructor", {})
    
    # 获取映射结果
    mapping_results = constructor_state.get("mapping_results", {})
    confirmed_format = constructor_state.get("confirmed_format", {})
    exception = state.get("exception")
    
    # 构建总结消息
    if exception:
        summary_message = _build_error_summary(exception)
    elif mapping_results:
        summary_message = _build_success_summary(mapping_results, confirmed_format)
    else:
        summary_message = _build_no_result_summary()
    
    # 添加 AI 消息
    if "messages" not in state:
        state["messages"] = []
    state["messages"].append(AIMessage(content=summary_message))
    
    # 保存到 store
    _save_to_store(state, store, mapping_results, summary_message)

    # 清理本轮映射的临时交互状态，保留 auto/default 配置供下轮复用
    _reset_mapping_runtime_state(state)
    
    logger.info("=== Summary Node: Completed ===")
    return state


def _build_success_summary(results: Dict[str, Any], format_config: Dict[str, Any]) -> str:
    """构建成功总结"""
    total = results.get("total_records", 0)
    mapped = results.get("mapped_records", 0)
    failed = results.get("failed_records", 0)
    output_file = results.get("output_file", "")
    output_dir = results.get("output_dir", "")
    mapping_type = results.get("mapping_type", "unknown")
    format_id = results.get("format_id", format_config.get("format_id", ""))
    format_name = format_config.get("format_name", format_id)
    
    # 计算成功率
    success_rate = (mapped / total * 100) if total > 0 else 0
    
    # 映射类型描述
    type_desc = "脚本映射" if mapping_type == "script" else "LLM映射"
    
    summary = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 数据映射完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 映射统计:
   • 总记录数: {total}
   • 成功映射: {mapped}
   • 映射失败: {failed}
   • 成功率: {success_rate:.1f}%

📋 格式信息:
   • 目标格式: {format_name}
   • 格式ID: {format_id}
   • 映射方式: {type_desc}

📁 输出信息:
   • 输出目录: {output_dir}
   • 输出文件: {output_file}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    return summary.strip()


def _build_error_summary(exception: str) -> str:
    """构建错误总结"""
    summary = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❌ 数据映射失败
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

错误信息:
{exception}

建议:
   • 检查中间格式数据是否存在
   • 确认格式配置是否正确
   • 查看日志获取更多详情

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    return summary.strip()


def _build_no_result_summary() -> str:
    """构建无结果总结"""
    summary = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ 数据映射未执行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

未找到映射结果。可能的原因:
   • 中间格式数据为空
   • 格式选择未完成
   • 映射过程被中断

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    return summary.strip()


def _save_to_store(state: LoopAIState, store: BaseStore, results: Dict[str, Any], summary: str):
    """保存总结到 store"""
    if store is None:
        return
    
    try:
        import datetime
        thread_id = state.get("task_id", "default")
        
        data = {
            "event_type": "mapping_summary",
            "timestamp": datetime.datetime.now().isoformat(),
            "summary": summary,
            "results": results,
            "has_exception": bool(state.get("exception"))
        }
        
        namespace = ("mapping", thread_id)
        store.put(namespace, "mapping_summary", data)
        logger.debug(f"Saved mapping summary to store")
    except Exception as e:
        logger.warning(f"Failed to save to store: {e}")


def _reset_mapping_runtime_state(state: LoopAIState):
    """
    清理映射流程的临时状态，方便下一轮循环重新触发映射。
    保留 automode/default 配置；仅清理确认/交互相关字段。
    """
    # 确保 constructor 字典存在
    if "constructor" not in state:
        state["constructor"] = {}
    
    transient_keys = [
        "confirmed_format",
        "confirmation_result",
        "pending_format",
        "mapping_user_intent",
        "mapping_selected_format_id",
        "mapping_custom_description",
    ]
    for key in transient_keys:
        if key in state.get("constructor", {}):
            state["constructor"][key] = None if key.endswith("_format") else ""

