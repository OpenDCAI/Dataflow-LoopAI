# WebCrawler Agent

## 工作流程

WebCrawler Agent 的执行流程包含四个主要节点：

1. **start_node**: 初始化配置并验证必需参数
2. **crawl_node**: 执行网页爬取任务，提取网页内容和代码块
3. **webcrawler_dataset_node**: 从爬取内容中生成训练数据集（SFT/PT 格式），并对生成了SFT的网页生成摘要和相关性评分
4. **end_node**: 返回结果

```
start_node → crawl_node → webcrawler_dataset_node → end_node
```

**处理逻辑**：
- `crawl_node` 阶段：爬取网页并提取内容，不生成摘要
- `webcrawler_dataset_node` 阶段：
  1. 直接从网页 Markdown 内容生成 SFT/PT 数据集
  2. 对成功生成了 SFT 记录的网页，生成摘要和相关性评分（0-10分）

## 代码结构

WebCrawler Agent 的代码组织如下：

```
loopai/agents/WebCrawler/
├── nodes/
│   ├── start_node.py              # 初始化节点
│   ├── crawl_node.py              # 爬取节点
│   ├── webcrawler_dataset_node.py # 数据集生成节点（工作流编排）
│   └── end_node.py                # 结束节点
└── utils/
    ├── crawl_orchestrator.py      # 爬取流程编排器
    ├── content_analyzer.py        # 内容分析器
    ├── dataset_generator.py        # 数据集生成工具（SFT/PT生成、摘要生成）
    ├── data_structures.py         # 数据结构定义
    └── log_manager.py              # 日志管理器
```

**模块说明**：
- **nodes/**: 包含 LangGraph 节点实现，负责工作流编排和状态管理
- **utils/**: 包含可复用的工具函数和类
  - `dataset_generator.py`: 提供数据集生成的核心功能（SFT/PT 记录生成、网页摘要生成等）
  - `crawl_orchestrator.py`: 管理爬取流程和并发控制
  - `content_analyzer.py`: 分析网页内容并提取代码块
  - `data_structures.py`: 定义数据结构（如 `CrawledContent`）
  - `log_manager.py`: 统一日志管理

**设计原则**：
- 节点文件专注于工作流编排和状态管理
- 业务逻辑封装在 `utils` 模块中，便于复用和测试
- 数据集生成相关功能集中在 `dataset_generator.py` 中

## 依赖的 State

### 必需参数

#### API 密钥配置
- `webcrawler_deepseek_api_key` (str): DeepSeek API 密钥，用于 LLM 调用
- `webcrawler_tavily_api_key` (str): Tavily API 密钥，用于网页搜索

#### 任务描述
- `messages` (List[Message]): 用户任务描述的消息列表
  - 通常将任务描述字符串包装成 `HumanMessage(content=task_description)` 放入列表
  - 在实际使用中（如 `run_webcrawler.py`），通常将任务描述字符串（如 `test_query`）包装成 `HumanMessage` 并放入 `messages` 列表
- `automated_query` (str): 由大模型自动化生成的查询字符串

### 可选配置参数

#### 模型配置
- `webcrawler_deepseek_api_base` (str): DeepSeek API 基础 URL，默认 `"https://api.deepseek.com/v1"`
  - 如果未设置，会尝试使用 `analyze_base_url`
- `webcrawler_model` (str): 使用的模型名称，默认 `"deepseek-chat"`
  - 如果未设置，会尝试使用 `analyze_model_path`
- `webcrawler_deepseek_api_key` (str): DeepSeek API 密钥
  - 如果未设置，会尝试使用 `analyze_api_key`
- `webcrawler_temperature` (float): LLM 温度参数，默认 `0.7`

#### 查询生成配置
- `webcrawler_num_queries` (int): 生成的搜索查询数量，默认 `5`

#### 爬取策略配置
- `webcrawler_max_pages` (int): 最大爬取页面数，默认 `10000`
- `webcrawler_crawl_depth` (int): 最大爬取深度，默认 `3`
- `webcrawler_max_links_per_page` (int): 每页最大链接数，默认 `5`
- `webcrawler_concurrent_pages` (int): 并发爬取页面数，默认 `3`

#### 内容过滤配置
- `webcrawler_min_text_length` (int): 最小文本长度（字符），默认 `500`
- `webcrawler_min_code_length` (int): 最小代码长度（字符），默认 `50`
- `webcrawler_min_relevance_score` (int): 最小相关性分数（1-10），默认 `6`
- `webcrawler_url_patterns` (str, optional): URL 模式匹配规则，默认 `None`

#### 运行时配置
- `webcrawler_request_delay` (float): 请求之间的延迟（秒），默认 `2.0`
- `webcrawler_timeout` (int): 请求超时时间（秒），默认 `30`
- `webcrawler_max_retries` (int): 最大重试次数，默认 `3`

#### 输出配置
- `webcrawler_output_dir` (str): 输出目录路径，默认 `"./output"`
- `webcrawler_output_format` (str): 输出格式，可选 `"jsonl"` 或 `"json"`，默认 `"jsonl"`
- `webcrawler_save_html` (bool): 是否保存 HTML 内容，默认 `False`

#### 数据集生成配置
- `webcrawler_max_records_per_page` (int): 每个网页最多生成的记录数，默认 `100`
- `webcrawler_min_relevance_score` (float): 最小相关性分数（0.0-1.0），默认 `0.6`
- `webcrawler_dataset_concurrent_limit` (int): 数据集生成的并发限制，默认 `50`
- `webcrawler_max_content_length` (int): LLM 处理的每页内容最大字符数，默认 `50000`
- `webcrawler_debug` (bool): 是否启用调试模式，默认 `False`
- `webcrawler_prompt_template_dir` (str, optional): Prompt 模板目录路径，默认 `None`

**注意**：`webcrawler_dataset_node` 依赖于 `crawl_node` 的输出。它需要从 `webcrawler_output_result.crawled_data` 中读取爬取的数据。如果 `crawl_node` 没有生成数据，数据集节点会设置异常并跳过处理。

## 输出更新的 State

WebCrawler Agent 执行完成后，会在 State 中添加以下字段：

### 爬取结果
- `webcrawler_output_result` (Dict[str, Any]): 包含完整的爬取结果，结构如下：
  ```python
  {
      "task": str,                    # 任务描述
      "run_id": str,                  # 运行 ID（时间戳格式）
      "timestamp": str,               # ISO 格式时间戳
      "total_pages": int,             # 成功爬取的页面数
      "search_queries": List[str],    # 生成的搜索查询列表
      "crawled_data": List[Dict],     # 爬取的数据列表
      "overall_summary": Dict,        # 整体摘要
      "statistics": Dict              # 统计信息
  }
  ```

- `webcrawler_output_run_id` (str): 本次运行的唯一 ID
- `webcrawler_output_dir` (str): 输出文件保存的目录路径

### 数据集生成结果（webcrawler_dataset_node 输出）
- `webcrawler_dataset_summary` (str): 数据集生成摘要信息
- `webcrawler_dataset_sft_count` (int): 生成的 SFT 格式记录数量
- `webcrawler_dataset_pt_count` (int): 生成的 PT 格式记录数量
- `webcrawler_dataset_sft_path` (str): SFT 格式 JSONL 文件保存路径
- `webcrawler_dataset_pt_path` (str): PT 格式 JSONL 文件保存路径
- `webcrawler_dataset_sft_mapped_path` (str): 映射后的 SFT 数据集文件路径（通过 Obtainer.mapping）
- `webcrawler_dataset_pt_mapped_path` (str): 映射后的 PT 数据集文件路径（通过 Obtainer.mapping）
- `webcrawler_dataset_mapping_results` (Dict): 数据集映射结果详情

### 消息更新
- `webcrawler_messages` (List[Message]): 在消息列表末尾添加一个 `AIMessage`，包含任务执行摘要

### 导航信息
- `webcrawler_next_to` (str): 设置为 `"query_node"`，用于返回到父图

### 异常信息（如果发生错误）
- `webcrawler_exception` (str): 错误信息字符串
- `webcrawler_output_result` (Dict): 包含 `{"error": str}` 的错误结果

## 使用示例

### 基本使用

```python
from loopai.agents.WebCrawler import WebCrawlerAgent
from loopai.memory import checkpointer, store
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage

# 初始化 Agent
agent = WebCrawlerAgent(
    checkpointer=checkpointer,
    store=store,
)

# 创建图
graph = agent()

# 准备初始 State
initial_state = {
    'task_id': 'webcrawler_001',
    'output_dir': './output',
    'exception': '',
    'current': 'webcrawl',
    'next_to': '',
    'automated_query': '',
    'messages': [HumanMessage(content="搜索 Python 异步编程最佳实践")],
    
    # API 配置
    'webcrawler_deepseek_api_key': 'your-deepseek-api-key',
    'webcrawler_tavily_api_key': 'your-tavily-api-key',
    'webcrawler_deepseek_api_base': 'https://api.deepseek.com/v1',
    'webcrawler_model': 'deepseek-chat',
    
    # 爬取策略（可选）
    'webcrawler_num_queries': 5,
    'webcrawler_max_pages': 100,
    'webcrawler_crawl_depth': 3,
    'webcrawler_max_links_per_page': 5,
    'webcrawler_concurrent_pages': 3,
    
    # 内容过滤（可选）
    'webcrawler_min_text_length': 500,
    'webcrawler_min_code_length': 50,
    'webcrawler_min_relevance_score': 6,
    
    # 运行时配置（可选）
    'webcrawler_request_delay': 2.0,
    'webcrawler_timeout': 30,
    'webcrawler_max_retries': 3,
    
    # 输出配置（可选）
    'webcrawler_output_format': 'jsonl',
    'webcrawler_save_html': False,
    
    # 数据集生成配置（可选）
    'webcrawler_temperature': 0.7,
    'webcrawler_max_records_per_page': 100,
    'webcrawler_min_relevance_score': 0.6,
    'webcrawler_dataset_concurrent_limit': 50,
    'webcrawler_max_content_length': 50000,
    'webcrawler_debug': False,
}

# 执行图
config = {"configurable": {"thread_id": "webcrawler_001"}}
result = graph.invoke(initial_state, config=config)

# 查看结果
print(f"爬取页面数: {result['webcrawler_output_result']['total_pages']}")
print(f"输出目录: {result['webcrawler_output_dir']}")

# 查看数据集生成结果
if 'webcrawler_dataset_summary' in result:
    print(f"数据集摘要: {result['webcrawler_dataset_summary']}")
    print(f"SFT 记录数: {result.get('webcrawler_dataset_sft_count', 0)}")
    print(f"PT 记录数: {result.get('webcrawler_dataset_pt_count', 0)}")
    if result.get('webcrawler_dataset_sft_path'):
        print(f"SFT 文件路径: {result['webcrawler_dataset_sft_path']}")
    if result.get('webcrawler_dataset_pt_path'):
        print(f"PT 文件路径: {result['webcrawler_dataset_pt_path']}")
    if result.get('webcrawler_dataset_sft_mapped_path'):
        print(f"映射后的 SFT 文件路径: {result['webcrawler_dataset_sft_mapped_path']}")
    if result.get('webcrawler_dataset_pt_mapped_path'):
        print(f"映射后的 PT 文件路径: {result['webcrawler_dataset_pt_mapped_path']}")
```

### 在 Starter Agent 中使用

WebCrawler 可以作为子图集成到 Starter Agent 中：

```python
from loopai.agents.Starter import StarterAgent
from loopai.agents.WebCrawler import WebCrawlerAgent

# 在 Starter Agent 的 init_graph 中添加 WebCrawler 节点
webcrawler_node = WebCrawlerAgent(
    checkpointer=self.checkpointer,
    store=self.store
)(**kwargs)

builder.add_node("webcrawler_node", webcrawler_node)
```

## 输出结果

### webcrawler_output_result 详细结构

```python
{
    "task": "任务描述",
    "run_id": "20240101_120000",
    "timestamp": "2024-01-01T12:00:00",
    "total_pages": 50,
    "search_queries": [
        "query1",
        "query2",
        ...
    ],
    "crawled_data": [
        {
            "url": "https://example.com/page1",
            "title": "页面标题",
            "content": "页面内容（Markdown格式）...",
            "code_blocks": [
                {
                    "language": "python",
                    "code": "代码内容...",
                    "length": 100
                }
            ],
            "ai_summary": null,  # 爬取阶段不生成摘要，将在数据集生成阶段为生成了SFT的网页生成
            "metadata": {
                "extraction_method": "playwright",
                "content_length": 5000,
                "code_blocks_count": 3
            }
        },
        ...
    ],
    "overall_summary": {
        "message": "摘要生成已移至数据集生成阶段",
        "total_pages": 50
    },
    "statistics": {
        "pages_analyzed": 50,
        "total_content_length": 250000,
        "avg_content_length": 5000
    }
}
```

## 输出文件

### 爬取结果文件

爬取结果会保存在 `{output_dir}/webcrawler_output/run_{run_id}/` 目录下，包含：

- `final_result.json`: 完整的爬取结果
- `overall_summary.json`: 整体摘要
- `search_queries.json`: 搜索查询列表
- `page_*.json`: 每个页面的详细数据
- `research_summary_*.json`: 每个查询的研究摘要

### 数据集文件

数据集文件会保存在 `{output_dir}/webcrawler_dataset/` 目录下，包含：

- `webcrawler_dataset_sft_{timestamp}.jsonl`: SFT（监督微调）格式的训练数据集
  - 格式：包含 `messages` 字段的 JSONL，每条记录是一个问答对
  - 适用于包含代码块的网页内容
  - 结构：`{"messages": [{"role": "user", "content": "...", "loss_mask": false}, {"role": "assistant", "content": "...", "loss_mask": true}], "meta": {...}}`
  
- `webcrawler_dataset_pt_{timestamp}.jsonl`: PT（预训练）格式的训练数据集
  - 格式：包含 `text` 字段的 JSONL，每条记录是纯文本内容
  - 适用于没有代码块或 SFT 生成失败的网页内容
  - 结构：`{"text": "...", "meta": {...}}`

- `webpage_summaries_{timestamp}.jsonl`: 网页摘要和相关性评分文件（新增）
  - 格式：包含生成了 SFT 记录的网页摘要和相关性评分
  - 每条记录包含：`{"url": "...", "title": "...", "summary": "...", "relevance_score": 8}`
  - 相关性评分范围：0-10 分，表示与用户查询的相关程度

**数据集生成逻辑**：
1. **直接生成数据**：从网页 Markdown 内容直接生成 SFT/PT 数据集
   - 如果网页包含代码块，优先尝试生成 SFT 格式（question-code pairs）
   - 如果 SFT 生成失败或网页没有代码块，则生成 PT 格式（markdown 文本内容）
2. **生成摘要和评分**：对成功生成了 SFT 记录的网页，生成摘要和相关性评分（0-10分）
3. **相关性过滤**：所有记录都会根据 `webcrawler_min_relevance_score` 进行相关性过滤

### 数据集格式说明

#### SFT 格式示例

```json
{
  "messages": [
    {
      "role": "user",
      "content": "编写一个Python函数实现异步HTTP请求",
      "loss_mask": false
    },
    {
      "role": "assistant",
      "content": "import asyncio\nimport aiohttp\n\nasync def fetch_url(url):\n    async with aiohttp.ClientSession() as session:\n        async with session.get(url) as response:\n            return await response.text()",
      "loss_mask": true
    }
  ],
  "system": null,
  "meta": {
    "source": "https://example.com/page1",
    "webpage_title": "Python异步编程指南",
    "webpage_url": "https://example.com/page1",
    "generated_at": "2024-01-01T12:00:00Z",
    "language": "zh",
    "timestamp": null,
    "token_count": null,
    "quality_score": null,
    "original_id": null
  },
  "relevance_score": 0.85
}
```

#### PT 格式示例

```json
{
  "text": "# Python异步编程最佳实践\n\n异步编程是现代Python开发中的重要技术...",
  "meta": {
    "source": "https://example.com/page2",
    "webpage_title": "Python异步编程指南",
    "webpage_url": "https://example.com/page2",
    "generated_at": "2024-01-01T12:00:00Z",
    "language": "zh",
    "timestamp": null,
    "token_count": null,
    "quality_score": null,
    "original_id": null
  },
  "relevance_score": 0.75
}
```

#### 网页摘要格式示例

```json
{
  "url": "https://example.com/page1",
  "title": "Python异步编程指南",
  "summary": "该网页详细介绍了Python异步编程的核心概念和实践方法，包括asyncio库的使用、协程的创建和管理、以及异步HTTP请求的实现。内容涵盖了从基础到高级的完整教程，适合不同水平的开发者学习。",
  "relevance_score": 8
}
```

**注意**：
- 只有成功生成了 SFT 记录的网页才会出现在摘要文件中
- `relevance_score` 范围是 0-10，表示与用户查询的相关程度
- 摘要文件可以帮助识别哪些网页贡献了高质量的 SFT 训练数据