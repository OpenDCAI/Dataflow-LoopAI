import os
from langgraph.config import get_stream_writer
from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger

logger = get_logger()


def _get_webcrawler_config(state: LoopAIState, key: str, default=None):
    """
    从 state['webcrawler'] 字典中获取配置值
    """
    webcrawler = state.get("webcrawler", {}) or {}
    return webcrawler.get(key, default)


def _set_webcrawler_config(state: LoopAIState, key: str, value):
    """
    设置 state['webcrawler'] 字典中的配置值
    """
    if "webcrawler" not in state or state["webcrawler"] is None:
        state["webcrawler"] = {}
    state["webcrawler"][key] = value

def start_node(state: LoopAIState) -> LoopAIState:
    """
    Start node for webcrawler agent
    Initialize configuration and validate required parameters
    """
    logger.info("WebCrawlerAgent: Starting task")
    
    # 确保 webcrawler 字典存在
    if "webcrawler" not in state or state["webcrawler"] is None:
        state["webcrawler"] = {}
    
    webcrawler = state["webcrawler"]
    
    # 调试：打印实际收到的 state
    logger.info(f"[DEBUG] start_node 收到的 state 包含的键: {list(state.keys())[:20]}")
    logger.info(f"[DEBUG] webcrawler 配置: {list(webcrawler.keys()) if webcrawler else '(空)'}")
    logger.info(f"[DEBUG] deepseek_api_key 是否存在: {'deepseek_api_key' in webcrawler}")
    
    writer = get_stream_writer()
    # 输出开始事件
    writer(StreamEvent(
        current=state['current'],
        message="WebCrawler 开始初始化配置",
        progress=0
    ).json())
    # === API 配置默认值 ===
    if not webcrawler.get("deepseek_api_base"):
        webcrawler["deepseek_api_base"] = "https://api.deepseek.com/v1"
    
    # === 模型配置默认值 ===
    if not webcrawler.get("model"):
        webcrawler["model"] = "deepseek-chat"
    
    if not webcrawler.get("temperature"):
        webcrawler["temperature"] = 0.7
    webcrawler["temperature"] = float(webcrawler["temperature"])
    
    # === 查询设置默认值 ===
    if not webcrawler.get("num_queries"):
        webcrawler["num_queries"] = 5
    webcrawler["num_queries"] = int(webcrawler["num_queries"])
    
    # === 爬取策略默认值 ===
    if not webcrawler.get("max_pages"):
        webcrawler["max_pages"] = 10000
    webcrawler["max_pages"] = int(webcrawler["max_pages"])
    
    if not webcrawler.get("crawl_depth"):
        webcrawler["crawl_depth"] = 3
    webcrawler["crawl_depth"] = int(webcrawler["crawl_depth"])
    
    if not webcrawler.get("max_links_per_page"):
        webcrawler["max_links_per_page"] = 5
    webcrawler["max_links_per_page"] = int(webcrawler["max_links_per_page"])
    
    if not webcrawler.get("concurrent_pages"):
        webcrawler["concurrent_pages"] = 3
    webcrawler["concurrent_pages"] = int(webcrawler["concurrent_pages"])
    
    # === 内容过滤默认值 ===
    if not webcrawler.get("min_text_length"):
        webcrawler["min_text_length"] = 500
    webcrawler["min_text_length"] = int(webcrawler["min_text_length"])
    
    if not webcrawler.get("min_code_length"):
        webcrawler["min_code_length"] = 50
    webcrawler["min_code_length"] = int(webcrawler["min_code_length"])
    
    if webcrawler.get("min_relevance_score") is None:
        webcrawler["min_relevance_score"] = 6
    webcrawler["min_relevance_score"] = int(webcrawler["min_relevance_score"])
    
    if not webcrawler.get("url_patterns"):
        webcrawler["url_patterns"] = None
    
    # === 运行时配置默认值 ===
    if not webcrawler.get("request_delay"):
        webcrawler["request_delay"] = 2.0
    webcrawler["request_delay"] = float(webcrawler["request_delay"])
    
    if not webcrawler.get("timeout"):
        webcrawler["timeout"] = 30
    webcrawler["timeout"] = int(webcrawler["timeout"])
    
    if not webcrawler.get("max_retries"):
        webcrawler["max_retries"] = 3
    webcrawler["max_retries"] = int(webcrawler["max_retries"])
    
    # === 输出配置默认值 ===
    if not state.get("output_dir"):
        state["output_dir"] = "./output"
    
    if not webcrawler.get("output_format"):
        webcrawler["output_format"] = "jsonl"
    
    if webcrawler.get("save_html") is None:
        webcrawler["save_html"] = False
    
    # === 数据集生成配置默认值 ===
    if not webcrawler.get("max_records_per_page"):
        webcrawler["max_records_per_page"] = 10
    webcrawler["max_records_per_page"] = int(webcrawler["max_records_per_page"])
    
    if not webcrawler.get("dataset_concurrent_limit"):
        webcrawler["dataset_concurrent_limit"] = 5
    webcrawler["dataset_concurrent_limit"] = int(webcrawler["dataset_concurrent_limit"])
    
    if not webcrawler.get("max_content_length"):
        webcrawler["max_content_length"] = 50000
    webcrawler["max_content_length"] = int(webcrawler["max_content_length"])
    
    if webcrawler.get("debug") is None:
        webcrawler["debug"] = False
    
    # 验证必要的API密钥
    if not webcrawler.get("deepseek_api_key"):
        logger.error("Missing DEEPSEEK_API_KEY")
        state["exception"] = "Missing required configuration: DEEPSEEK_API_KEY"
        writer(StreamEvent(
            current=state['current'],
            message="配置错误: 缺少 DEEPSEEK_API_KEY",
            data={"error": "Missing DEEPSEEK_API_KEY"}
        ).json())
        return state
    
    if not webcrawler.get("tavily_api_key"):
        logger.error("Missing TAVILY_API_KEY")
        state["exception"] = "Missing required configuration: TAVILY_API_KEY"
        writer(StreamEvent(
            current=state['current'],
            message="配置错误: 缺少 TAVILY_API_KEY",
            data={"error": "Missing TAVILY_API_KEY"}
        ).json())
        return state
    
    # 设置Tavily API Key到环境变量
    os.environ["TAVILY_API_KEY"] = webcrawler["tavily_api_key"]
    
    logger.info(f"WebCrawlerAgent: Configuration initialized - model: {webcrawler.get('model')}, "
               f"max_pages: {webcrawler.get('max_pages')}, depth: {webcrawler.get('crawl_depth')}, "
               f"concurrent: {webcrawler.get('concurrent_pages')}")
    
    # 输出配置完成事件
    writer(StreamEvent(
        current=state['current'],
        message=f"配置初始化完成 - 模型: {webcrawler.get('model')}, 最大页面数: {webcrawler.get('max_pages')}, 爬取深度: {webcrawler.get('crawl_depth')}",
        progress=1,
        data={
            "model": webcrawler.get("model"),
            "max_pages": webcrawler.get("max_pages"),
            "crawl_depth": webcrawler.get("crawl_depth"),
            "max_links_per_page": webcrawler.get("max_links_per_page"),
            "concurrent_pages": webcrawler.get("concurrent_pages"),
            "min_text_length": webcrawler.get("min_text_length"),
            "min_relevance_score": webcrawler.get("min_relevance_score"),
            "api_base": webcrawler.get("deepseek_api_base")
        }
    ).json())
    
    return state
