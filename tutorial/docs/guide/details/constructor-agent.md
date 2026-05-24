# Constructor Agent 详细指南

`ConstructorAgent` 负责把原始候选数据整理成更适合训练的数据。它位于数据获取之后、模型训练之前，是把“可用原料”变成“可训练样本”的关键一层。

## 核心职责

- 数据清洗
- 数据合成
- 去重与筛选

## 进入它之前通常要准备什么

通常需要具备以下输入：

- `Obtainer` 或 `WebCrawler` 输出的原始数据
- 处理策略或目标格式
- 输出路径

## 关键配置

Constructor 的配置通常写在 `state.constructor` 或 `starter.yaml` 的 `default_states.constructor` 中。

如果主流程里部分字段为空，Constructor 会从 `state.obtainer` 中兼容继承同名字段。

| 字段 | 作用 |
| --- | --- |
| `model_path` / `base_url` / `api_key` | 调用 OpenAI-compatible 聊天模型，用于清洗规划、自定义格式映射和 CoT 处理。 |
| `category` | 数据类别，通常为 `PT` 或 `SFT`。 |
| `download_dir` / `intermediate_data_path` / `output_dir` | 指定下载数据、清洗中间数据和输出目录。 |
| `postprocess_version` | 选择后处理路径，默认值为 `agent_v2`。 |
| `max_samples_before_cleaning` / `cleaning_random_seed` | 控制清洗前采样规模和可复现性。 |
| `llm_timeout` / `max_retries` / `max_concurrent_mapping` | 控制 LLM 调用、重试和映射并发。 |
| `default_mapping_format` | 非空时可跳过格式确认，直接进入预设格式映射。 |
| `benchmark_source_dir` / `benchmark_pool_path` / `benchmark_pool_size` | 配置 benchmark-aware 清洗和采样池。 |

## 它的输入和输出可以怎么理解

输入通常包括：

- 原始数据
- 处理规则
- 数据构造目标

输出通常包括：

- 更干净、更一致的数据集
- 可直接提供给 `Trainer` 使用的训练数据

## 在闭环中的位置

Constructor 是“原始数据”到“训练数据”之间的桥梁。

在闭环里，它通常位于：

```text
Analyzer -> Obtainer / WebCrawler -> Constructor -> Trainer
```

前面的节点负责发现问题和获取数据，Constructor 负责把这些数据处理成真正可用于训练的样本。

## 使用时最该关注什么

- 清洗是否充分
- 去重是否有效
- 数据是否已经适合进入训练阶段
- 输出格式是否与下游训练框架兼容
- 中间过程是否保留了足够的可追溯信息
