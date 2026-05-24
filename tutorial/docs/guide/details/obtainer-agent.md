# Obtainer Agent 详细指南

`ObtainerAgent` 负责根据已有问题诊断去获取更合适的数据。

## 核心职责

- 根据分析结论获取候选数据
- 为后续数据处理准备原始样本

## 进入它之前通常要准备什么

更理想的前置条件是：

- 已经有分析报告
- 已经明确想补什么类型的数据
- 检索、模型或外部资源配置已经到位

## 关键配置

Obtainer 的配置通常写在 `state.obtainer` 或 `starter.yaml` 的 `default_states.obtainer` 中。

| 字段 | 作用 |
| --- | --- |
| `model_path` / `base_url` / `api_key` | 调用 OpenAI-compatible 聊天模型，用于查询理解、URL 选择、下载决策和格式映射 |
| `search_engine` / `tavily_api_key` | 配置网页搜索；Tavily key 也可以通过 `TAVILY_API_KEY` 提供 |
| `kaggle_username` / `kaggle_key` | 配置 Kaggle 数据集下载；也可以使用 `KAGGLE_USERNAME` / `KAGGLE_KEY` |
| `rag_api_base_url` / `rag_api_key` / `rag_embed_model` | 配置 RAG 嵌入模型；为空时通常复用 Obtainer 的模型服务配置 |
| `max_urls` / `max_depth` / `concurrent_limit` / `topk_urls` / `url_timeout` | 控制搜索与网页探索范围 |
| `category` | 数据类别，通常为 `PT` 或 `SFT` |
| `default_mapping_format` | 非空时可跳过格式确认，直接进入预设格式映射 |

如果要使用网页抓取或 Kaggle 流程，除 Python 依赖外还需要在主环境中执行一次 `playwright install`。

## 它的输入和输出可以怎么理解

输入通常是：

- 问题模式
- 数据需求描述
- 检索相关配置

输出通常是：

- 候选数据
- 原始样本集合
- 可供 Constructor 继续处理的数据结果

## 在闭环中的位置

Obtainer 处在“发现问题之后，生成训练数据之前”的数据获取环节。

## 使用时最该关注什么

- 获取的数据是否真的对症
- 数据量是否足够
- 是否还需要引入网页抓取等额外来源
