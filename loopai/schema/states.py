from typing import TypedDict, Any, List, Dict, Annotated, Optional, Union
from langgraph.graph import MessagesState
from pydantic import BaseModel, Field


# ==========================================
# 1. 核心工具函数 (Reducers)
# ==========================================

def replace_value(current, new):
    """保留原有的替换逻辑：如果不为None则替换"""
    return new if new is not None else current


def merge_dict(current: Dict[str, Any], new: Union[Dict[str, Any], BaseModel]) -> Dict[str, Any]:
    """
    【修改】深合并逻辑：
    1. 支持接收 Pydantic Model，自动转换为字典。
    2. 递归合并嵌套字典 (Deep Merge)。
    3. 对于非字典类型（如列表、字符串、数字），保持“替换”逻辑。
    """
    if current is None:
        current = {}

    # 1. Pydantic 处理：转为字典，过滤未设置的值
    if isinstance(new, BaseModel):
        new = new.model_dump(exclude_unset=True)

    if new is None:
        return current

    # 2. 创建当前状态的浅拷贝
    merged = current.copy()

    # 3. 遍历新数据的键值对
    for key, value in new.items():
        # 如果 key 存在于当前状态，且【两者都是字典】，则递归合并
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = merge_dict(merged[key], value)
        else:
            # 否则直接覆盖
            merged[key] = value

    return merged


# ==========================================
# 2. 定义 Obtainer 模块的状态类 (Pydantic)
# ==========================================


class ObtainerState(BaseModel):
    """
    Obtainer 模块的专用状态管理类
    增加了 ui_type 和 title 供前端渲染使用
    """
    # --- Agent Config (Agent配置) ---
    model_path: Optional[str] = Field(
        default=None,
        title="模型路径",
        description="大模型路径或名称 (e.g. gpt-4, /local/model)",
        json_schema_extra={"ui_type": "text", "ui_group": "Agent配置"}
    )
    base_url: Optional[str] = Field(
        default=None,
        title="API Base URL",
        description="模型 API 的 Base URL",
        json_schema_extra={"ui_type": "text", "ui_group": "Agent配置"}
    )
    api_key: Optional[str] = Field(
        default=None,
        title="API Key",
        description="模型 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "Agent配置"}
    )
    temperature: float = Field(
        default=0.7,
        title="采样温度",
        description="模型采样温度 (0.0 - 1.0)",
        ge=0.0, le=1.0,  # Pydantic 校验范围
        json_schema_extra={"ui_type": "slider",
                           "step": 0.1, "max": 1, "ui_group": "Agent配置"}
    )

    # --- Search Engine & Crawling (搜索与爬取) ---
    search_engine: str = Field(
        default="tavily",
        title="搜索引擎",
        description="使用的搜索引擎",
        json_schema_extra={
            "ui_type": "select",
            "options": ["tavily", "google", "bing", "duckduckgo"],
            "ui_group": "搜索设置"
        }
    )
    tavily_api_key: str = Field(
        default="",
        title="Tavily Key",
        description="Tavily 搜索引擎的 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "搜索设置"}
    )
    max_urls: int = Field(
        default=10,
        title="最大URL数",
        description="单次搜索最大处理 URL 数量",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )
    max_depth: int = Field(
        default=2,
        title="爬取深度",
        description="爬虫最大深度",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )
    concurrent_limit: int = Field(
        default=5,
        title="并发限制",
        description="并发请求限制",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )
    topk_urls: int = Field(
        default=3,
        title="Top-K URL",
        description="保留最相关的 URL 数量",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )
    url_timeout: int = Field(
        default=30,
        title="超时时间",
        description="URL 请求超时时间(秒)",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )
    recursion_limit: int = Field(
        default=5,
        title="重试次数",
        description="递归/重试限制次数",
        json_schema_extra={"ui_type": "number", "ui_group": "搜索设置"}
    )

    # --- Task Logic (任务逻辑 - 通常由系统生成，前端设为只读或JSON视图) ---
    intent_type: str = Field(
        default="",
        title="意图类型",
        description="用户意图分类结果",
        json_schema_extra={"ui_type": "text",
                           "readOnly": True, "ui_group": "任务状态"}
    )
    normalized_query: str = Field(
        default="",
        title="标准化查询",
        description="标准化后的查询语句",
        json_schema_extra={"ui_type": "textarea",
                           "readOnly": True, "ui_group": "任务状态"}
    )
    normalized_reason: str = Field(
        default="",
        title="处理理由",
        description="标准化处理的理由",
        json_schema_extra={"ui_type": "textarea",
                           "readOnly": True, "ui_group": "任务状态"}
    )
    task_list: List[Dict[str, Any]] = Field(
        default_factory=list,
        title="任务列表",
        description="生成的任务列表",
        json_schema_extra={"ui_type": "json_viewer", "ui_group": "任务状态"}
    )
    current_task_index: int = Field(
        default=0,
        title="当前任务索引",
        description="当前执行的任务索引",
        json_schema_extra={"ui_type": "number",
                           "readOnly": True, "ui_group": "任务状态"}
    )
    subtasks: List[Dict[str, Any]] = Field(
        default_factory=list,
        title="子任务列表",
        description="当前任务拆分的子任务列表",
        json_schema_extra={"ui_type": "json_viewer", "ui_group": "任务状态"}
    )
    max_download_subtasks: Optional[int] = Field(
        default=None,
        title="最大下载子任务",
        description="最大下载子任务数限制",
        json_schema_extra={"ui_type": "number", "ui_group": "任务设置"}
    )

    # --- Data Context (数据上下文) ---
    datasets_background: str = Field(
        default="",
        title="数据集背景",
        description="数据集背景描述",
        json_schema_extra={"ui_type": "textarea", "ui_group": "数据上下文"}
    )
    category: str = Field(
        default="",
        title="数据类别",
        description="数据类别",
        json_schema_extra={"ui_type": "text", "ui_group": "数据上下文"}
    )
    research_summary: str = Field(
        default="",
        title="调研总结",
        description="调研总结",
        json_schema_extra={"ui_type": "textarea", "ui_group": "数据上下文"}
    )
    urls_visited: List[str] = Field(
        default_factory=list,
        title="已访问URL",
        description="已访问过的 URL 列表",
        json_schema_extra={"ui_type": "tags_input", "ui_group": "数据上下文"}
    )
    download_results: Dict[str, Any] = Field(
        default_factory=dict,
        title="下载结果",
        description="下载的原始结果",
        json_schema_extra={"ui_type": "json_viewer", "ui_group": "数据上下文"}
    )
    postprocess_results: Dict[str, Any] = Field(
        default_factory=dict,
        title="后处理结果",
        description="后处理后的结果",
        json_schema_extra={"ui_type": "json_viewer", "ui_group": "数据上下文"}
    )
    intermediate_data_path: str = Field(
        default="",
        title="中间数据路径",
        description="中间数据存储路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "数据上下文"}
    )

    # --- RAG Configuration (RAG配置) ---
    reset_rag: bool = Field(
        default=True,
        title="重置 RAG",
        description="是否重置 RAG 数据库",
        json_schema_extra={"ui_type": "switch", "ui_group": "RAG配置"}
    )
    rag_embed_model: str = Field(
        default="",
        title="Embedding 模型",
        description="RAG 嵌入模型名称",
        json_schema_extra={"ui_type": "text", "ui_group": "RAG配置"}
    )
    rag_collection_name: str = Field(
        default="rag_collection",
        title="集合名称",
        description="向量数据库集合名称",
        json_schema_extra={"ui_type": "text", "ui_group": "RAG配置"}
    )
    rag_api_base_url: str = Field(
        default="",
        title="RAG API Base",
        description="RAG 服务 Base URL",
        json_schema_extra={"ui_type": "text", "ui_group": "RAG配置"}
    )
    rag_api_key: str = Field(
        default="",
        title="RAG API Key",
        description="RAG 服务 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "RAG配置"}
    )

    # --- External Auth (外部认证) ---
    kaggle_username: str = Field(
        default="",
        title="Kaggle 用户名",
        description="Kaggle 用户名",
        json_schema_extra={"ui_type": "text", "ui_group": "外部认证"}
    )
    kaggle_key: str = Field(
        default="",
        title="Kaggle Key",
        description="Kaggle API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "外部认证"}
    )

    # --- Mapping Subgraph (映射子图参数) ---
    default_mapping_format: str = Field(
        default="",
        title="映射 Schema",
        description="默认的数据映射格式/Schema",
        json_schema_extra={"ui_type": "code_editor",
                           "language": "json", "ui_group": "数据映射"}
    )

    # --- Sub-node: Webpage Collect (网页收集节点参数) ---
    webpage_collect_summary: str = Field(
        default="",
        title="收集总结",
        description="网页收集阶段的总结",
        json_schema_extra={"ui_type": "textarea", "ui_group": "网页收集"}
    )
    webpage_collect_urls_visited: List[str] = Field(
        default_factory=list,
        title="收集阶段URL",
        description="网页收集阶段访问的 URL",
        json_schema_extra={"ui_type": "tags_input", "ui_group": "网页收集"}
    )
    webpage_collect_data_count: int = Field(
        default=0,
        title="收集数量",
        description="网页收集的数据条数",
        json_schema_extra={"ui_type": "number", "ui_group": "网页收集"}
    )
    webpage_collect_jsonl_path: str = Field(
        default="",
        title="JSONL 路径",
        description="网页收集结果 JSONL 路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "网页收集"}
    )
    webpage_collect_db_path: str = Field(
        default="",
        title="DB 路径",
        description="网页收集结果 DB 路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "网页收集"}
    )

    # --- Sub-node: Webpage Dataset (数据集节点参数) ---
    webpage_dataset_summary: str = Field(
        default="",
        title="数据集总结",
        description="数据集生成阶段的总结",
        json_schema_extra={"ui_type": "textarea", "ui_group": "数据集生成"}
    )
    webpage_dataset_count: int = Field(
        default=0,
        title="数据集数量",
        description="最终生成的数据集条数",
        json_schema_extra={"ui_type": "number", "ui_group": "数据集生成"}
    )
    webpage_dataset_jsonl_path: str = Field(
        default="",
        title="最终 JSONL 路径",
        description="最终数据集 JSONL 路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "数据集生成"}
    )

# ==========================================
# 定义 WebCrawlerState 模块的状态类 
# ==========================================


class WebCrawlerState(BaseModel):
    """
    WebCrawler 模块的专用状态管理类
    用于网页爬取、内容提取和数据集生成
    """
    # === API 配置 (API密钥) ===
    deepseek_api_key: str = Field(
        default="",
        title="DeepSeek API Key",
        description="DeepSeek API 密钥，用于 LLM 调用",
        json_schema_extra={"ui_type": "password", "ui_group": "API配置"}
    )
    tavily_api_key: str = Field(
        default="",
        title="Tavily API Key",
        description="Tavily API 密钥，用于网页搜索",
        json_schema_extra={"ui_type": "password", "ui_group": "API配置"}
    )
    deepseek_api_base: str = Field(
        default="https://api.deepseek.com/v1",
        title="DeepSeek API Base URL",
        description="DeepSeek API 的基础 URL",
        json_schema_extra={"ui_type": "text", "ui_group": "API配置"}
    )

    # === 模型配置 (Model Config) ===
    model: str = Field(
        default="deepseek-chat",
        title="模型名称",
        description="使用的模型名称",
        json_schema_extra={"ui_type": "text", "ui_group": "模型配置"}
    )
    temperature: float = Field(
        default=0.7,
        title="采样温度",
        description="LLM 采样温度 (0.0 - 1.0)",
        ge=0.0, le=1.0,
        json_schema_extra={"ui_type": "slider", "step": 0.1, "max": 1, "ui_group": "模型配置"}
    )

    # === 查询生成配置 (Query Generation) ===
    num_queries: int = Field(
        default=1,
        title="查询数量",
        description="生成的搜索查询数量",
        json_schema_extra={"ui_type": "number", "ui_group": "查询设置"}
    )

    # === 爬取策略配置 (Crawl Strategy) ===
    max_pages: int = Field(
        default=10,
        title="最大页面数",
        description="最大爬取页面数量",
        json_schema_extra={"ui_type": "number", "ui_group": "爬取策略"}
    )
    crawl_depth: int = Field(
        default=1,
        title="爬取深度",
        description="最大爬取深度",
        json_schema_extra={"ui_type": "number", "ui_group": "爬取策略"}
    )
    max_links_per_page: int = Field(
        default=2,
        title="每页最大链接数",
        description="每个页面最大跟踪链接数量",
        json_schema_extra={"ui_type": "number", "ui_group": "爬取策略"}
    )
    concurrent_pages: int = Field(
        default=2,
        title="并发页面数",
        description="并发爬取的页面数量",
        json_schema_extra={"ui_type": "number", "ui_group": "爬取策略"}
    )

    # === 内容过滤配置 (Content Filter) ===
    min_text_length: int = Field(
        default=500,
        title="最小文本长度",
        description="内容过滤的最小文本长度（字符）",
        json_schema_extra={"ui_type": "number", "ui_group": "内容过滤"}
    )
    min_code_length: int = Field(
        default=50,
        title="最小代码长度",
        description="内容过滤的最小代码长度（字符）",
        json_schema_extra={"ui_type": "number", "ui_group": "内容过滤"}
    )
    min_relevance_score: int = Field(
        default=6,
        title="最小相关性分数",
        description="内容过滤的最小相关性分数 (0-10)",
        ge=0, le=10,
        json_schema_extra={"ui_type": "slider", "step": 1, "max": 10, "ui_group": "内容过滤"}
    )
    url_patterns: Optional[str] = Field(
        default=None,
        title="URL 模式",
        description="URL 匹配模式规则，用于过滤特定链接",
        json_schema_extra={"ui_type": "text", "ui_group": "内容过滤"}
    )

    # === 运行时配置 (Runtime Config) ===
    request_delay: float = Field(
        default=2.0,
        title="请求延迟",
        description="请求之间的延迟时间（秒）",
        json_schema_extra={"ui_type": "number", "ui_group": "运行配置"}
    )
    timeout: int = Field(
        default=30,
        title="超时时间",
        description="请求超时时间（秒）",
        json_schema_extra={"ui_type": "number", "ui_group": "运行配置"}
    )
    max_retries: int = Field(
        default=3,
        title="最大重试次数",
        description="请求失败时的最大重试次数",
        json_schema_extra={"ui_type": "number", "ui_group": "运行配置"}
    )

    # === 输出配置 (Output Config) ===
    output_format: str = Field(
        default="jsonl",
        title="输出格式",
        description="输出文件格式",
        json_schema_extra={
            "ui_type": "select",
            "options": ["jsonl", "json"],
            "ui_group": "输出设置"
        }
    )
    save_html: bool = Field(
        default=False,
        title="保存 HTML",
        description="是否保存原始 HTML 内容",
        json_schema_extra={"ui_type": "switch", "ui_group": "输出设置"}
    )
    output_dir: str = Field(
        default="",
        title="输出目录",
        description="爬取结果的输出目录路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "输出设置"}
    )
    output_run_id: str = Field(
        default="",
        title="运行 ID",
        description="本次爬取会话的运行 ID",
        json_schema_extra={"ui_type": "text", "readOnly": True, "ui_group": "输出结果"}
    )
    output_result: Optional[Dict[str, Any]] = Field(
        default=None,
        title="爬取结果",
        description="完整的爬取结果数据",
        json_schema_extra={"ui_type": "json_viewer", "readOnly": True, "ui_group": "输出结果"}
    )

    # === 数据集生成配置 (Dataset Generation) ===
    max_records_per_page: int = Field(
        default=10,
        title="每页最大记录数",
        description="每个网页最多生成的数据记录数",
        json_schema_extra={"ui_type": "number", "ui_group": "数据集生成"}
    )
    dataset_concurrent_limit: int = Field(
        default=5,
        title="数据集生成并发数",
        description="数据集生成的并发限制",
        json_schema_extra={"ui_type": "number", "ui_group": "数据集生成"}
    )
    max_content_length: int = Field(
        default=50000,
        title="最大内容长度",
        description="LLM 处理的每页内容最大字符数",
        json_schema_extra={"ui_type": "number", "ui_group": "数据集生成"}
    )
    debug: bool = Field(
        default=False,
        title="调试模式",
        description="是否启用调试模式",
        json_schema_extra={"ui_type": "switch", "ui_group": "数据集生成"}
    )

    # === 数据集生成输出 (Dataset Output) ===
    dataset_summary: str = Field(
        default="",
        title="数据集生成摘要",
        description="数据集生成的摘要信息",
        json_schema_extra={"ui_type": "textarea", "readOnly": True, "ui_group": "数据集输出"}
    )
    dataset_sft_count: int = Field(
        default=0,
        title="SFT 记录数",
        description="生成的 SFT 格式记录数量",
        json_schema_extra={"ui_type": "number", "readOnly": True, "ui_group": "数据集输出"}
    )
    dataset_pt_count: int = Field(
        default=0,
        title="PT 记录数",
        description="生成的 PT 格式记录数量",
        json_schema_extra={"ui_type": "number", "readOnly": True, "ui_group": "数据集输出"}
    )
    dataset_sft_path: str = Field(
        default="",
        title="SFT 文件路径",
        description="SFT 格式 JSONL 文件保存路径",
        json_schema_extra={"ui_type": "file_path", "readOnly": True, "ui_group": "数据集输出"}
    )
    dataset_pt_path: str = Field(
        default="",
        title="PT 文件路径",
        description="PT 格式 JSONL 文件保存路径",
        json_schema_extra={"ui_type": "file_path", "readOnly": True, "ui_group": "数据集输出"}
    )

    # === 数据集映射配置 (Dataset Mapping - 使用 Obtainer.mapping) ===
    sft_mapping_format: str = Field(
        default="jsonl_sft",
        title="SFT 映射格式",
        description="SFT 中间数据的目标格式 (FORMAT_MAPPERS key)",
        json_schema_extra={"ui_type": "text", "ui_group": "数据集映射"}
    )
    pt_mapping_format: str = Field(
        default="jsonl_pt",
        title="PT 映射格式",
        description="PT 中间数据的目标格式 (FORMAT_MAPPERS key)",
        json_schema_extra={"ui_type": "text", "ui_group": "数据集映射"}
    )
    dataset_sft_mapped_path: str = Field(
        default="",
        title="SFT 映射路径",
        description="映射后的 SFT 数据集文件路径",
        json_schema_extra={"ui_type": "file_path", "readOnly": True, "ui_group": "数据集映射"}
    )
    dataset_pt_mapped_path: str = Field(
        default="",
        title="PT 映射路径",
        description="映射后的 PT 数据集文件路径",
        json_schema_extra={"ui_type": "file_path", "readOnly": True, "ui_group": "数据集映射"}
    )
    dataset_mapping_results: Optional[Dict[str, Any]] = Field(
        default=None,
        title="映射结果",
        description="SFT/PT 数据集映射结果详情",
        json_schema_extra={"ui_type": "json_viewer", "readOnly": True, "ui_group": "数据集映射"}
    )



class JudgerState(BaseModel):
    eval_model_path: str = Field(
        default=None,
        title="评估模型路径",
        description="评估模型路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "评估模型"}
    )
    eval_task_type: str = Field(
        default="code",
        title="评估任务类型",
        description="评估任务类型",
        json_schema_extra={"ui_type": "list", "ui_group": "评估模型",
                           "allowed_values": ["code", "text2sql"]}
    )
    eval_base_url: str = Field(
        default=None,
        title="评估模型 Base URL",
        description="评估模型 Base URL，未设置或为空的时候，将会尝试通过本地开启vllm",
        json_schema_extra={"ui_type": "text", "ui_group": "评估模型"}
    )
    eval_api_key: str = Field(
        default="EMPTY",
        title="评估模型 API Key",
        description="评估模型 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "评估模型"}
    )
    eval_temperature: float = Field(
        default=0,
        title="评估模型温度",
        description="评估模型温度",
        json_schema_extra={"ui_type": "slider", "max": 1, "ui_group": "评估模型"}
    )
    eval_top_p: float = Field(
        default=0.95,
        title="评估模型 Top P",
        description="评估模型 Top P",
        json_schema_extra={"ui_type": "slider", "max": 1, "ui_group": "评估模型"}
    )
    eval_problem_path: str = Field(
        default=None,
        title="评估模型问题路径",
        description="评估模型问题路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "评估模型"}
    )
    eval_format_type: str = Field(
        default=None,
        title="评估模型问题格式化类型",
        description="评估模型问题格式化类型，如果为空或None将不进入格式化节点，改格式化方式可以用户自由定义，目前支持\"human-eval\"和\"mbpp\"，格式化后的文件将存至output_dir定义的目录下",
        json_schema_extra={"ui_type": "list", "ui_group": "评估模型", "allowed_values": ["human-eval"]}
    )
    eval_batch_size: int = Field(
        default=10,
        title="评估模型批量大小",
        description="评估模型批量大小，也是问题生成样例数量大小",
        json_schema_extra={"ui_type": "number", "ui_group": "评估模型"}
    )
    eval_case_num: int = Field(
        default=10,
        title="评估模型样例生成数量",
        description="评估模型每个问题的样例生成数量",
        json_schema_extra={"ui_type": "number", "ui_group": "评估模型"}
    )
    eval_text2sql_dir: str = Field(
        default=None,
        title="评估模型text2sql数据库目录",
        description="评估模型text2sql数据库目录，仅text2sql任务下生效，并且数据文件中需要以字段db_id标注出相应的数据库文件夹至路径目录下",
        json_schema_extra={"ui_type": "file_path", "ui_group": "评估模型"}
    )
    eval_env_configs: str = Field(
        default='{"CUDA_VISIBLE_DEVICES": "0,1","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}',
        title="评估模型vllm启动环境参数",
        description="评估模型vllm启动环境参数，需要完整字符串配置，为空则认为已启动vllm将会跳过启动vllm的过程",
        json_schema_extra={"ui_type": "text", "ui_group": "评估模型"}
    )
    eval_vllm_port: int = Field(
        default=8911,
        title="vllm本地启动参数——port",
        description="vllm本地启动参数——port，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效",
        json_schema_extra={"ui_type": "number", "ui_group": "评估模型"}
    )
    eval_vllm_tensor_parallel_size: int = Field(
        default=2,
        title="vllm本地启动参数——tensor_parallel_size",
        description="vllm本地启动参数——tensor_parallel_size，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效",
        json_schema_extra={"ui_type": "number", "ui_group": "评估模型"}
    )
    eval_vllm_gpu_memory_utilization: float = Field(
        default=0.9,
        title="vllm本地启动参数——gpu_memory_utilization",
        description="vllm本地启动参数——gpu_memory_utilization，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效",
        json_schema_extra={"ui_type": "slider", "ui_group": "评估模型"}
    )
    eval_vllm_env_path: str = Field(
        default="",
        title="vllm本地启动参数——启动环境",
        description="vllm本地启动参数——启动环境，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效，为空时默认为当前环境启动。参数需要具体到python目录，格式应为<path>/miniconda3/envs/<env_name>/bin/python",
        json_schema_extra={"ui_type": "file_path", "ui_group": "评估模型"}
    )
    output_dir: str = Field(
        default=None,
        title="评估模型输出文件目录",
        description="评估模型输出文件目录，包含中间产出的样例以及最终评测的结果。输出文件路径将会在judger参数output_result_path（评测结果）、output_case_path（评测样例集）、output_problem_path（评测格式化后问题集）中记录。",
        json_schema_extra={"ui_type": "file_path", "ui_group": "评估模型", "is_output": True}
    )

class AnalyzerState(BaseModel):
    analyze_task_type: str = Field(
        default="code",
        title="分析任务类型",
        description="分析任务类型",
        json_schema_extra={"ui_type": "list", "ui_group": "分析模型",
                           "allowed_values": ["code", "text2sql"]}
    )
    analyze_batch_size: int = Field(
        default=20,
        title="分析模型批量大小",
        description="分析模型批量大小",
        json_schema_extra={"ui_type": "number", "ui_group": "分析模型"}
    )
    analyze_model_path: str = Field(
        default="",
        title="分析模型路径",
        description="分析模型路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )
    analyze_base_url: str = Field(
        default="",
        title="分析模型 Base URL",
        description="分析模型 Base URL",
        json_schema_extra={"ui_type": "text", "ui_group": "分析模型"}
    )
    analyze_api_key: str = Field(
        default="",
        title="分析模型 API Key",
        description="分析模型 API Key",
        json_schema_extra={"ui_type": "password", "ui_group": "分析模型"}
    )
    analyze_temperature: float = Field(
        default=0,
        title="分析模型温度",
        description="分析模型温度",
        json_schema_extra={"ui_type": "slider", "max": 1, "ui_group": "分析模型"}
    )
    analyze_top_p: float = Field(
        default=0.95,
        title="分析模型 Top P",
        description="分析模型 Top P",
        json_schema_extra={"ui_type": "slider", "max": 1, "ui_group": "分析模型"}
    )
    output_brief: bool = Field(
        default=False,
        title="是否输出简要分析结果",
        description="是否输出简要分析结果",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "分析模型"}
    )
    analyze_output_result_path: str = Field(
        default="",
        title="分析模型输出结果路径",
        description="分析模型输出结果路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )
    analyze_output_summary_path: str = Field(
        default="",
        title="分析模型输出摘要路径",
        description="分析模型输出摘要路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )
    analyze_sampling_top_k: int = Field(
        default=5,
        title="分析模型采样 Top K",
        description="分析模型采样 Top K",
        json_schema_extra={"ui_type": "number", "ui_group": "分析模型"}
    )
    analyze_output_report_json_path: str = Field(
        default="",
        title="分析模型输出报告 JSON 路径",
        description="分析模型输出报告 JSON 路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )
    analyze_output_report_text_path: str = Field(
        default="",
        title="分析模型输出报告文本路径",
        description="分析模型输出报告文本路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )
    output_suggestion: bool = Field(
        default=False,
        title="是否输出建议",
        description="是否输出建议",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "分析模型"}
    )
    analyze_output_suggestion_path: str = Field(
        default="",
        title="分析模型输出建议路径",
        description="分析模型输出建议路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "分析模型"}
    )


class TrainerState(BaseModel):
    train_framework: str = Field(
        default="",
        title="训练框架",
        description="训练框架",
        json_schema_extra={"ui_type": "list", "ui_group": "训练模型",
                           "allowed_values": ["llamafactory", "verl"]}
    )
    llamafactory_dir: str = Field(
        default="",
        title="LlamaFactory 目录",
        description="LlamaFactory 目录",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    train_input_dataset_path: str = Field(
        default="",
        title="训练数据集路径",
        description="训练数据集路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    train_input_task_description: str = Field(
        default="",
        title="训练任务描述",
        description="训练任务描述",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    train_input_config_template_path: str = Field(
        default="",
        title="训练配置模板路径",
        description="训练配置模板路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    train_config_output_path: str = Field(
        default="",
        title="训练配置输出路径",
        description="训练配置输出路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    train_input_model_name: str = Field(
        default="",
        title="训练模型名称",
        description="训练模型名称",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    train_input_use_swanlab: bool = Field(
        default=True,
        title="是否使用 SwanLab",
        description="是否使用 SwanLab",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "训练模型"}
    )
    train_input_swanlab_project: str = Field(
        default="",
        title="SwanLab 项目名称",
        description="SwanLab 项目名称",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    data_check_passed: bool = Field(
        default=False,
        title="数据检查是否通过",
        description="数据检查是否通过",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "训练模型"}
    )
    data_check_result: dict = Field(
        default={},
        title="数据检查结果",
        description="数据检查结果",
        json_schema_extra={"ui_type": "json", "ui_group": "训练模型"}
    )
    data_check_report_path: str = Field(
        default="",
        title="数据检查报告路径",
        description="数据检查报告路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    data_check_error: str = Field(
        default="",
        title="数据检查错误信息",
        description="数据检查错误信息",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    config_generation_success: bool = Field(
        default=False,
        title="配置生成是否成功",
        description="配置生成是否成功",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "训练模型"}
    )
    config_explanation_path: str = Field(
        default="",
        title="配置解释路径",
        description="配置解释路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    config_generation_error: str = Field(
        default="",
        title="配置生成错误信息",
        description="配置生成错误信息",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    training_success: bool = Field(
        default=False,
        title="训练是否成功",
        description="训练是否成功",
        json_schema_extra={"ui_type": "toggle_switch", "ui_group": "训练模型"}
    )
    training_execution_time: float = Field(
        default=0,
        title="训练执行时间",
        description="训练执行时间",
        json_schema_extra={"ui_type": "number", "ui_group": "训练模型"}
    )
    training_task_id: str = Field(
        default="",
        title="训练任务 ID",
        description="训练任务 ID",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    training_final_status: dict = Field(
        default={},
        title="训练最终状态",
        description="训练最终状态",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    training_log_path: str = Field(
        default="",
        title="训练日志路径",
        description="训练日志路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    training_report_path: str = Field(
        default="",
        title="训练报告路径",
        description="训练报告路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    training_error: str = Field(
        default="",
        title="训练错误信息",
        description="训练错误信息",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    training_service_url: str = Field(
        default="http://localhost:8000",
        title="训练服务器 URL",
        description="训练服务器 URL",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    current_training_status: str = Field(
        default="",
        title="当前训练状态",
        description="当前训练状态",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    update_model_path: str = Field(
        default="",
        title="更新模型路径",
        description="更新模型路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )
    swanlab_url: str = Field(
        default="",
        title="SwanLab URL",
        description="SwanLab URL",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )
    train_output_swanlab_log_path: str = Field(
        default="",
        title="SwanLab 日志路径",
        description="SwanLab 日志路径",
        json_schema_extra={"ui_type": "file_path", "ui_group": "训练模型"}
    )


class ConfigerState(BaseModel):
    configer_error: dict = Field(
        default=None,
        title="配置器错误信息",
        description="配置器错误信息",
        json_schema_extra={"ui_type": "text", "ui_group": "训练模型"}
    )


def get_state_config_schema():
    """获取Starter配置字段说明"""
    def get_field_statement(model_cls):
        schema = model_cls.model_json_schema()
        properties = schema.get('properties', {})
        return properties

    fields_statement = {
        "judger": get_field_statement(JudgerState),
        "configer": get_field_statement(ConfigerState),
        "analyzer": get_field_statement(AnalyzerState),
        "trainer": get_field_statement(TrainerState),
        "obtainer": get_field_statement(ObtainerState),
        "webcrawler": get_field_statement(WebCrawlerState),
    }

    return fields_statement


def get_missing_fields(required_fields, state: dict):
    missing_fields = {}
    for key in required_fields:
        for field in required_fields[key]:
            if key == 'default':
                if field not in state or state.get(field) is None:
                    missing_fields.setdefault(key, []).append(field)
            else:
                if field not in state.get(key, {}) or state.get(key, {}).get(field) is None:
                    missing_fields.setdefault(key, []).append(field)
    return missing_fields
# ==========================================
# 3. 主 State 定义
# ==========================================


class LoopAIState(MessagesState):
    # === Global Attributes (全局属性) ===
    task_id: str
    mined_data: str
    output_dir: str  # 全局输出目录

    # === Obtainer Module (新增的模块化部分) ===
    # 使用 merge_dict 处理更新
    # 这里的 Dict[str, Any] 实际上就是 ObtainerState 转换后的字典
    obtainer: Annotated[Dict[str, Any], merge_dict]

    # === Configer (保持原样) ===
    configer: Annotated[Dict[str, Any], merge_dict]

    # === Judger (保持原样) ===
    judger: Annotated[Dict[str, Any], merge_dict]
    # eval_model_path: str
    # eval_base_url: str
    # eval_api_key: str
    # eval_temperature: float = 0
    # eval_top_p: float = 0.95
    # eval_test_case_path: str
    # eval_problem_path: str
    # eval_result_path: str
    # eval_batch_size: int = 20

    # === Analyzer (保持原样) ===
    analyzer: Annotated[Dict[str, Any], merge_dict]
    # analyze_task_type: str = 'code'
    # analyze_batch_size: int = 20
    # analyze_model_path: str
    # analyze_base_url: str
    # analyze_api_key: str
    # analyze_temperature: float = 0
    # analyze_top_p: float = 0.95
    # output_brief: bool
    # analyze_output_result_path: str
    # analyze_output_summary_path: str
    # analyze_sampling_top_k: int = 5
    # analyze_output_report_json_path: str
    # analyze_output_report_text_path: str
    # output_suggestion: bool
    # analyze_output_suggestion_path: str

    # === Trainer (保持原样) ===
    trainer: Annotated[Dict[str, Any], merge_dict]

    # === WebCrawler (网页爬取模块) ===
    webcrawler: Annotated[Dict[str, Any], merge_dict]

    # train_input_dataset_path: str
    # train_input_task_description: str
    # train_input_config_template_path: str
    # train_config_output_path: str
    # train_input_model_name: str
    # train_input_use_swanlab: bool = True
    # train_input_swanlab_project: str
    # data_check_passed: bool = False
    # data_check_result: dict = {}
    # data_check_report_path: str = ""
    # data_check_error: str = ""
    # config_generation_success: bool = False
    # config_explanation_path: str = ""
    # config_generation_error: str = ""
    # training_success: bool = False
    # training_execution_time: float = 0.0
    # training_task_id: str = ""
    # training_final_status: dict = {}
    # training_log_path: str = ""
    # training_report_path: str = ""
    # training_error: str = ""
    # training_service_url: str = "http://localhost:8000"
    # current_training_status: str = ""
    # update_model_path: str
    # swanlab_url: str
    # train_output_swanlab_log_path: str

    # === Graph Control (图控制属性) ===
    current: str
    next_to: Annotated[str, replace_value]

    # automated_query 既是全局控制信号，也可能被 obtainer 生成
    automated_query: Annotated[str, replace_value]

    exception: Annotated[str, replace_value]


class RuntimeContext(TypedDict):
    exception_navigate: str
