import os
import asyncio
from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from ..utils import CrawlOrchestrator

logger = get_logger()


def crawl_node(state: LoopAIState) -> LoopAIState:
    """
    Main crawl node - executes web crawling task
    """
    logger.info("WebCrawlerAgent: Executing crawl task")
    
    writer = get_stream_writer()
    
    # 输出爬取开始事件
    writer(StreamEvent(
        current="crawl_node",
        message="开始执行网页爬取任务"
    ).json())
    
    # 如果有异常,跳过执行
    if state.get("exception"):
        logger.error(f"Skipping crawl due to previous exception: {state['exception']}")
        writer(StreamEvent(
            current="crawl_node",
            message=f"因前序异常跳过爬取: {state['exception']}",
            data={"error": state['exception']}
        ).json())
        return state
    
    # 获取任务描述
    task = ""
    if state.get("messages") and len(state["messages"]) > 0:
        last_message = state["messages"][-1]
        if hasattr(last_message, "content"):
            task = last_message.content
        elif isinstance(last_message, dict):
            task = last_message.get("content", "")
    
    if not task:
        task = state.get("automated_query", "")
    
    if not task:
        logger.warning("No task found in state, using default task")
        task = "搜索相关技术内容"
    
    writer(StreamEvent(
        current="crawl_node",
        message=f"爬取任务: {task[:100]}{'...' if len(task) > 100 else ''}",
        data={"task": task}
    ).json())
    
    # 创建爬取编排器
    try:
        orchestrator = CrawlOrchestrator(
            deepseek_api_key=state.get("webcrawler_deepseek_api_key", ""),
            tavily_api_key=state.get("webcrawler_tavily_api_key", ""),
            deepseek_api_base=state.get("webcrawler_deepseek_api_base", "https://api.deepseek.com/v1"),
            model=state.get("webcrawler_model", "deepseek-chat"),
            max_pages=state.get("webcrawler_max_pages", 10000),
            output_dir=os.path.join(state.get("output_dir", "./output"), "webcrawler_output"),
            stream_callback=writer,  # 传递 writer 作为回调
            # 爬取策略参数
            num_queries=state.get("webcrawler_num_queries", 5),
            crawl_depth=state.get("webcrawler_crawl_depth", 3),
            max_links_per_page=state.get("webcrawler_max_links_per_page", 5),
            concurrent_pages=state.get("webcrawler_concurrent_pages", 3),
            # 内容过滤参数
            min_text_length=state.get("webcrawler_min_text_length", 500),
            min_code_length=state.get("webcrawler_min_code_length", 50),
            min_relevance_score=state.get("webcrawler_min_relevance_score", 6),
            url_patterns=state.get("webcrawler_url_patterns", None),
            # 运行时配置参数
            request_delay=state.get("webcrawler_request_delay", 2.0),
            timeout=state.get("webcrawler_timeout", 30),
            max_retries=state.get("webcrawler_max_retries", 3),
            # 输出配置参数
            output_format=state.get("webcrawler_output_format", "jsonl"),
            save_html=state.get("webcrawler_save_html", False)
        )
        
        # 执行爬取任务
        logger.info(f"Starting crawl task: {task[:100]}...")
        result = asyncio.run(orchestrator.run(task))
        
        # 保存结果到state
        state["webcrawler_output_result"] = result
        state["webcrawler_output_run_id"] = result.get("run_id")
        state["webcrawler_output_dir"] = str(orchestrator.run_dir)
        
        logger.info(f"WebCrawlerAgent: Crawl completed successfully - "
                   f"pages: {result.get('total_pages', 0)}, "
                   f"output: {state['webcrawler_output_dir']}")
        
        # 输出爬取完成事件
        writer(StreamEvent(
            current="crawl_node",
            message=f"爬取任务完成 - 成功爬取 {result.get('total_pages', 0)} 个网页",
            data={
                "total_pages": result.get('total_pages', 0),
                "output_dir": state['webcrawler_output_dir'],
                "run_id": result.get("run_id")
            }
        ).json())
        
    except Exception as e:
        logger.error(f"WebCrawlerAgent: Crawl failed with error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        state["exception"] = str(e)
        state["webcrawler_output_result"] = {"error": str(e)}
        
        # 输出爬取失败事件
        writer(StreamEvent(
            current="crawl_node",
            message=f"爬取任务失败: {str(e)}",
            data={"error": str(e)}
        ).json())
    
    return state