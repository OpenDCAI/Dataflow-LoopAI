# Obtainer Agent 使用指南

Obtainer Agent 是 Dataflow-LoopAI 框架中负责数据获取的智能代理。它能够自动化完成从查询理解到数据下载、格式转换的完整流程，支持多种数据源和智能格式映射。

## 🏗️ 架构设计

Obtainer Agent 采用多阶段顺序执行架构：

```
查询规范化 → 网络搜索 → 下载执行 → 后处理 → 格式映射(可选) → 结束
    ↓           ↓          ↓         ↓          ↓           ↓
  结束        结束       结束      结束       结束        结束
```

### 1. 启动节点 (Start Node)

**功能：** 初始化配置参数，智能分类任务类型，规范化用户查询

**主要特性：**

- **智能查询规范化：** 自动识别评估式查询并转换为数据集请求
  - 例如："评估模型需要什么数据" → "获取用于模型评估的数据集"
  
- **自动任务分类：** 使用 LLM 智能判断任务类型
  - **PT (Pre-training)：** 预训练任务，用于语言模型预训练
  - **SFT (Supervised Fine-tuning)：** 监督微调任务，用于指令微调、问答等

- **配置初始化：** 自动设置默认参数和 API 密钥

**输出：**
- `obtainer.category`: 任务类别（PT/SFT）
- `obtainer.normalized_query`: 规范化后的查询
- `obtainer.intent_type`: 查询意图类型

### 2. 网络搜索节点 (Web Search Node)

**功能：** 执行网络搜索，存储内容到 RAG，生成下载任务

**主要特性：**

- **多搜索引擎支持：**
  - Tavily API（默认）
  - 可扩展其他搜索引擎

- **RAG 集成：** 自动将搜索结果存储到向量数据库
  - 支持独立 RAG 配置（API、嵌入模型）
  - 持久化存储，支持增量更新

- **智能 URL 选择：** 基于相关性评分选择最佳 URL

- **深度探索：** 支持网页森林探索（Forest Exploration）
  - 可配置探索深度、并发限制、超时时间

- **下载任务生成：** 自动分析搜索结果，生成下载子任务
  - 识别 HuggingFace 数据集
  - 识别 Kaggle 数据集
  - 识别网页数据源

**输出：**
- `obtainer.subtasks`: 下载任务列表
- `obtainer.research_summary`: 研究摘要
- `obtainer.urls_visited`: 访问的 URL 列表

### 3. 下载节点 (Download Node)

**功能：** 执行下载任务，支持多种数据源

**支持的下载方式（按优先级）：**

1. **HuggingFace 数据集**
   - 自动识别 HuggingFace 数据集链接
   - 支持完整数据集下载
   - 支持数据集子集选择

2. **Kaggle 数据集**
   - 自动识别 Kaggle 数据集链接
   - 需要配置 Kaggle API 密钥
   - 支持竞赛数据集和公开数据集

3. **网页爬取**
   - 使用 Playwright 进行网页内容抓取
   - 支持 JavaScript 渲染页面
   - 智能提取结构化数据

**智能决策：** 使用 LLM 自动选择最佳下载方法

**输出：**
- `obtainer.subtasks`: 更新任务状态（completed_successfully/failed_to_download）
- `obtainer.download_results`: 下载结果详情

### 4. 后处理节点 (Post-process Node)

**功能：** 将下载的数据集转换为 PT/SFT 格式

**主要特性：**

- **格式转换：** 根据任务类别自动转换数据格式
  - **PT 格式：** 纯文本格式，用于预训练
  - **SFT 格式：** 指令-输出格式，用于微调

- **数据清洗：** 自动处理重复、空值、格式错误

- **批量处理：** 支持多文件批量转换

- **中间数据保存：** 保存转换后的中间格式数据

**输出：**
- `obtainer.intermediate_data_path`: 中间数据文件路径
- `obtainer.postprocess_results`: 后处理结果统计

### 5. 格式映射子图 (Mapping Subgraph)

**功能：** 将中间格式数据映射为目标格式（可选）

**工作流程：**

1. **入口检查：** 检查是否有默认格式配置
2. **格式询问：** 询问用户需要的目标格式
3. **格式列表：** 显示所有预设格式详情
4. **格式选择：** 选择预设格式或自定义格式
5. **格式确认：** 确认选择的格式
6. **格式映射：** 执行格式转换
   - 预设格式：使用脚本映射（快速）
   - 自定义格式：使用 LLM 映射（灵活）
7. **结果总结：** 生成映射结果摘要

**支持的预设格式：**
- Alpaca（指令微调）
- ChatML（对话格式）
- 更多格式可扩展

**输出：**
- `obtainer.confirmed_format`: 确认的目标格式
- `obtainer.mapping_results`: 映射结果详情，通常包含最终数据文件路径

## 📝 使用方法

### 基本用法

```python
from loopai.agents import ObtainerAgent
from loopai.memory import checkpointer, store

# 创建 ObtainerAgent 实例
obtainer = ObtainerAgent(
    checkpointer=checkpointer,
    store=store,
    model_name="qwen2.5-7b-instruct",
    base_url="http://localhost:8000/v1",
    api_key="your-api-key"
)

# 准备获取状态
obtainer_state = {
    # 必需字段
    'automated_query': '获取用于训练中文对话模型的数据集',

    # Obtainer 配置
    'obtainer': {
        'category': 'SFT',  # 或 'PT'，不设置则自动分类
        'model_path': 'qwen2.5-7b-instruct',
        'base_url': 'http://localhost:8000/v1',
        'api_key': 'your-api-key',
        'search_engine': 'tavily',
        'max_urls': 10,
    },
    'output_dir': './output/obtainer'
}

# 构建并执行图
config = {"configurable": {"thread_id": "my_obtainer_task"}}
graph = obtainer()
result = graph.invoke(obtainer_state, config=config)
```

### 高级配置

```python
# 使用自定义 RAG 配置
obtainer_state = {
    'automated_query': '获取数学推理数据集',
    'obtainer': {
        'category': 'SFT',
        'reset_rag': True,  # 重置 RAG 数据库
        'rag_embed_model': 'text-embedding-3-large',
        'rag_collection_name': 'math_datasets',
        'rag_api_base_url': 'http://localhost:8000/v1',
        'rag_api_key': 'your-embedding-api-key',

        # 深度探索配置
        'max_depth': 3,
        'concurrent_limit': 5,
        'topk_urls': 10,
        'url_timeout': 120,

        # 格式映射配置
        'default_mapping_format': 'alpaca',  # 跳过用户交互，直接使用 Alpaca 格式
    },
}

# Kaggle 配置
obtainer_state['obtainer'].update({
    'kaggle_username': 'your-kaggle-username',
    'kaggle_key': 'your-kaggle-key',
})

# Tavily API 配置
obtainer_state['obtainer'].update({
    'tavily_api_key': 'your-tavily-api-key',
    # 或设置环境变量 TAVILY_API_KEY
})
```

## 📊 状态字段说明

### 输入字段

| 字段名 | 类型 | 必需 | 默认值 | 说明 |
|-------|------|-----|--------|-----|
| `automated_query` | str | ✅ | - | 用户查询或任务描述 |
| `obtainer.category` | str | ❌ | 自动检测 | 任务类别：PT 或 SFT |
| `obtainer.model_path` | str | ✅ | - | LLM 模型名称；为空时可从 Analyzer/Starter 配置继承 |
| `obtainer.base_url` | str | ✅ | - | LLM API 基础 URL |
| `obtainer.api_key` | str | ✅ | - | LLM API 密钥 |
| `obtainer.temperature` | float | ❌ | 0.7 | LLM 温度参数 |
| `obtainer.search_engine` | str | ❌ | tavily | 搜索引擎类型 |
| `obtainer.max_urls` | int | ❌ | 10 | 最大搜索 URL 数量 |
| `obtainer.max_depth` | int | ❌ | 2 | 深度探索最大深度 |
| `obtainer.concurrent_limit` | int | ❌ | 5 | 并发探索限制 |
| `obtainer.topk_urls` | int | ❌ | 3 | 每页选择 Top-K URL |
| `obtainer.url_timeout` | int | ❌ | 30 | URL 探索超时时间（秒） |
| `obtainer.tavily_api_key` | str | 按需 | - | Tavily API 密钥；也可用环境变量 `TAVILY_API_KEY` |
| `obtainer.kaggle_username` | str | 按需 | - | Kaggle 用户名；也可用环境变量 `KAGGLE_USERNAME` |
| `obtainer.kaggle_key` | str | 按需 | - | Kaggle API 密钥；也可用环境变量 `KAGGLE_KEY` |
| `obtainer.reset_rag` | bool | ❌ | True | 是否重置 RAG 数据库 |
| `obtainer.rag_embed_model` | str | 按需 | - | RAG 嵌入模型名称 |
| `obtainer.rag_collection_name` | str | ❌ | rag_collection | RAG 集合名称 |
| `obtainer.rag_api_base_url` | str | 按需 | - | RAG API 基础 URL |
| `obtainer.rag_api_key` | str | 按需 | - | RAG API 密钥 |
| `obtainer.default_mapping_format` | str | ❌ | 空 | 默认映射格式（非空则跳过用户交互） |
| `obtainer.debug` | bool | ❌ | False | 是否启用调试模式 |
| `output_dir` | str | ❌ | ./output | 输出目录 |

### 输出字段

| 字段名 | 类型 | 说明 |
|-------|------|-----|
| `obtainer.category` | str | 检测到的任务类别 |
| `obtainer.normalized_query` | str | 规范化后的查询 |
| `obtainer.intent_type` | str | 查询意图类型 |
| `obtainer.subtasks` | list | 下载任务列表 |
| `obtainer.research_summary` | str | 研究摘要 |
| `obtainer.urls_visited` | list | 访问的 URL 列表 |
| `obtainer.download_results` | dict | 下载结果详情 |
| `obtainer.intermediate_data_path` | str | 中间数据文件路径 |
| `obtainer.postprocess_results` | dict | 后处理结果统计 |
| `obtainer.confirmed_format` | dict | 确认的目标格式 |
| `obtainer.mapping_results` | dict | 映射结果详情，通常包含最终数据文件路径 |

## 🛠️ 工具类

### CategoryClassifier

```python
from loopai.agents.Obtainer.utils import CategoryClassifier

classifier = CategoryClassifier(
    model_name="qwen2.5-7b-instruct",
    base_url="http://localhost:8000/v1",
    api_key="your-api-key"
)

category = await classifier.classify_category(
    user_query="训练一个中文问答模型",
    objective="获取训练数据"
)
print(f"任务类别: {category}")  # 输出: SFT
```

### ObtainQueryNormalizer

```python
from loopai.agents.Obtainer.utils import ObtainQueryNormalizer

normalizer = ObtainQueryNormalizer(
    model_name="qwen2.5-7b-instruct",
    base_url="http://localhost:8000/v1",
    api_key="your-api-key"
)

result = await normalizer.normalize(
    user_query="评估模型需要什么数据",
    objective="模型评估"
)
print(f"规范化查询: {result['normalized_query']}")
```

### RAGManager

```python
from loopai.agents.Obtainer.utils import RAGManager

rag_manager = RAGManager(
    api_base_url="http://localhost:8000/v1",
    api_key="your-api-key",
    embed_model="text-embedding-3-large",
    persist_directory="./output/rag_db",
    reset=False,
    collection_name="datasets"
)

# 添加文档
rag_manager.add_documents(["文档内容1", "文档内容2"])

# 搜索相似文档
results = rag_manager.search("查询文本", top_k=5)
```

### HuggingFaceManager

```python
from loopai.agents.Obtainer.utils import HuggingFaceManager

hf_manager = HuggingFaceManager()
result = hf_manager.download_dataset(
    dataset_name="squad",
    output_dir="./downloads",
    subset=None
)
```

### KaggleManager

```python
from loopai.agents.Obtainer.utils import KaggleManager

kaggle_manager = KaggleManager(
    username="your-username",
    key="your-key"
)
result = kaggle_manager.download_dataset(
    dataset_name="titanic",
    output_dir="./downloads"
)
```

## 🚨 故障排除

### 常见问题

1. **API 密钥缺失**
   - 检查 Tavily API 密钥是否设置（环境变量或状态字段）
   - 检查 Kaggle 凭证是否配置
   - 确认 LLM API 密钥有效

2. **下载失败**
   - 检查网络连接
   - 验证数据集名称是否正确
   - 确认 Kaggle API 密钥权限
   - 检查磁盘空间是否充足

3. **RAG 初始化失败**
   - 确认嵌入模型 API 可用
   - 检查输出目录写入权限
   - 验证 RAG API 配置正确

4. **格式转换错误**
   - 检查原始数据格式是否符合预期
   - 验证任务类别（PT/SFT）是否正确
   - 查看后处理日志了解详细错误

5. **Playwright 问题**
   - 安装 Playwright 浏览器：`playwright install`
   - 检查系统依赖是否完整
   - 验证网页访问权限

### 日志分析

调试日志位于 `{output_dir}/obtainer_logs/obtainer_debug_{timestamp}.log`，包含：
- 节点执行详情
- API 调用记录
- 错误堆栈信息
- 状态变更历史

启用调试模式：
```python
obtainer_state = {
    'obtainer': {
        'debug': True,
    },
    # ... 其他配置
}
```

## 📈 功能特性

### 智能查询理解

- **查询规范化：** 自动将评估式、推荐式查询转换为数据集请求
- **意图识别：** 智能识别用户真实意图
- **上下文理解：** 结合对话历史理解查询

### 多源数据获取

- **HuggingFace：** 支持 50,000+ 数据集
- **Kaggle：** 支持公开数据集和竞赛数据
- **网页爬取：** 支持动态网页内容提取

### 智能格式转换

- **自动分类：** PT/SFT 任务自动识别
- **格式适配：** 根据任务类型自动选择格式
- **灵活映射：** 支持预设格式和自定义格式

### RAG 增强搜索

- **向量存储：** 搜索结果持久化存储
- **语义检索：** 基于向量相似度检索
- **增量更新：** 支持增量添加新内容

## 🎯 最佳实践

### 查询优化

1. **明确任务类型**
   - 明确指定 PT 或 SFT 类别，避免自动分类错误
   - 在查询中包含任务类型关键词

2. **详细描述需求**
   - 包含领域信息（如"中文"、"数学"）
   - 说明数据规模要求
   - 提及数据格式偏好

3. **使用规范化查询**
   - 直接使用数据集请求而非评估式查询
   - 避免模糊表述

### 配置优化

1. **RAG 配置**
   - 根据任务选择合适的嵌入模型
   - 定期重置 RAG 数据库避免数据过期
   - 使用独立的 RAG API 配置提高性能

2. **搜索参数**
   - 根据数据源丰富度调整 `max_urls`
   - 深度探索时合理设置 `max_depth` 和 `concurrent_limit`
   - 根据网络情况调整 `url_timeout`

3. **下载配置**
   - 提前配置 Kaggle 凭证
   - 设置合适的下载目录
   - 监控磁盘空间

### 数据质量

1. **数据验证**
   - 检查下载数据的完整性
   - 验证数据格式是否符合预期
   - 查看后处理统计信息

2. **格式选择**
   - 根据下游任务选择合适格式
   - 使用预设格式提高效率
   - 自定义格式时提供清晰示例

3. **结果检查**
   - 查看中间数据文件
   - 验证最终数据格式
   - 检查数据样本质量

## 📚 扩展开发

### 添加新的搜索引擎

在 `websearch_node.py` 中扩展 `WebTools` 类，添加新的搜索引擎支持。

### 添加新的下载方式

1. 在 `utils/` 目录创建新的管理器类
2. 在 `download_node.py` 中集成新的下载方法
3. 更新 `DownloadMethodDecisionAgent` 支持新方法

### 添加新的数据格式

1. 在 `tools/format_mapping_tools.py` 中添加预设格式
2. 或使用 LLM 映射支持自定义格式
3. 更新 `postprocess_node.py` 支持新格式转换

### 自定义 RAG 后端

在 `utils/rag_manager.py` 中扩展 `RAGManager` 类，支持其他向量数据库。

---

💡 **提示：** 查看 `examples/scripts/run_obtainer.py` 了解完整的使用示例。
