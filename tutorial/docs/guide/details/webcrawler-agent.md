# WebCrawler Agent 详细指南

`WebCrawlerAgent` 是偏网页抓取方向的数据获取节点，可以看作 `Obtainer` 在开放网页场景下的补充与扩展。

## 核心职责

- 从网页或在线资源中抓取可用数据
- 扩展 `Obtainer` 的外部数据来源

## 进入它之前通常要准备什么

通常需要先想清楚以下几件事：

- 已经明确要补什么类型的数据，以及对应评测领域（与 Judger / Analyzer 的 `eval_task_type` / `analyze_task_type` 对齐，常见取值为 `code`、`text2sql`、`general_text`）
- 有一份尽量结构化的任务描述（写在 `messages` 或 `automated_query` 中；闭环场景可参考 `examples/scripts/run_webcrawler.py` 的四段式写法）
- 如果 WebCrawler 接在 `Analyzer` 之后，通常可以把 Analyzer 报告直接作为任务描述。

## 关键配置

WebCrawler 的配置通常写在 `state.webcrawler` 或 `starter.yaml` 的 `default_states.webcrawler` 中。

| 字段 | 作用 |
| --- | --- |
| `deepseek_api_key` / `deepseek_api_base` / `model` / `temperature` | 调用 OpenAI-compatible 聊天模型，用于搜索查询生成、网页相关性判断、SFT/PT 抽取与摘要评分。未填时，数据集生成阶段会尝试从 `analyzer.analyze_*` 继承。 |
| `tavily_api_key` | 配置 Tavily 网页搜索；也可通过 `TAVILY_API_KEY` 环境变量提供。 |
| `num_queries` / `max_pages` / `crawl_depth` / `max_links_per_page` / `concurrent_pages` | 控制搜索与爬取范围（`start_node` 默认分别为 `5` / `10000` / `3` / `5` / `3`）。 |
| `min_text_length` / `min_code_length` / `min_relevance_score` / `url_patterns` | 控制正文、代码块与网页相关性过滤。 |
| `request_delay` / `timeout` / `max_retries` | 控制爬取节奏与容错。 |
| `output_format` / `save_html` | 控制爬取结果落地格式（`jsonl` / `json`）。 |
| `max_records_per_page` / `dataset_concurrent_limit` / `max_content_length` | 控制数据集生成规模与 LLM 单页输入长度。 |
| `sft_mapping_format` / `pt_mapping_format` | 中间格式映射目标（Constructor `FORMAT_MAPPERS` 的 key，如 `alpaca`、`chatml`、`jsonl_sft`、`jsonl_pt`）。 |
| `debug` | 开启更详细的调试日志。 |


### 各阶段必填字段

| 阶段 | 必填 | 说明 |
| --- | --- | --- |
| 全局 | `messages` 或 `automated_query` | 作为爬取与数据集生成的任务描述。注意：`crawl_node` 优先读 `messages` 最后一条，再回退 `automated_query`；`webcrawler_dataset_node` 则优先 `automated_query`。闭环场景建议两处保持一致。 |
| 全局 | `output_dir` | 可选，默认 `./output`；爬取结果写入 `{output_dir}/webcrawler_output/`，数据集写入 `{output_dir}/webcrawler_dataset/`。 |
| `start_node` | `deepseek_api_key`、`tavily_api_key` | 缺失时写入 `state.exception`，后续节点跳过。 |
| `webcrawler_dataset_node` | `model`、`deepseek_api_base`、`deepseek_api_key` | 可由 `webcrawler` 或 `analyzer` 配置合并满足。 |


## 它的输入和输出可以怎么理解

输入通常包括：

- 抓取目标
- 查询主题
- 访问策略

输出通常包括：

- 抓取到的网页内容
- 可继续进入 `Constructor` 的原始文本或样本

## 在闭环中的位置

WebCrawler 可以看作 `Obtainer` 的一个外部数据扩展分支。

在闭环中，它通常处于：

```text
Analyzer -> Obtainer / WebCrawler -> Constructor -> Trainer
```

当已有数据源不足，或者需要从公开网页补充信息时，WebCrawler 就会发挥作用。

## 使用时最该关注什么

- 来源是否可靠
- 抓取内容是否相关
- 是否需要较强的后处理来清洗内容
- 站点访问限制是否会影响抓取
- 抓取结果是否便于后续格式化和训练使用
