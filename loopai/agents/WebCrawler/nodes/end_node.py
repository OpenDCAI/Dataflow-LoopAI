from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from langchain_core.messages import AIMessage

logger = get_logger()


def end_node(state: LoopAIState) -> LoopAIState:
    """
    End node for webcrawler agent
    Summarize results and return to parent graph
    """
    logger.info("WebCrawlerAgent: Task completed, returning to parent graph")
    
    writer = get_stream_writer()
    
    # 输出结束开始事件
    writer(StreamEvent(
        current="end_node",
        message="开始生成任务摘要"
    ).json())
    
    # 生成摘要
    summary_parts = []
    
    if state.get("exception"):
        summary_parts.append(f"执行过程中出现错误: {state.get('exception')}")
    else:
        result = state.get("webcrawler_output_result", {})
        total_pages = result.get("total_pages", 0)
        
        if total_pages > 0:
            summary_parts.append(f"成功爬取 {total_pages} 个网页")
            
            overall_summary = result.get("overall_summary", {})
            if overall_summary.get("overview"):
                overview = overall_summary['overview']
                preview = overview[:200] + "..." if len(overview) > 200 else overview
                summary_parts.append(f"整体概述: {preview}")
            
            if overall_summary.get("key_findings"):
                findings = overall_summary["key_findings"][:3]
                summary_parts.append(f"关键发现: {', '.join(findings)}")
            
            if overall_summary.get("recommendations"):
                recommendations = overall_summary["recommendations"][:2]
                summary_parts.append(f"建议: {', '.join(recommendations)}")
            
            output_dir = state.get("webcrawler_output_dir", "")
            if output_dir:
                summary_parts.append(f"输出目录: {output_dir}")
        else:
            summary_parts.append("未能成功爬取有效数据")
    
    # 创建摘要消息
    if summary_parts:
        summary_text = "网页爬取任务执行完成:\n" + "\n".join(summary_parts)
    else:
        summary_text = "网页爬取任务执行完成,但未找到相关数据。"
    
    # 添加摘要到messages
    if "messages" not in state:
        state["messages"] = []
    
    state["messages"].append(AIMessage(content=summary_text))
    logger.info(f"WebCrawlerAgent: Added summary to messages: {summary_text[:100]}...")
    state["next_to"] = "query_node"
    
    # 输出任务完成事件
    result = state.get("webcrawler_output_result", {})
    writer(StreamEvent(
        current="end_node",
        message=f"WebCrawler 任务完成 - 共爬取 {result.get('total_pages', 0)} 个网页",
        data={
            "summary": summary_text,
            "total_pages": result.get("total_pages", 0),
            "output_dir": state.get("webcrawler_output_dir", ""),
            "has_error": bool(state.get("exception"))
        }
    ).json())
    
    return state