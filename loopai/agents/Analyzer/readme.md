# Analyzer Agent说明文档

Analyzer 是 Dataflow-LoopAI 中用于 **模型评测结果分析、指标计算和结论生成** 的分析型 Agent。  
该模块主要读取 Judger 产出的评测结果，或读取用户指定的评测结果文件，在不重新生成模型回答的前提下，对已有评测结果进行样本级错误分析、指标计算、报告生成和改进建议总结。

Analyzer 主要面向两类场景：

- **代码生成（code）/ Text-to-SQL（text2sql）**：读取已有 OJ / SQL 执行评测结果，分析失败样本、错误阶段和模型能力短板。
- **通用文本评测（general_text）**：基于 bench 信息和 DataFlow / One-Eval 结果，自动推荐指标、计算指标，并生成指标分析报告。

Analyzer 强调评测结果的 **可解释性、系统性、可复用性和报告自动化**。

---

## 一、功能概览

Analyzer 提供以下核心能力：

- 参数检查与任务路由：根据 `analyze_task_type` 自动选择分析链路。
- 样本级失败原因分析：对 code / text2sql 的失败样本进行错误阶段、错误类型和关键失败因素分析。
- 全局评测结果总结：统计通过率、失败类型分布、错误阶段分布等信息。
- 通用文本指标推荐：根据 bench_name / eval_type 自动推荐合适的评测指标。
- 通用文本指标计算：调用 MetricRunner 对评测结果进行指标计算。
- 指标报告生成：根据 metric 结果生成自然语言评测报告与数据构造建议。
- 最终结论生成：整合 summary、失败样本和分析结果，输出最终评测结论与可选改进建议。
- 输出结构化 JSON 报告与可读文本报告。

---

## 二、Pipeline 结构

Analyzer 不是单一线性流程，而是根据 `analyze_task_type` 分为两条链路。

### 1. code / text2sql 分析链路

当 `analyzer.analyze_task_type` 为 `code` 或 `text2sql` 时，Analyzer 执行原有评测结果分析链：

```text
check_required_fields
        ↓
route_eval
        ↓
eval_model_node
        ↓
analyze_result_node
        ↓
draw_conclusion_node
        ↓
finish
```

该链路主要用于分析已有代码生成或 Text-to-SQL 评测结果。

---

### 2. general_text 指标评测链路

当 `analyzer.analyze_task_type` 为 `general_text` 或其他通用文本任务时，Analyzer 不进入 `eval_model_node`，而是进入 metric 分析链：

```text
check_required_fields
        ↓
route_eval
        ↓
metric_recommend_node
        ↓
metric_score_node
        ↓
analyze_metric_report_node
        ↓
finish
```

该链路主要用于通用文本任务的指标推荐、指标计算和指标报告生成。

---

## 三、Node 功能说明

### 1️⃣ check_required_fields（参数验证）

🟢 输入
- `state["analyzer"]` 中的 Analyzer 配置。
- `state["judger"]` 中可能已经存在的 Judger 输出结果。
- `state["bench"]` 中可能存在的通用文本 bench 信息。

🟡 功能
- 检查通用必填参数是否存在。
- 检查 `analyze_task_type` 是否存在。
- 对 `code` / `text2sql` 检查分析模型配置与评测结果路径。
- 对 `general_text` 检查是否存在 bench 或评测结果路径。
- 如果缺少必要参数，则写入 `state["exception"]`、`state["configer"]["configer_error"]`，并跳转到 `config_node`。

🔵 输出
- 参数完整时进入 `route_eval`。
- 参数缺失时进入配置补全流程。

---

### 2️⃣ route_eval（任务路由）

🟢 输入
- `analyzer.analyze_task_type`

🟡 功能
- 如果任务类型为 `code` 或 `text2sql`，跳转到 `eval_model_node`。
- 如果任务类型为 `general_text` 或其他通用文本任务，跳转到 `metric_recommend_node`。

🔵 输出
- 路由后的下一个节点。

---

### 3️⃣ eval_model_node（样本级评测结果分析）

🟢 输入
- `analyzer.analyze_task_type`：任务类型，支持 `code`、`text2sql`。
- `judger.output_result_path` 或 `analyzer.eval_result_path`：已有评测结果 jsonl 文件。
- 分析模型配置：`analyze_model_path`、`analyze_base_url`、`analyze_api_key`、`analyze_temperature`、`analyze_top_p` 等。
- 分析控制参数：`analyze_batch_size`、`quick_brief`、`quick_brief_limit` 等。

🟡 功能
- 读取已有评测结果文件。
- 过滤失败样本。
- 对失败样本进行 LLM + 规则融合分析。
- 判断失败阶段、错误类型、失败原因与关键证据。
- 可选生成快速中文短评。
- 生成增强版评测记录与评测摘要。

🔵 输出
- `analyzer.analyze_output_result_path`：增强版评测记录 jsonl 路径。
- `analyzer.analyze_output_summary_path`：评测摘要 JSON 路径。
- `analyzer.analyze_output_summary_txt_path`：评测摘要文本路径（如生成）。

---

### 4️⃣ analyze_result_node（全局结果分析）

🟢 输入
- `analyzer.analyze_output_summary_path`
- `analyzer.analyze_output_result_path`
- `analyzer.analyze_sampling_top_k`
- 分析模型配置。

🟡 功能
- 读取 `eval_model_node` 生成的 summary 与增强版样本记录。
- 汇总整体评测表现。
- 抽取代表性失败样例。
- 调用分析模型生成全局评测分析。
- 从模型能力与数据 / 评测策略两个角度总结问题。

🔵 输出
- `analyzer.analyze_output_report_json_path`：结构化分析报告 JSON 路径。
- `analyzer.analyze_output_report_text_path`：自然语言分析报告文本路径。

---

### 5️⃣ draw_conclusion_node（最终结论生成）

🟢 输入
- `analyzer.analyze_output_summary_path`
- `analyzer.analyze_output_result_path`
- `analyzer.analyze_output_report_json_path`
- `quick_brief_limit`
- `output_suggestion`

🟡 功能
- 读取评测摘要与样本级结果。
- 生成最终评测报告。
- 汇总模型整体能力、主要失败类型和风险点。
- 可选生成模型改进建议。
- 可选生成数据构造 / 优化建议。

🔵 输出
- `analyzer.analyze_output_report_json_path`：最终报告 JSON 路径。
- `analyzer.analyze_output_report_text_path`：最终报告文本路径。
- `analyzer.analyze_output_suggestion_path`：改进建议文本路径（当 `output_suggestion=True` 时）。

---

### 6️⃣ metric_recommend_node（通用文本指标推荐）

🟢 输入
- `state["bench"]` 或 `state["judger"]["bench"]`
- `analyzer.bench_name`
- `analyzer.bench_dataflow_eval_type`

🟡 功能
- 优先根据 `bench_name` 从 metric registry / dispatcher 中推荐指标。
- 如果未命中，则根据 `bench_dataflow_eval_type` 使用 fallback 规则推荐指标。
- 将推荐结果写入 `state["analyzer"]["metric_plan"]` 与 `state["metric_plan"]`。

🔵 输出
- `analyzer.metric_plan`：指标推荐方案。

示例：

```json
{
  "general_text_eval": [
    {"name": "exact_match", "priority": "primary"},
    {"name": "token_f1", "priority": "secondary"},
    {"name": "extraction_rate", "priority": "diagnostic"}
  ]
}
```

---

### 7️⃣ metric_score_node（通用文本指标计算）

🟢 输入
- `state["bench"]` 或 `state["judger"]["bench"]`
- `analyzer.metric_plan`
- `judger.output_pred_path` / `judger.output_result_path` / `analyzer.analyze_output_result_path` / `analyzer.eval_result_path`

🟡 功能
- 读取 metric 推荐结果。
- 定位评测记录文件。
- 将记录路径写入 bench.meta 中。
- 调用 `MetricRunner` 执行指标计算。
- 将指标结果保存到 Analyzer 输出目录。

🔵 输出
- `analyzer.metric_eval_result_path`：指标评测结果 JSON 路径。
- `analyzer.metric_eval_results`：指标评测结果对象。

---

### 8️⃣ analyze_metric_report_node（通用文本指标报告生成）

🟢 输入
- `analyzer.metric_eval_result_path`
- `analyzer.metric_eval_results`
- bench / records 对齐信息。
- 分析模型配置。

🟡 功能
- 读取 metric 结果。
- 构建结构化分析摘要。
- 生成自然语言评测报告。
- 生成数据构造与训练建议。
- 生成 obtainer / 数据获取建议。

🔵 输出
- `analyzer.analysis_summary`
- `analyzer.analysis_summary_json_path`
- `analyzer.analyze_output_report_text_path`
- `analyzer.analyze_output_data_plan_text_path`

---

## 四、Analyzer 参数说明

Analyzer 的参数主要位于 `state["analyzer"]` 中，部分通用参数位于 state 顶层。

### 1. 通用必填参数

- `output_dir`【str】：输出目录。可以位于 state 顶层或 `analyzer.output_dir` 中。
- `analyzer.analyze_task_type`【str】：分析任务类型。支持：
  - `code`
  - `text2sql`
  - `general_text`

---

### 2. code / text2sql 必填参数

当 `analyze_task_type` 为 `code` 或 `text2sql` 时，需要以下参数：

- `analyzer.eval_result_path`【str】：已有评测结果文件路径。如果已经运行 Judger，也可以使用 `judger.output_result_path`。
- `analyzer.analyze_model_path`【str】：分析模型路径或模型名。
- `analyzer.analyze_base_url`【str】：分析模型 API Base URL。
- `analyzer.analyze_api_key`【str】：分析模型 API Key。
- `analyzer.analyze_temperature`【float】：分析模型温度。
- `analyzer.analyze_top_p`【float】：分析模型 top_p。
- `analyzer.analyze_batch_size`【int】：分析批大小。
- `analyzer.analyze_max_concurrency`【int】：最大并发数。
- `analyzer.analyze_chunk_size`【int】：分析分块大小。
- `analyzer.analyze_sampling_top_k`【int】：失败样例采样数量。
- `analyzer.output_brief`【bool】：是否输出简要分析。
- `analyzer.output_suggestion`【bool】：是否输出改进建议。
- `analyzer.quick_brief`【bool】：是否启用快速摘要模式。
- `analyzer.quick_brief_limit`【int】：快速摘要样本数量限制。

---

### 3. general_text 相关参数

当 `analyze_task_type` 为 `general_text` 时，Analyzer 主要依赖 bench 与 metric 链路。

常用参数包括：

- `analyzer.bench_name`【str】：评测集名称。
- `analyzer.bench_dataflow_eval_type`【str】：DataFlow / One-Eval 评测类型，例如 `key2_qa`、`key1_text_score` 等。
- `analyzer.bench_config`【dict】：通用文本评测配置。
- `analyzer.key_mapping`【dict】：字段映射，如 `input_question_key`、`input_answer_key`、`input_pred_key` 等。
- `analyzer.skip_dataflow_eval`【bool】：是否跳过 DataFlow 正式评测。
- `analyzer.analyze_max_tokens`【int】：分析模型最大输出 token 数。
- `analyzer.tensor_parallel_size`【int】：模型推理张量并行数。
- `analyzer.is_api`【bool】：是否通过 API 调用分析模型。

---

### 4. Analyzer 产物字段

以下字段通常由 Analyzer 节点自动写入：

- `analyzer.analyze_output_result_path`：增强版评测结果路径。
- `analyzer.analyze_output_summary_path`：评测摘要 JSON 路径。
- `analyzer.analyze_output_summary_txt_path`：评测摘要 TXT 路径。
- `analyzer.metric_plan`：指标推荐结果。
- `analyzer.metric_eval_result_path`：指标计算结果路径。
- `analyzer.metric_eval_results`：指标计算结果。
- `analyzer.analysis_summary`：结构化分析摘要。
- `analyzer.analysis_summary_json_path`：分析摘要 JSON 路径。
- `analyzer.analyze_output_report_json_path`：分析报告 JSON 路径。
- `analyzer.analyze_output_report_text_path`：分析报告文本路径。
- `analyzer.analyze_output_suggestion_path`：改进建议路径。
- `analyzer.analyze_output_data_plan_text_path`：数据构造建议路径。

---

## 五、评测数据格式要求

Analyzer 直接读取已有评测结果文件（jsonl），无需重新运行模型生成答案。

### code

常见字段：

- `task_id`：任务编号。
- `completion`：模型生成代码。
- `result`：执行结果。
- `passed`：是否通过测试。
- `assert_parsed`：断言解析信息。
- `judge`：判题信息，包括失败阶段、错误类型等。

示例：

```json
{
  "task_id": "HumanEval/0",
  "completion": "def return1():\n    return 1",
  "passed": true,
  "result": "passed"
}
```

---

### text2sql

常见字段：

- `task_id`：任务编号。
- `question`：自然语言问题。
- `db_id`：数据库 ID。
- `db_file`：数据库文件路径。
- `ground_truth`：标准 SQL。
- `completion`：模型生成 SQL。
- `result`：执行结果。
- `passed`：是否通过。
- `judge`：判题信息。

示例：

```json
{
  "task_id": "bird/0",
  "question": "What is the highest eligible free rate for K-12 students?",
  "db_id": "toxicology",
  "db_file": "/path/to/toxicology.sqlite",
  "ground_truth": "SELECT ...",
  "completion": "SELECT ...",
  "passed": false,
  "result": "execution error"
}
```

---

### general_text

通用文本任务通常由 bench / DataFlow / One-Eval 结果驱动。

常见字段由 `key_mapping` 决定，例如：

- `input_question_key`
- `input_answer_key`
- `input_pred_key`
- `input_target_key`
- `input_rejected_key`
- `input_better_key`

示例：

```json
{
  "question": "What is the capital of France?",
  "answer": "Paris",
  "prediction": "Paris"
}
```

---

## 六、输出文件说明

Analyzer 的输出文件通常位于：

```text
{output_dir}/{task_id}/analyzer/
```

常见输出包括：

### code / text2sql 链路

- `oj_records_enriched_*.jsonl`：增强版评测记录。
- `summary_*.json`：评测摘要 JSON。
- `summary_*.txt`：评测摘要文本。
- `report_*.json`：结构化分析报告。
- `report_*.txt`：自然语言分析报告。
- `final_report_*.json`：最终结论 JSON。
- `final_report_*.txt`：最终结论文档。
- `final_report_*.suggestions.txt`：改进建议文件（可选）。

### general_text 链路

- `metric_eval_result_*.json`：metric 计算结果。
- `analysis_summary_*.json`：结构化分析摘要。
- `metric_report_*.txt`：自然语言指标分析报告。
- `data_plan_*.txt`：数据构造 / 训练建议报告。

---

## 七、运行样例

### 1. code / text2sql 分析样例

```python
from loopai.agents import AnalyzerAgent
from loopai.memory import checkpointer, store

sg = AnalyzerAgent(checkpointer=checkpointer, store=store)
graph = sg()

config = {"configurable": {"thread_id": "analyzer-code-demo"}}

graph.invoke({
    "task_id": "10001",
    "output_dir": "/root/brjverl/dataflow/examples/scripts/output/",
    "analyzer": {
        "analyze_task_type": "code",
        "eval_result_path": "/root/brjverl/dataflow/examples/scripts/output/10001/result.jsonl",

        "analyze_model_path": "/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/",
        "analyze_base_url": "http://127.0.0.1:8002/v1",
        "analyze_api_key": "EMPTY",
        "analyze_temperature": 0.0,
        "analyze_top_p": 0.95,

        "analyze_batch_size": 20,
        "analyze_max_concurrency": 5,
        "analyze_chunk_size": 50,
        "analyze_sampling_top_k": 5,

        "output_brief": True,
        "output_suggestion": True,
        "quick_brief": True,
        "quick_brief_limit": 10
    }
}, config=config)
```

---

### 2. general_text 指标分析样例

```python
from loopai.agents import AnalyzerAgent
from loopai.memory import checkpointer, store

sg = AnalyzerAgent(checkpointer=checkpointer, store=store)
graph = sg()

config = {"configurable": {"thread_id": "analyzer-general-text-demo"}}

graph.invoke({
    "task_id": "10002",
    "output_dir": "/root/brjverl/dataflow/examples/scripts/output/",
    "analyzer": {
        "analyze_task_type": "general_text",

        "eval_result_path": "/root/brjverl/dataflow/examples/scripts/output/10002/detail.jsonl",
        "bench_name": "general_text_eval",
        "bench_dataflow_eval_type": "key2_qa",
        "bench_config": {
            "eval_type": "key2_qa"
        },
        "key_mapping": {
            "input_question_key": "question",
            "input_answer_key": "answer",
            "input_pred_key": "prediction"
        },

        "analyze_model_path": "/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/",
        "analyze_base_url": "http://127.0.0.1:8002/v1",
        "analyze_api_key": "EMPTY",
        "analyze_temperature": 0.0,
        "analyze_top_p": 0.95,
        "analyze_max_tokens": 2048,
        "tensor_parallel_size": 1,
        "is_api": True,

        "skip_dataflow_eval": False,
        "output_suggestion": True
    }
}, config=config)
```

---

## 八、与 Judger Agent 的关系

Judger 和 Analyzer 可以组合形成完整的评测闭环：

```text
Judger 负责生成和评测
        ↓
Analyzer 负责分析和总结
```

### Judger 输出

- 样例文件
- 评测结果文件
- OJ / SQL 执行结果
- benchmark detail records

### Analyzer 输入

- Judger 输出的 result jsonl
- Judger 写入的 bench 信息
- 用户手动指定的 eval_result_path

### Analyzer 输出

- 失败分析
- metric 结果
- 分析报告
- 最终结论
- 改进建议

---

## 九、总结

Analyzer Agent 是 Dataflow-LoopAI 中的评测分析模块。

它不负责重新生成模型答案，而是在已有评测结果基础上完成：

- 样本级错误分析
- 全局统计总结
- 指标推荐与计算
- 指标报告生成
- 最终结论生成
- 改进建议输出

其中：

- `code` / `text2sql` 走失败样本分析链路。
- `general_text` 走 metric 推荐与指标分析链路。

Analyzer 与 Judger 配合后，可以形成完整的：

```text
模型生成 → 自动评测 → 指标计算 → 结果分析 → 报告生成 → 优化建议
```

闭环流程。
