# ObtainerCLI DataMixer 使用文档

> lite 版本获取 worker 使用 Hugging Face 数据集；优先选择 2025/2026 创建或更新的数据集，
> 支持多数据集批量下载、JSONL 规范化、入湖和索引构建；下文仍保留的
> 历史命令说明不适用于 lite 版本。

ObtainerCLI 的数据湖能力由 DataMixer 完整承载。公开生产命令面只有：

```bash
loopai-obtainercli dm --root /path/to/warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <datamixer-command> --json
```

`download manifest` 仍是 worker 内部的数据下载桥，用于处理调用方提供的
manifest。正常产品流程中，外层 Codex 不应直接调用它，而应启动
`dataset-acquisition-agent`。下载完成后，初始化、入湖、处理、索引、召回、出湖、snapshot
和 lineage 都必须回到 `loopai-obtainercli dm ...`。

## 1. 环境与事件

```bash
conda activate loopaiv2
loopai-obtainercli --help
loopai-obtainercli dm --help
```

ObtainerCLI 会输出 JSON。需要记录 StreamEvent 时传入公共参数：

```bash
loopai-obtainercli \
  --task-id data_task_001 \
  --output-dir ./outputs \
  dm --root /data/lakes/code_sft/warehouse stats --json
```

事件写入 `./outputs/<task-id>/obtainercli/<version>/obtainercli.pkl`，可用：

```python
from loopai.skills.ObtainerCLI import load_events

events = load_events(task_id="data_task_001", output_dir="./outputs")
```

## 2. 初始化与指针

直接创建 DataMixer warehouse：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse init --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse status --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse schema --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse columns --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse stats --json
```

LoopAI 项目应复用同一个 DataMixer warehouse。repo 内的
`.loopai/lake.yaml` 是可切换指针，`--lake` 只负责把指针解析到同一个
DataMixer warehouse：

```yaml
root: /data/lakes/code_sft
warehouse: /data/lakes/code_sft/warehouse
catalog: datamixer
backend: datamixer
namespace: loopai
```

之后可使用：

```bash
loopai-obtainercli dm --lake .loopai/lake.yaml stats --json
```

`lake.yaml` 还会持久化不含凭据的 Obtainer 运行上下文：模型名和最近的
acquisition run。因此正常工作流应使用 `--lake`，无需反复填写 warehouse；但 acquisition 的
`start`、`status` 和 `resume` 必须显式复用同一个 `--run` 路径：

```bash
loopai-obtainercli dm --lake .loopai/lake.yaml dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --objective "collect code training data" --keywords "code dataset" --json
loopai-obtainercli dm --lake .loopai/lake.yaml dataset-acquisition-agent status \
  --run ./outputs/acquisition_run --json
loopai-obtainercli dm lake context --link .loopai/lake.yaml
```

先扫描项目目录、`outputs`、`.loopai` 和常见 LoopAI 缓存目录中的候选
DataMixer lake：

```bash
loopai-obtainercli dm lake scan --link .loopai/lake.yaml --project-root .
```

从扫描结果中选择已有 warehouse，加载为当前项目的数据湖指针：

```bash
loopai-obtainercli dm lake load \
  --warehouse /data/lakes/code_sft/warehouse \
  --link .loopai/lake.yaml
```

查看当前指针：

```bash
loopai-obtainercli dm lake current --link .loopai/lake.yaml
```

解除数据湖与已结束 task/run 的绑定（清空 `obtainer_active_task_id` 和
`obtainer_active_acquisition_run`），新任务重跑前应执行一次，
避免残留的旧 task_id 让 agent 误判数据湖状态：

```bash
loopai-obtainercli dm lake unbind --link .loopai/lake.yaml
```

卸载当前项目指针但保留可复用 warehouse：

```bash
loopai-obtainercli dm lake delete --link .loopai/lake.yaml
```

只有明确要删除真实 DataMixer warehouse 文件时才使用：

```bash
loopai-obtainercli dm lake delete --link .loopai/lake.yaml --delete-warehouse --yes
```

## 3. Hugging Face 数据搜集与入湖 Worker

Analyzer 报告进入 Codex SDK 后，使用 `dataset-acquisition-agent` 启动隔离
worker。Worker 在 Hugging Face Hub 按关键词和 `lastModified` 排序查找多个
数据集，优先选择 2025/2026 创建或更新的数据集；每个数据集单独规范化为
JSONL、入湖，全部完成后统一构建 DataMixer 索引。

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --analysis-report ./outputs/analyzer_report.md \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python SyntaxError fix, runtime exception repair, assertion failure repair" \
  --target-datasets 8 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --json
```

默认后台运行，立即返回 PID、日志路径和 run 目录。轮询状态：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataset-acquisition-agent status \
  --run ./outputs/acquisition_run \
  --json
```

继续同一个内部 Codex thread：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataset-acquisition-agent resume \
  --run ./outputs/acquisition_run \
  --message "Remove unrelated datasets from the filtered manifest, then continue ingest." \
  --json
```

默认不要传 `--model`；worker 会从 Starter 模型池读取 Codex 默认模型。每次
start/resume 的返回值与 `thread.json` 都会记录 `resolved_model` 和
`model_source`。

Worker 会在 run 目录写入候选、过滤、下载、规范化、入湖和索引报告，并以
`final_report.json` 汇总 Hugging Face 数据集 ID、更新时间、JSONL 路径、行数、
入湖结果和索引结果。单数据集默认最多 100000 行、2GiB JSONL。

金融入湖示例：

```bash
loopai-obtainercli dm --root /data/lakes/finance/warehouse ingest sec_finance \
  --file ./sec_finance.classified.jsonl \
  --quality-level L3 \
  --domain finance \
  --source-uri https://www.sec.gov/Archives/ \
  --tag source_dataset_id=sec-filings \
  --json
```

下载完成后，主 agent 可继续运行 DataFlowAgent 对入湖数据做后处理。

## 4. 入湖

规范 JSONL 推荐把训练内容放在 `content`，把可过滤字段放在同一行 metadata：

```jsonl
{"content":{"instruction":"Fix the syntax error","output":"def add(a, b): return a + b"},"bug_type":"syntax","quality_score":0.95,"source_uri":"hf://dataset/train/0"}
{"content":{"instruction":"Fix the runtime error","output":"return values[0] if values else None"},"bug_type":"runtime","quality_score":0.91,"source_uri":"hf://dataset/train/1"}
```

入湖：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse ingest code_repair_mix \
  --file ./outputs/downloads/code_repair.train.jsonl \
  --content-key content \
  --stage sft \
  --domain code \
  --lang python \
  --source huggingface \
  --license unknown \
  --task-type SFT \
  --quality-level L3 \
  --processing-level normalized \
  --source-kind huggingface \
  --loop-uuid "$LOOP_UUID" \
  --version-id "$VERSION_ID" \
  --tag source_dataset=owner/name \
  --tokenizer tiktoken:o200k_base \
  --json
```

非 JSONL 或 schema 未规范的数据，先用 DataMixer `agent-ingest`：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse agent-ingest ./outputs/downloads/raw_file \
  --engine builtin \
  --dataset code_repair_mix \
  --quality-level L3 \
  --json
```

## 5. 查询、处理、索引与召回

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse query \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 20 \
  --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dist domain --json

# 湖级领域 taxonomy：内置 broad classes + 已入湖 domain 自动同步；可显式扩展
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse domain list --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse domain add text2sql robotics --json

# 对清洗后的记录做 LLM 多标签领域分类；无需重复维护 labels 参数
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op run domain_classify \
  --dataset code_repair_mix \
  --arg model=deepseek-proxy \
  --arg max_input_chars=12000 \
  --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op list --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op run quality_score --dataset code_repair_mix --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op run minhash_dedup --dataset code_repair_mix --arg k=5 --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse contam add --name benchmark --file benchmark.txt --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse decontaminate --against benchmark --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataflow agent-run \
  --target "score GSM8K answer-focused SFT rows and keep high-quality rows" \
  --dataset math_sft \
  --trial-rows 20 \
  --expected-outputs math_answer_quality \
  --recipe /path/to/recipe.yaml \
  --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse index build --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recall \
  --query "buggy and fixed Python code pairs for runtime exception repair" \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 50 \
  --json
```

进 DataFlowAgent 之前，先按 bad case 题目做多路召回,得到候选集再交给
DataFlowAgent（不要把整湖直接喂进去）。这一步由 `recall-badcases` 一步完成：
读 bad case 题目 JSONL，逐条 `question` 多路召回，命中按 `sample_id` 并集去重，
直接出湖成候选输入 JSONL。`--from` 省略时自动定位 Analyzer 最新一轮的
`badcase_questions_*.jsonl`；召回数量由 `--limit` 控制，默认 6000。

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recall-badcases \
  --out ./outputs/obtainer/recall_candidates.jsonl \
  --limit 6000 \
  --json
# 显式指定题目文件 / domain 路由 / 相似度阈值 / 关键词召回：
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recall-badcases \
  --from ./outputs/<task>/analyzer/.../badcase_questions_<ts>.jsonl \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --min-sim 0.3 \
  --out ./outputs/obtainer/recall_candidates.jsonl \
  --json
```

召回产物即 DataFlowAgent 的 `full_input.jsonl`，每行带 `recall_score` 和
`recall_question_id` 便于追溯。

后处理阶段必须使用 DataFlowAgent。`dataflow agent-run` 导出试跑样本，规划算子链并试跑；**非空试跑输出通过六维发布评审后才交付**（`mode=trial_run`，交付物为 `pipeline.py` 和 `trial_processed.jsonl`）。代码校验每维评分、总分至少 85、无红线以及 `release` 决策。**全量执行由上层 Codex 负责**：使用 chunk 脚手架处理 `full_input.jsonl`，再按 `sample_id` 用 `apply-jsonl` 合并回 DataMixer。仅已发布的 trial 会提供 `upstream.chunked_run_command` / `upstream.apply_command`。

带失败评审的 `planned_only` 会在三次 SDK 调用预算内自动续接同一线程，失败证据保存在 `review_checkpoints/`。若预算用尽，返回 `continuation_required=true` 和 `thread_id`，表示仍需处理；这不是质量交付成功。上层应读取评审，使用 `--resume-thread-id <thread_id>`、新 `--work-dir` 和具体修复目标继续运行。服务等外部阻塞应记录实际尝试和失败证据。

**质量评估必须使用 DataFlow 的 LLM 评估算子**（`PromptedEvaluator` / `PromptedFilter` 等），不得因耗时或成本而退化成纯启发式规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许规则算子兜底并说明具体原因。不得覆盖原始字段和值；后训练内容需要构造或改写时，使用生成算子写入新的派生字段，再使用 LLM 评估算子打分和筛选生成内容。

全量执行必须流式分 chunk，禁止一次性把整个导出读进内存：上层 Codex 通过外层脚手架 `loopai.agents.Obtainer.datamixer.dataflow_chunked_runner` 按 **1 万行一个 chunk** 切片输入、逐 chunk 启动同一 pipeline 并保序合并（`--chunk-size 10000`，输出 `full_processed.jsonl`）。交付的 pipeline 必须遵循 `DATAFLOW_INPUT` / `DATAFLOW_CACHE_DIR` / `DATAFLOW_PREFIX` 环境变量约定。

`domain_classify` 将主类写入可索引的 `domain`，完整多标签写入
`domain_labels`（位于样本 tags）；`domain list` 同时会发现已有样本的非空
`domain` 值。因此增量入湖或已有湖不会漏掉它们内部使用的领域类别。

自定义标签过滤使用受控 `json_extract(tags_json, '$."tag_name"')` 形式。

benchmark 参考记录先创建独立 dataset，再用 `contam add --benchmark-dataset`
关联该 dataset；训练去污的 `--filter` 应限定当前任务。导入参考记录时使用
`ingest <dataset> --benchmark-set <guard> --stage eval --quality-level L3 --file <jsonl>`。
此入口只接受 guard 已关联且不含非 eval 记录的 dataset，避免参考记录被自己的
guard 过滤；写入 `guard_only=true`、`is_contaminated=1` 和 `contam_source`。
普通训练导入仍执行全部去污规则。不能将此参数用于训练源。

## 6. 生产 SFT 出湖

DataFlowAgent 完成后，主 agent 直接运行 DataMixer recipe 命令完成规划和出湖：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recipe validate ./recipe.yaml --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recipe plan ./recipe.yaml --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recipe preview ./recipe.yaml --per-bucket 3 --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recipe export ./recipe.yaml \
  --out ./outputs/code_failure_repair_sft_v1/export --snapshot --json
```

Recipe 对 Alpaca SFT 的约束包括：

- 最终训练 JSONL 每行只能有 `instruction`、`input`、`output`。
- `output.sources` 禁止使用 `text`、`raw_content`、`content` 或整段记录 fallback。
- `instruction == output` 必须阻断。
- 若 Q/A 混在单个 text 字段里，必须先用 DataMixer/DataFlow 规范化，或排除该 bucket。
- 若 Analyzer 或用户没有明确 SFT 规模，默认至少 `100000` records。
- failure taxonomy 过滤必须依赖语义标签，如 `bug_type=syntax/logic/runtime/assertion`。
- 所有成功出湖必须有 manifest、recipe fingerprint、dataset digest 和 snapshot id。

## 7. Lineage 与 Snapshot

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse snapshot create --name sft_mix_v1 --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse snapshot list --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse lineage list --json
```

最终汇报至少包含：warehouse 路径、候选/下载 manifest、入湖数据集、处理命令、index/recall 检查、recipe fingerprint、snapshot id、export manifest 和导出路径。
