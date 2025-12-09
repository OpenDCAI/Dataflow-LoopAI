import os
from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger

logger = get_logger()


def start_node(state: LoopAIState, agent) -> LoopAIState:
    """
    Start node for webcrawler agent
    Initialize configuration and validate required parameters
    """
    logger.info("WebCrawlerAgent: Starting task")
    
    # 调试：打印实际收到的 state
    logger.info(f"[DEBUG] start_node 收到的 state 包含的键: {list(state.keys())[:20]}")
    logger.info(f"[DEBUG] webcrawler_deepseek_api_key 是否存在: {'webcrawler_deepseek_api_key' in state}")
    logger.info(f"[DEBUG] webcrawler_deepseek_api_key 的值: {state.get('webcrawler_deepseek_api_key', '(不存在)')[:20] if state.get('webcrawler_deepseek_api_key') else '(空或不存在)'}")
    
    writer = get_stream_writer()
    
    # 输出开始事件
    writer(StreamEvent(
        current="start_node",
        message="WebCrawler 开始初始化配置"
    ).json())
    
    # 确保必要配置存在（只在真正缺失时从环境变量读取，不覆盖已有配置）
    # 注意：run_webcrawler.py 已经设置了这些值，这里只是 fallback
    pass  # API keys 应该已经在 initial_state 里设置好了
    
    if not state.get("webcrawler_deepseek_api_base"):
        state["webcrawler_deepseek_api_base"] = "https://api.deepseek.com/v1"
    
    if not state.get("webcrawler_model"):
        state["webcrawler_model"] = "deepseek-chat"
    
    # === 生成查询配置 ===
    if not state.get("num_queries"):
        state["num_queries"] = 5
    
    # === 爬取策略 ===
    if not state.get("webcrawler_max_pages"):
        state["webcrawler_max_pages"] = 10000
    
    if not state.get("crawl_depth"):
        state["crawl_depth"] = 3
    
    if not state.get("max_links_per_page"):
        state["max_links_per_page"] = 5
    
    if not state.get("concurrent_pages"):
        state["concurrent_pages"] = 3
    
    # === 内容过滤 ===
    if not state.get("min_text_length"):
        state["min_text_length"] = 500
    
    if not state.get("min_code_length"):
        state["min_code_length"] = 50
    
    if not state.get("min_relevance_score"):
        state["min_relevance_score"] = 6
    
    if not state.get("url_patterns"):
        state["url_patterns"] = None
    
    # === 运行时配置 ===
    if not state.get("request_delay"):
        state["request_delay"] = 2.0
    
    if not state.get("timeout"):
        state["timeout"] = 30
    
    if not state.get("max_retries"):
        state["max_retries"] = 3
    
    # === 输出配置 ===
    if not state.get("output_dir"):
        state["output_dir"] = "./output"
    
    if not state.get("output_format"):
        state["output_format"] = "jsonl"
    
    if not state.get("save_html"):
        state["save_html"] = False
    
    # 验证必要的API密钥
    if not state.get("webcrawler_deepseek_api_key"):
        logger.error("Missing DEEPSEEK_API_KEY")
        state["exception"] = "Missing required configuration: DEEPSEEK_API_KEY"
        writer(StreamEvent(
            current="start_node",
            message="配置错误: 缺少 DEEPSEEK_API_KEY",
            data={"error": "Missing DEEPSEEK_API_KEY"}
        ).json())
        return state
    
    if not state.get("webcrawler_tavily_api_key"):
        logger.error("Missing TAVILY_API_KEY")
        state["exception"] = "Missing required configuration: TAVILY_API_KEY"
        writer(StreamEvent(
            current="start_node",
            message="配置错误: 缺少 TAVILY_API_KEY",
            data={"error": "Missing TAVILY_API_KEY"}
        ).json())
        return state
    
    # 设置Tavily API Key到环境变量
    if state.get("webcrawler_tavily_api_key"):
        os.environ["TAVILY_API_KEY"] = state["webcrawler_tavily_api_key"]
    
    logger.info(f"WebCrawlerAgent: Configuration initialized - model: {state.get('webcrawler_model')}, "
               f"max_pages: {state.get('webcrawler_max_pages')}, depth: {state.get('crawl_depth')}, "
               f"concurrent: {state.get('concurrent_pages')}")
    
    # 输出配置完成事件
    writer(StreamEvent(
        current="start_node",
        message=f"配置初始化完成 - 模型: {state.get('webcrawler_model')}, 最大页面数: {state.get('webcrawler_max_pages')}, 爬取深度: {state.get('crawl_depth')}",
        data={
            "model": state.get("webcrawler_model"),
            "max_pages": state.get("webcrawler_max_pages"),
            "crawl_depth": state.get("crawl_depth"),
            "max_links_per_page": state.get("max_links_per_page"),
            "concurrent_pages": state.get("concurrent_pages"),
            "min_text_length": state.get("min_text_length"),
            "min_relevance_score": state.get("min_relevance_score"),
            "api_base": state.get("webcrawler_deepseek_api_base")
        }
    ).json())
    
    return state