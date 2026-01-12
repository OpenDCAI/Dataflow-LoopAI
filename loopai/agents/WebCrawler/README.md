# WebCrawler Agent 使用指南

WebCrawler Agent 是 Dataflow-LoopAI 框架中负责网页爬取和数据集生成的智能代理。它能够自动化完成从查询理解到网页爬取、内容提取、数据集生成的完整流程。

## 🏗️ 架构设计

WebCrawler Agent 采用多阶段顺序执行架构：

```
启动节点 → 爬取节点 → 数据集生成节点 → 结束节点
    ↓          ↓            ↓            ↓
   结束       结束         结束         结束
```

### 1. 启动节点 (Start Node)

**功能：** 初始化配置参数，验证必需参数，设置默认值

**主要特性：**

- **配置初始化：** 自动设置默认参数和 API 密钥
  - 模型配置（API 基础 URL、模型名称、温度参数）
  - 查询生成配置（搜索查询数量）
  - 爬取策略配置（最大页面数、爬取深度、并发限制等）
  - 内容过滤配置（最小文本长度、最小代码长度、相关性分数阈值）
  - 运行时配置（请求延迟、超时时间、重试次数）
  - 输出配置（输出格式、是否保存 HTML）
  - 数据集生成配置（每页最大记录数、并发限制、内容长度限制）

- **参数验证：** 检查必需 API 密钥
  - DeepSeek API 密钥（用于 LLM 调用）
  - Tavily API 密钥（用于网页搜索）

**输出：**
- 更新 `webcrawler` 字典中的配置参数
- 设置环境变量 `TAVILY_API_KEY`

### 2. 爬取节点 (Crawl Node)

**功能：** 执行网页爬取任务，提取网页内容和代码块

**主要特性：**

- **智能查询生成：** 使用 LLM 根据用户查询生成多个搜索查询
  - 默认生成 5 个搜索查询
  - 可配置查询数量

- **多搜索引擎支持：**
  - Tavily API（默认）
  - 可扩展其他搜索引擎

- **深度爬取：** 支持多层级网页爬取
  - 可配置爬取深度（默认 3 层）
  - 每页最大链接数限制（默认 5 个）
  - 并发爬取控制（默认 3 个并发）

- **内容提取：** 使用 Playwright 进行网页内容抓取
  - 支持 JavaScript 渲染页面
  - 提取 Markdown 格式内容
  - 自动提取代码块（支持多种代码块格式）

- **内容过滤：** 智能过滤低质量内容
  - 最小文本长度过滤（默认 500 字符）
  - 最小代码长度过滤（默认 50 字符）
  - URL 模式匹配过滤（可选）

- **结果保存：** 保存爬取结果到本地文件
  - JSON/JSONL 格式输出
  - 可选保存 HTML 原始内容
  - 包含完整的元数据和统计信息

**输出：**
- `webcrawler.output_result`: 完整的爬取结果字典
- `webcrawler.output_run_id`: 本次运行的唯一 ID
- `webcrawler.output_dir`: 输出文件保存的目录路径

### 3. 数据集生成节点 (Dataset Generation Node)

**功能：** 从爬取内容中生成训练数据集（SFT/PT 格式），对生成了 SFT 的网页生成摘要和相关性评分，并将中间格式数据映射为目标格式

**主要特性：**

- **智能数据集生成：**
  - **SFT 格式生成：** 对于包含代码块的网页，生成问答对格式的训练数据
    - 用户消息：代码功能描述（问题）
    - 助手消息：代码块内容（答案）
  - **PT 格式生成：** 对于没有代码块或 SFT 生成失败的网页，生成纯文本格式的训练数据
    - 直接使用网页 Markdown 内容作为训练文本

- **摘要和评分生成：** 对成功生成了 SFT 记录的网页，生成摘要和相关性评分
  - 生成网页内容摘要
  - 相关性评分（0-10 分），表示与用户查询的相关程度
  - 保存到独立的摘要文件

- **相关性过滤：** 根据相关性分数过滤低质量数据
  - 默认最小相关性分数：0.6
  - 可配置过滤阈值

- **格式映射：** 自动将中间格式数据映射为目标格式
  - 使用 Constructor 的 `script_mapping_node` 进行格式转换
  - 可选择Alpaca格式、ChatML格式、JSONL预训练格式、OpenAI微调格式、Llama2对话格式

- **并发处理：** 支持并发生成数据集记录
  - 默认并发限制：5 个任务
  - 可配置并发数量

**输出：**
- `webcrawler.dataset_summary`: 数据集生成摘要信息
- `webcrawler.dataset_sft_count`: 生成的 SFT 格式记录数量
- `webcrawler.dataset_pt_count`: 生成的 PT 格式记录数量
- `webcrawler.dataset_sft_path`: SFT 格式 JSONL 文件保存路径（中间格式）
- `webcrawler.dataset_pt_path`: PT 格式 JSONL 文件保存路径（中间格式）
- `webcrawler.dataset_sft_mapped_path`: 映射后的 SFT 数据集文件路径
- `webcrawler.dataset_pt_mapped_path`: 映射后的 PT 数据集文件路径
- `webcrawler.dataset_mapping_results`: 数据集映射结果详情

### 4. 结束节点 (End Node)

**功能：** 生成任务摘要，返回结果到父图

**主要特性：**

- **结果汇总：** 生成任务执行摘要
  - 爬取页面数量统计
  - 整体概述和关键发现
  - 输出目录信息

- **消息更新：** 在消息列表末尾添加 `AIMessage`，包含任务执行摘要

- **导航信息：** 设置 `next_to` 为 `"query_node"`，用于返回到父图

**输出：**
- 更新 `messages` 列表，添加任务摘要
- 设置 `next_to` 为 `"query_node"`

## 📝 使用方法

### 基本用法

```python
from loopai.agents.WebCrawler import WebCrawlerAgent
from loopai.memory import checkpointer, store
from langchain_core.messages import HumanMessage

# 创建 WebCrawlerAgent 实例
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
    
    # API 配置（必需）
    'webcrawler': {
        'deepseek_api_key': 'your-deepseek-api-key',
        'tavily_api_key': 'your-tavily-api-key',
        'deepseek_api_base': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
    }
}

# 执行图
config = {"configurable": {"thread_id": "webcrawler_001"}}
result = graph.invoke(initial_state, config=config)

# 查看结果
webcrawler = result.get('webcrawler', {})
print(f"爬取页面数: {webcrawler.get('output_result', {}).get('total_pages', 0)}")
print(f"输出目录: {webcrawler.get('output_dir', '')}")

# 查看数据集生成结果
if 'dataset_summary' in webcrawler:
    print(f"数据集摘要: {webcrawler['dataset_summary']}")
    print(f"SFT 记录数: {webcrawler.get('dataset_sft_count', 0)}")
    print(f"PT 记录数: {webcrawler.get('dataset_pt_count', 0)}")
```

### 高级配置

```python
initial_state = {
    'messages': [HumanMessage(content="搜索机器学习模型训练最佳实践")],
    'output_dir': './output',
    
    'webcrawler': {
        # API 配置
        'deepseek_api_key': 'your-deepseek-api-key',
        'tavily_api_key': 'your-tavily-api-key',
        'deepseek_api_base': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
        'temperature': 0.7,
        
        # 查询生成配置
        'num_queries': 5,  # 生成的搜索查询数量
        
        # 爬取策略配置
        'max_pages': 100,  # 最大爬取页面数
        'crawl_depth': 3,  # 最大爬取深度
        'max_links_per_page': 5,  # 每页最大链接数
        'concurrent_pages': 3,  # 并发爬取页面数
        
        # 内容过滤配置
        'min_text_length': 500,  # 最小文本长度（字符）
        'min_code_length': 50,  # 最小代码长度（字符）
        'min_relevance_score': 6,  # 最小相关性分数（1-10）
        'url_patterns': None,  # URL 模式匹配规则（可选）
        
        # 运行时配置
        'request_delay': 2.0,  # 请求之间的延迟（秒）
        'timeout': 30,  # 请求超时时间（秒）
        'max_retries': 3,  # 最大重试次数
        
        # 输出配置
        'output_format': 'jsonl',  # 输出格式：'jsonl' 或 'json'
        'save_html': False,  # 是否保存 HTML 内容
        
        # 数据集生成配置
        'max_records_per_page': 100,  # 每个网页最多生成的记录数
        'dataset_concurrent_limit': 50,  # 数据集生成的并发限制
        'max_content_length': 50000,  # LLM 处理的每页内容最大字符数
        'debug': False,  # 是否启用调试模式
        
        # 格式映射配置（可选）
        'sft_mapping_format': 'jsonl_sft',  # SFT 数据映射格式
        'pt_mapping_format': 'jsonl_pt',  # PT 数据映射格式
    }
}
```

## 📊 状态字段说明

### 输入字段

所有配置参数都存储在 `webcrawler` 字典中：

| 字段名 | 类型 | 必需 | 默认值 | 说明 |
|-------|------|-----|--------|-----|
| `messages` | List[Message] | ✅ | - | 用户任务描述的消息列表 |
| `automated_query` | str | ❌ | - | 由大模型自动化生成的查询字符串 |
| `webcrawler.deepseek_api_key` | str | ✅ | - | DeepSeek API 密钥 |
| `webcrawler.tavily_api_key` | str | ✅ | - | Tavily API 密钥 |
| `webcrawler.deepseek_api_base` | str | ❌ | `https://api.deepseek.com/v1` | DeepSeek API 基础 URL |
| `webcrawler.model` | str | ❌ | `deepseek-chat` | 使用的模型名称 |
| `webcrawler.temperature` | float | ❌ | 0.7 | LLM 温度参数 |
| `webcrawler.num_queries` | int | ❌ | 5 | 生成的搜索查询数量 |
| `webcrawler.max_pages` | int | ❌ | 10000 | 最大爬取页面数 |
| `webcrawler.crawl_depth` | int | ❌ | 3 | 最大爬取深度 |
| `webcrawler.max_links_per_page` | int | ❌ | 5 | 每页最大链接数 |
| `webcrawler.concurrent_pages` | int | ❌ | 3 | 并发爬取页面数 |
| `webcrawler.min_text_length` | int | ❌ | 500 | 最小文本长度（字符） |
| `webcrawler.min_code_length` | int | ❌ | 50 | 最小代码长度（字符） |
| `webcrawler.min_relevance_score` | int/float | ❌ | 6/0.6 | 最小相关性分数（爬取阶段：1-10，数据集阶段：0.0-1.0） |
| `webcrawler.url_patterns` | str | ❌ | None | URL 模式匹配规则 |
| `webcrawler.request_delay` | float | ❌ | 2.0 | 请求之间的延迟（秒） |
| `webcrawler.timeout` | int | ❌ | 30 | 请求超时时间（秒） |
| `webcrawler.max_retries` | int | ❌ | 3 | 最大重试次数 |
| `webcrawler.output_format` | str | ❌ | `jsonl` | 输出格式：`jsonl` 或 `json` |
| `webcrawler.save_html` | bool | ❌ | False | 是否保存 HTML 内容 |
| `webcrawler.max_records_per_page` | int | ❌ | 10 | 每个网页最多生成的记录数 |
| `webcrawler.dataset_concurrent_limit` | int | ❌ | 5 | 数据集生成的并发限制 |
| `webcrawler.max_content_length` | int | ❌ | 50000 | LLM 处理的每页内容最大字符数 |
| `webcrawler.debug` | bool | ❌ | False | 是否启用调试模式 |
| `webcrawler.sft_mapping_format` | str | ❌ | `jsonl_sft` | SFT 数据映射格式 |
| `webcrawler.pt_mapping_format` | str | ❌ | `jsonl_pt` | PT 数据映射格式 |
| `output_dir` | str | ❌ | `./output` | 输出目录路径 |
| `prompt_template_dir` | str | ❌ | None | Prompt 模板目录路径 |

**注意：** 如果未设置 `webcrawler` 相关配置，系统会尝试从 `analyzer` 配置中获取：
- `webcrawler.model` ← `analyzer.analyze_model_path`
- `webcrawler.deepseek_api_base` ← `analyzer.analyze_base_url`
- `webcrawler.deepseek_api_key` ← `analyzer.analyze_api_key`

### 输出字段

WebCrawler Agent 执行完成后，会在 `webcrawler` 字典中添加以下字段：

| 字段名 | 类型 | 说明 |
|-------|------|-----|
| `webcrawler.output_result` | Dict[str, Any] | 完整的爬取结果，包含任务描述、运行 ID、时间戳、爬取数据列表、统计信息等 |
| `webcrawler.output_run_id` | str | 本次运行的唯一 ID |
| `webcrawler.output_dir` | str | 输出文件保存的目录路径 |
| `webcrawler.dataset_summary` | str | 数据集生成摘要信息 |
| `webcrawler.dataset_sft_count` | int | 生成的 SFT 格式记录数量 |
| `webcrawler.dataset_pt_count` | int | 生成的 PT 格式记录数量 |
| `webcrawler.dataset_sft_path` | str | SFT 格式 JSONL 文件保存路径（中间格式） |
| `webcrawler.dataset_pt_path` | str | PT 格式 JSONL 文件保存路径（中间格式） |
| `webcrawler.dataset_sft_mapped_path` | str | 映射后的 SFT 数据集文件路径 |
| `webcrawler.dataset_pt_mapped_path` | str | 映射后的 PT 数据集文件路径 |
| `webcrawler.dataset_mapping_results` | Dict | 数据集映射结果详情 |

**消息更新：**
- `messages`: 在消息列表末尾添加一个 `AIMessage`，包含任务执行摘要

**导航信息：**
- `next_to`: 设置为 `"query_node"`，用于返回到父图

**异常信息（如果发生错误）：**
- `exception`: 错误信息字符串
- `webcrawler.output_result`: 包含 `{"error": str}` 的错误结果

## 🛠️ 工具类

### CrawlOrchestrator

爬取流程编排器，负责管理整个爬取流程：

```python
from loopai.agents.WebCrawler.utils import CrawlOrchestrator

orchestrator = CrawlOrchestrator(
    deepseek_api_key="your-api-key",
    tavily_api_key="your-tavily-key",
    deepseek_api_base="https://api.deepseek.com/v1",
    model="deepseek-chat",
    max_pages=100,
    output_dir="./output",
    num_queries=5,
    crawl_depth=3,
    concurrent_pages=3,
)

result = await orchestrator.run("搜索 Python 异步编程")
```

### ContentAnalyzer

内容分析器，负责提取网页内容和代码块：

```python
from loopai.agents.WebCrawler.utils import ContentAnalyzer

analyzer = ContentAnalyzer()
content = await analyzer.extract_content(url)
code_blocks = extract_code_blocks_from_markdown(content)
```

### DatasetGenerator

数据集生成工具，提供 SFT/PT 记录生成和摘要生成功能：

```python
from loopai.agents.WebCrawler.utils.dataset_generator import (
    generate_sft_records,
    generate_pt_records,
    generate_webpage_summary_and_relevance,
)

# 生成 SFT 记录
sft_result = await generate_sft_records(
    llm=llm,
    prompt_loader=prompt_loader,
    user_query="Python 异步编程",
    webpage_title="标题",
    webpage_content="内容",
    webpage_url="https://example.com",
    code_blocks=[...],
    max_records=10,
)

# 生成 PT 记录
pt_result = await generate_pt_records(
    llm=llm,
    prompt_loader=prompt_loader,
    user_query="Python 异步编程",
    webpage_title="标题",
    webpage_content="内容",
    webpage_url="https://example.com",
    max_records=10,
)

# 生成摘要和相关性评分
summary_result = await generate_webpage_summary_and_relevance(
    llm=llm,
    user_query="Python 异步编程",
    webpage_title="标题",
    webpage_content="内容",
    webpage_url="https://example.com",
)
```

## 🚨 故障排除

### 常见问题

1. **API 密钥缺失**
   - 检查 DeepSeek API 密钥是否设置
   - 检查 Tavily API 密钥是否设置
   - 确认 API 密钥有效且有足够配额

2. **爬取失败**
   - 检查网络连接
   - 验证目标网站是否可访问
   - 检查 Playwright 浏览器是否已安装：`playwright install`
   - 确认系统依赖是否完整
   - 查看超时设置是否合理

3. **数据集生成失败**
   - 检查爬取节点是否成功生成数据
   - 验证 LLM API 是否可用
   - 确认内容长度是否超过限制
   - 查看相关性分数阈值是否设置过高

4. **格式映射错误**
   - 检查中间格式数据文件是否存在
   - 验证映射格式配置是否正确
   - 确认 Constructor 模块是否可用

5. **Playwright 问题**
   - 安装 Playwright 浏览器：`playwright install`
   - 检查系统依赖是否完整
   - 验证网页访问权限
   - 查看浏览器启动日志

### 日志分析

调试日志位于 `{output_dir}/webcrawler_output/run_{run_id}/logs/` 目录下，包含：
- 节点执行详情
- API 调用记录
- 错误堆栈信息
- 状态变更历史

启用调试模式：
```python
initial_state = {
    'webcrawler': {
        'debug': True,
        # ... 其他配置
    }
}
```

## 📈 功能特性

### 智能查询生成

- **多查询策略：** 根据用户查询自动生成多个搜索查询
- **查询优化：** 使用 LLM 优化查询表达，提高搜索准确性
- **可配置数量：** 支持自定义查询数量

### 深度网页爬取

- **多层级爬取：** 支持递归爬取链接页面
- **并发控制：** 智能并发管理，平衡效率和稳定性
- **内容提取：** 使用 Playwright 支持 JavaScript 渲染页面
- **代码块识别：** 自动识别和提取多种格式的代码块

### 智能数据集生成

- **格式自适应：** 根据网页内容自动选择 SFT 或 PT 格式
- **质量过滤：** 基于相关性分数过滤低质量数据
- **摘要生成：** 为高质量网页生成摘要和相关性评分
- **格式映射：** 自动将中间格式映射为目标训练格式

### 内容质量保证

- **长度过滤：** 过滤过短的文本和代码块
- **相关性评分：** 基于用户查询的相关性评分（0-10 分）
- **URL 模式匹配：** 支持基于 URL 模式的内容过滤

## 🎯 最佳实践

### 查询优化

1. **明确任务目标**
   - 在查询中包含具体的技术领域或主题
   - 说明需要的数据类型（代码示例、教程、文档等）
   - 避免过于宽泛的查询

2. **合理设置爬取参数**
   - 根据数据源丰富度调整 `max_pages`
   - 深度爬取时合理设置 `crawl_depth` 和 `concurrent_pages`
   - 根据网络情况调整 `timeout` 和 `request_delay`

3. **内容过滤配置**
   - 根据目标数据质量要求设置 `min_text_length` 和 `min_code_length`
   - 使用 `url_patterns` 过滤特定域名或路径
   - 合理设置 `min_relevance_score` 平衡数据量和质量

### 数据集生成优化

1. **并发配置**
   - 根据 LLM API 限制设置 `dataset_concurrent_limit`
   - 避免过高的并发导致 API 限流
   - 监控 API 使用情况

2. **内容长度控制**
   - 根据 LLM 上下文窗口设置 `max_content_length`
   - 避免内容过长导致生成失败
   - 考虑使用内容摘要或分块处理

3. **记录数量控制**
   - 根据网页内容质量设置 `max_records_per_page`
   - 避免生成过多低质量记录
   - 平衡数据量和数据质量

### 输出管理

1. **目录组织**
   - 使用有意义的 `output_dir` 路径
   - 定期清理旧的输出文件
   - 为不同任务使用不同的 `task_id`

2. **文件格式选择**
   - 使用 `jsonl` 格式便于流式处理
   - 需要完整 JSON 结构时使用 `json` 格式
   - 根据下游任务选择合适的数据格式

3. **结果验证**
   - 检查生成的数据集文件
   - 验证数据格式是否符合预期
   - 查看摘要文件了解数据来源和质量

## 📚 扩展开发

### 添加新的搜索引擎

在 `utils/crawl_orchestrator.py` 中扩展 `CrawlOrchestrator` 类，添加新的搜索引擎支持。

### 添加新的内容提取方法

1. 在 `utils/content_analyzer.py` 中添加新的提取方法
2. 在 `CrawlOrchestrator` 中集成新的提取逻辑
3. 更新内容分析流程

### 添加新的数据集格式

1. 在 `utils/dataset_generator.py` 中添加新的格式生成函数
2. 更新 `webcrawler_dataset_node.py` 支持新格式
3. 添加相应的格式映射配置

### 自定义代码块提取

在 `utils/crawl_orchestrator.py` 中扩展 `extract_code_blocks_from_markdown` 函数，支持更多代码块格式。

### 自定义相关性评分算法

在 `utils/dataset_generator.py` 中扩展 `generate_webpage_summary_and_relevance` 函数，实现自定义评分逻辑。

---

💡 **提示：** 查看 `examples/scripts/test_webcrawler_dataset.py` 了解完整的使用示例。

## 📄 输出文件

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

- `webpage_summaries_{timestamp}.jsonl`: 网页摘要和相关性评分文件
  - 格式：包含生成了 SFT 记录的网页摘要和相关性评分
  - 每条记录包含：`{"url": "...", "title": "...", "summary": "...", "relevance_score": 8}`
  - 相关性评分范围：0-10 分，表示与用户查询的相关程度

### 数据集格式示例

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

**注意：**
- 只有成功生成了 SFT 记录的网页才会出现在摘要文件中
- `relevance_score` 范围是 0-10，表示与用户查询的相关程度
- 摘要文件可以帮助识别哪些网页贡献了高质量的 SFT 训练数据
