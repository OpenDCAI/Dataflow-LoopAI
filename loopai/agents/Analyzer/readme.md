# Analyzer Agent

Analyzer 是 Dataflow-LoopAI 中用于 **模型评测结果分析与结论生成** 的分析型 Agent。  
该模块基于已有的模型评测结果（如 OJ / 执行结果），在不重新运行模型的前提下，对结果进行深入分析，并自动生成结构化评测报告与改进建议。

Analyzer 主要面向 **代码生成（Code Generation）** 与 **Text-to-SQL / SQL 生成** 等任务场景，强调评测结果的**可解释性、系统性与复用性**。

---

## 功能概览

Analyzer 提供以下核心能力：

- 样本级失败原因分析（LLM + 启发式规则融合）
- 模型整体能力与不足的统计总结
- 数据集背景自动生成（基于真实样本字段与示例）
- 最终评测结论与改进建议生成
- 输出结构化 JSON 报告与可读文本报告

---

## Pipeline 结构

Analyzer 由三个顺序执行的 Node 构成，形成一条线性分析流水线：

```text
eval_model_node
        ↓
analyze_result_node
        ↓
draw_conclusion_node
```

⸻

Node 功能说明

1️⃣ eval_model_node（样本级分析）

输入
	•	已有的评测结果文件（jsonl）

功能
	•	对每条样本进行失败原因分析
	•	识别失败阶段、错误类型、关键失败因子
	•	融合启发式规则与 LLM 判因结果

输出
	•	增强版 OJ 记录（oj_records_enriched_*.jsonl）
	•	初步评测摘要（summary_*.json / .txt）

⸻

2️⃣ analyze_result_node（全局统计与总结）

输入
	•	增强后的样本级记录

功能
	•	汇总模型在不同维度下的整体表现
	•	从以下两个视角生成分析结论：
	•	模型能力与行为模式
	•	数据 / 评测策略合理性

输出
	•	结构化分析结果（report_*.json）
	•	LLM 自动生成的分析文本（report_*.txt）

⸻

3️⃣ draw_conclusion_node（结论生成）

输入
	•	summary
	•	全局分析结果
	•	原始与增强后的样本信息

功能
	•	自动生成 数据集背景介绍
	•	生成最终评测结论报告
	•	可选生成模型改进建议

输出
	•	final_report_*.json
	•	final_report_*.txt
	•	final_report_*.suggestions.txt（可选）

⸻

输入数据要求

Analyzer 直接读取 已有评测结果文件（jsonl），无需重新运行模型。

Code 任务常见字段示例
	•	任务编号（task_id）
	•	模型生成代码（completion）
	•	运行结果（result）
	•	是否通过（passed）
	•	断言与判题信息（assert_parsed / judge）

SQL / Text-to-SQL 任务常见字段示例
	•	用户问题（question）
	•	数据库文件路径（db_file）
	•	标准 SQL（ground_truth）
	•	模型生成 SQL（completion）
	•	执行结果（result）
	•	是否通过（passed）

⸻

Node 功能说明

1️⃣ eval_model_node（样本级分析）

输入
	•	已有的评测结果文件（jsonl）

功能
	•	对每条样本进行失败原因分析
	•	识别失败阶段、错误类型、关键失败因子
	•	融合启发式规则与 LLM 判因结果

输出
	•	增强版 OJ 记录（oj_records_enriched_*.jsonl）
	•	初步评测摘要（summary_*.json / .txt）

⸻

2️⃣ analyze_result_node（全局统计与总结）

输入
	•	增强后的样本级记录

功能
	•	汇总模型在不同维度下的整体表现
	•	从以下两个视角生成分析结论：
	•	模型能力与行为模式
	•	数据 / 评测策略合理性

输出
	•	结构化分析结果（report_*.json）
	•	LLM 自动生成的分析文本（report_*.txt）

⸻

3️⃣ draw_conclusion_node（结论生成）

输入
	•	summary
	•	全局分析结果
	•	原始与增强后的样本信息

功能
	•	自动生成 数据集背景介绍
	•	生成最终评测结论报告
	•	可选生成模型改进建议

输出
	•	final_report_*.json
	•	final_report_*.txt
	•	final_report_*.suggestions.txt（可选）

⸻

输入数据要求

Analyzer 直接读取 已有评测结果文件（jsonl），无需重新运行模型。

Code 任务常见字段示例
	•	任务编号（task_id）
	•	模型生成代码（completion）
	•	运行结果（result）
	•	是否通过（passed）
	•	断言与判题信息（assert_parsed / judge）

SQL / Text-to-SQL 任务常见字段示例
	•	用户问题（question）
	•	数据库文件路径（db_file）
	•	标准 SQL（ground_truth）
	•	模型生成 SQL（completion）
	•	执行结果（result）
	•	是否通过（passed）

⸻

## 如何运行 Analyzer Pipeline

Analyzer 可以通过一个最小化的 Python 入口，直接运行整条 pipeline，完成：

**评测增强 → 分析 → 报告生成**

---

### 1️⃣ 构造运行所需的 state（选择 code / sql）

> 关键点：通过 `analyze_task_type` 指定任务类型  
> - `code`：代码生成 / 代码评测  
> - `sql`：Text-to-SQL / SQL 生成评测

```python
from pathlib import Path

def _build_state(task_type: str, eval_result_path: Path, outdir: Path) -> dict:
    return {
        # 任务类型："code" 或 "sql"
        "analyze_task_type": task_type,

        # 已存在的评测结果文件（jsonl）
        "eval_result_path": str(eval_result_path),

        # 输出目录
        "output_dir": str(outdir),

        # -------- LLM 配置（OpenAI-compatible）--------
        "analyze_model_path": "/path/to/model",
        "analyze_base_url": "http://127.0.0.1:8002/v1",
        "analyze_api_key": "EMPTY",

        "analyze_temperature": 0.0,
        "analyze_top_p": 0.95,
        "analyze_batch_size": 1,

        # 是否生成一句话短评 / 改进建议
        "output_brief": True,
        "output_suggestion": True,

        # analyze_result_node 里的采样与并发配置
        "analyze_sampling_top_k": 5,
        "analyze_chunk_size": 8,
        "analyze_max_concurrency": 4,
    }
```

### 2️⃣ 顺序执行三个 Node**

```python
state = eval_model_node(state)
state = analyze_result_node(state)
state = draw_conclusion_node(state)
```

## 输出结果说明

Analyzer 运行结束后，输出目录中通常包含以下文件：

- `oj_records_enriched_*.jsonl`  
  样本级增强后的评测记录（包含失败阶段、错误类型、判因信息等）

- `summary_*.json` / `summary_*.txt`  
  评测摘要与整体统计信息

- `final_report_*.json`  
  最终结构化评测报告（机器可读）

- `final_report_*.txt`  
  最终人类可读评测报告

- `final_report_*.suggestions.txt`（可选）  
  LLM 自动生成的模型改进建议

---

## 数据集背景自动生成

Analyzer 会基于**真实样本字段与示例数据**，由 LLM 自动生成数据集背景说明。

字段描述采用如下统一格式：

例如：

- 任务编号（`task_id`）
- 模型生成代码（`completion`）
- 是否通过（`passed`）

该背景介绍**仅描述数据集本身与评测目标**，不会涉及模型性能结论。

---

## LLM 依赖说明

Analyzer 使用 **OpenAI-compatible API** 调用本地或远程大模型，仅需后端支持以下接口规范：

```http
POST {base_url}/chat/completions
```

## ** LangGraph 集成**

Analyzer 可作为一个完整 Agent 接入 LangGraph，节点执行顺序如下：

eval_model → analyze_result → draw_conclusion

适用于构建 **评测 → 分析 → 反馈** 的自动化评测闭环系统。

