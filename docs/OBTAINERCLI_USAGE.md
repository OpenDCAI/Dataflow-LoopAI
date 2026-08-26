# ObtainerCLI DataMixer 使用文档

ObtainerCLI 的数据湖能力由 DataMixer 完整承载。公开生产命令面只有：

```bash
loopai-obtainercli dm --root /path/to/warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <datamixer-command> --json
```

`searchagent` 和 `download manifest` 是 acquisition worker 内部的数据采集桥，用于发现和下载候选数据集。正常产品流程中，外层 Codex 不应直接调用它们，而应启动 `dataset-acquisition-agent`。worker 会与 `domain_data_acquisition`（旧名 `webcrawler_dm`）并行启动：前者发现 hosted datasets，后者采集垂域权威网页为 L1；两条流都必须保留状态与产物。下载完成后，初始化、入湖、处理、索引、召回、配比、出湖、snapshot 和 lineage 都必须回到 `loopai-obtainercli dm ...`。

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

`lake.yaml` 还会持久化不含凭据的 Obtainer 运行上下文：选择的垂域采集
WebAgent、模型名、并发/子目标默认值、最近 acquisition run 和 campaign id。
因此正常工作流应使用 `--lake`，无需反复填写 warehouse；但 acquisition 的
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

解除数据湖与已结束 task/run 的绑定（清空 `obtainer_active_task_id`、
`obtainer_active_acquisition_run`、`obtainer_active_campaign_id`、
`obtainer_active_l1_dataset`，保留 WebAgent 模型/并发等默认值），新任务重跑前应执行一次，
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

## 3. 数据搜集、下载与入湖 Worker

Analyzer 报告进入 Codex SDK 后，应启用 Obtainer skill，并优先用
`dataset-acquisition-agent` 启动隔离 worker。外层 Codex 不需要直接编排
SearchAgent、download 和 ingest 细节。

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataset-acquisition-agent start \
  --run ./outputs/acquisition_run \
  --analysis-report ./outputs/analyzer_report.md \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python SyntaxError fix, runtime exception repair, assertion failure repair" \
  --target-datasets 8 \
  --max-rows-per-dataset 100000 \
  --max-bytes-per-dataset 2147483648 \
  --discovery-mode auto \
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

默认不要传 `--model`；worker 会从 Starter 模型池读取配置好的 Codex 默认模型。
只有用户明确要求本次覆盖模型时才使用 `--model`。
每次 start/resume 的返回值与 `thread.json` 都会记录 `resolved_model`、
`webagent_model` 和 `model_source`（`codex_default` 或
`operator_override`）；CLI 会将该同一模型注册到当前 DataMixer warehouse
供 WebAgent 使用，不能静默回退到本地 vLLM。

Worker 内部策略会要求：先把报告解析成明确搜集意图，候选列表先与原始需求
校对并写 rejections，再下载；单数据集最多 100000 行，且本地 JSONL
输出默认最多 2GiB；下载后规范 JSONL；
入湖必须走 DataMixer `ingest` 或 `agent-ingest`；最终写 `final_report.json`。
数据集别名不构成 finance 领域证据；`--domain finance` 只是批次级 metadata，
不会在入湖时触发逐条 LLM 审批。数据集 agent 的入湖是批量路径：`dm ingest`
按批次 metadata（含必填 `--quality-level`）把全部规范化行直接写入湖，不逐条
approve/drop。逐条质量审批属于后处理：WebAgent 的 L2 `domain_classify` 会
收到 campaign 的探索关键词（`--focus-keywords`），LLM 只接受与该主题直接相关、
且带有 grounded 语义信号的条目，随后由 `topic_quality_filter` 按置信度与信号
阈值把关。生产出湖前必须完成 DataFlowAgent 后处理（`dm dataflow
agent-run`，产出 L4 数据）；湖内每个能力桶可用量至少为出湖目标的 1.5 倍，
且 L4 最终规模达标后才允许调用 `sft-export-agent`。若用户明确指定 L3 出湖，
则跳过 L4 门，L3 数据可直接出湖。来源 URI、数据集 ID 和
source 名称只作为 provenance 与质量报告审计信息，不参与接受或拒绝。

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

记录的分域标注（如 `domain_classify` 标签与语义信号）作为 provenance 保留在
行标签中，不构成出湖接受或拒绝的依据。出湖质量由后处理主线把关：先完成
DataFlowAgent 后处理（`dm dataflow agent-run`，产出 L4 数据），湖内每个能力桶
可用量至少为出湖目标的 1.5 倍，且 L4 最终规模达标后才允许调用
`sft-export-agent`。若用户明确指定 L3 出湖，则跳过 L4 门，L3 数据可直接出湖。

底层 SearchAgent 与下载命令仍保留为采集桥，但只供 worker 内部调用或人工调试。
外层 Codex 在正常工作流中不要创建 task JSON、不要直接调用 `searchagent`，也不要直接调用 `download manifest`。

检查 `searchagent_manifest.json` 后，先剔除不相关数据集并写 filtered manifest
和 rejection report，再下载候选数据集。采集桥对单个数据集最多写出 100000 行和 2GiB 本地 JSONL：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataset-acquisition-agent status \
  --run ./outputs/acquisition_run \
  --json
```

worker 内部的 `download manifest` 会强制执行单数据集 100000 行上限和 2GiB 输出文件上限；即使传 `--max-rows 0`、更大的行数，或过大的 `--max-bytes-per-dataset`，也会按安全上限写出。达到字节上限时会中断当前数据集下载、保留已写出的部分 JSONL，并在结果里报告 `truncated`、`truncated_reason`、`rows_written` 和 `bytes_written`。生产 SFT 的最终规模、配比和出湖必须继续通过 DataMixer recipe 完成，不能把下载阶段的多个文件拼接为最终训练集。

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

后处理阶段是必须要使用 dataflowagent 的，不要手工盲选单个 DataFlow operator。`dataflow agent-run` 会让 Codex SDK 先导出试跑样本、按 DataFlow-Skills 规则规划算子链、生成并试跑 pipeline；**试跑成功即交付**（`mode=trial_run`，交付物 = `pipeline.py` + 试跑输出 `trial_processed.jsonl`）。**全量执行由上层 Codex 负责**：拿到交付的 pipeline 后，用 chunk 脚手架跑 `full_input.jsonl`，产出 `full_processed.jsonl`（L4），再按 `sample_id` 用 `apply-jsonl` merge 回 DataMixer。不要让 dataflowagent 自己跑全量或 merge。agent-run 返回的 `upstream.chunked_run_command` / `upstream.apply_command` 直接给出上层要执行的命令。

**按桶 1.5x 缓冲导出，不是全量导出。** `agent-run` 尽量带上出湖 `--recipe`（recipe.yaml）或 `--mix-plan`（mix_plan.json）：full input 会按每个桶 `ceil(bucket_target * 1.5)` 行、固定 seed 抽样导出（桶内可用行不足则全取），避免对全湖十几万行做冗余后处理。处理范围就是 `full_input.jsonl` 本身，禁止上层/agent 自行重新全量导出或扩大范围。

**质量评估必须使用 DataFlow 的 LLM 评估算子**（`PromptedEvaluator` / `PromptedFilter` 等），不得因耗时或成本而退化成纯启发式规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许规则算子兜底并说明具体原因。不得覆盖原始字段和值；后训练内容需要构造或改写时，使用生成算子写入新的派生字段，再使用 LLM 评估算子打分和筛选生成内容。

全量执行必须流式分 chunk，禁止一次性把整个导出读进内存：上层 Codex 通过外层脚手架 `loopai.agents.Obtainer.datamixer.dataflow_chunked_runner` 按 **1 万行一个 chunk** 切片输入、逐 chunk 启动同一 pipeline 并保序合并（`--chunk-size 10000`，输出 `full_processed.jsonl`）。交付的 pipeline 必须遵循 `DATAFLOW_INPUT` / `DATAFLOW_CACHE_DIR` / `DATAFLOW_PREFIX` 环境变量约定。

`domain_classify` 将主类写入可索引的 `domain`，完整多标签写入
`domain_labels`（位于样本 tags）；`domain list` 同时会发现已有样本的非空
`domain` 值。因此增量入湖或已有湖不会漏掉它们内部使用的领域类别。

自定义标签过滤使用受控 `json_extract(tags_json, '$."tag_name"')` 形式。

## 6. 生产 SFT 出湖 Worker

生产 SFT 出湖必须使用 DataMixer recipe，但外层 Codex 不再直接手写和反复
调用 `recipe validate/plan/preview/export`。使用单命令 wrapper 启动隔离的
内部 Codex SDK worker；wrapper 会把 DataMixer recipe、schema、snapshot、
纯 Alpaca 校验和失败处理规则注入到 worker 上下文。

启动新 worker：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse sft-export-agent start \
  --run ./outputs/code_failure_repair_sft_v1 \
  --analysis-report ./outputs/analyzer_report.md \
  --format alpaca \
  --target-records 100000 \
  --out ./outputs/code_failure_repair_sft_v1/export
```

`start` 默认把内部 Codex SDK worker 放到后台运行，立即返回 PID、日志路径
和 run 目录。只有需要阻塞等待时才加 `--foreground`。

查看状态：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse sft-export-agent status \
  --run ./outputs/code_failure_repair_sft_v1
```

在同一个内部 Codex thread 上继续修复：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse sft-export-agent resume \
  --run ./outputs/code_failure_repair_sft_v1 \
  --message "Remove buckets whose output falls back to text, then re-export."
```

默认不要传 `--model`；worker 会从 Starter 模型池读取配置好的 Codex 默认模型。
只有用户明确要求本次覆盖模型时才使用 `--model`。

`resume` 同样默认后台运行；外层 Codex 用 `status` 轮询，不需要长时间占住
上下文。

外层 Codex 只负责监督：读取 `status.json` 和 `final_report.json`，决定
`resume` 当前 worker，还是 `start` 一个新 worker。详细 recipe 规划、schema
修复、DataFlow 规范化、`recipe export --snapshot`、manifest/snapshot/digest
记录和最终 JSONL 校验都由 worker wrapper 内部策略控制。

Wrapper 对 Alpaca SFT 的硬约束包括：

- 最终训练 JSONL 每行只能有 `instruction`、`input`、`output`。
- `output.sources` 禁止使用 `text`、`raw_content`、`content` 或整段记录 fallback。
- `instruction == output` 必须阻断。
- 若 Q/A 混在单个 text 字段里，必须先用 DataMixer/DataFlow 规范化，或排除该 bucket。
- 若 Analyzer 或用户没有明确 SFT 规模，默认至少 `100000` records。
- failure taxonomy 配比必须依赖语义标签，如 `bug_type=syntax/logic/runtime/assertion`。
- 所有成功出湖必须有 manifest、recipe fingerprint、dataset digest 和 snapshot id。

## 7. Lineage 与 Snapshot

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse snapshot create --name sft_mix_v1 --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse snapshot list --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse lineage list --json
```

最终汇报至少包含：warehouse 路径、SearchAgent manifest、下载 manifest、入湖数据集、处理命令、index/recall 检查、recipe fingerprint、snapshot id、export manifest 和导出路径。
