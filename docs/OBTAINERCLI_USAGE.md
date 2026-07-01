# ObtainerCLI DataMixer 使用文档

ObtainerCLI 的数据湖能力由 DataMixer 完整承载。公开生产命令面只有：

```bash
loopai-obtainercli dm --root /path/to/warehouse <datamixer-command> --json
loopai-obtainercli dm --lake .loopai/lake.yaml <datamixer-command> --json
```

`searchagent` 和 `download manifest` 是 Obtainer 的数据采集桥，用于发现和下载候选数据集。下载完成后，初始化、入湖、处理、索引、召回、配比、出湖、snapshot 和 lineage 都必须回到 `loopai-obtainercli dm ...`。

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

如果 repo 内已有 `.loopai/lake.yaml`，`--lake` 只负责把指针解析到同一个 DataMixer warehouse：

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

## 3. SearchAgent 与下载

Analyzer 报告进入 Codex SDK 后，应启用 Obtainer skill，并先把报告解析成明确的数据集搜集意图。不要只把整份报告丢给搜索。

```bash
loopai-obtainercli searchagent \
  --query-file ./outputs/analyzer_report.md \
  --objective "collect buggy and fixed Python code-pair datasets covering syntax, logic, runtime, and assertion failures for SFT training" \
  --keywords "program repair dataset, buggy fixed code pairs, Python SyntaxError fix, runtime exception repair, assertion failure repair" \
  --output-root ./outputs \
  --max-deep-queries 3 \
  --max-deep-pages 3 \
  --json
```

检查 `searchagent_manifest.json` 后下载候选数据集。采集桥对单个数据集最多写出 100000 行：

```bash
loopai-obtainercli download manifest \
  --manifest ./outputs/searchagent_manifest.json \
  --output-root ./outputs/downloads \
  --split train \
  --max-rows 100000 \
  --json
```

`download manifest` 会强制执行单数据集 100000 行上限；即使传 `--max-rows 0` 或更大的值，也会按该上限写出。生产 SFT 的最终规模、配比和出湖必须继续通过 DataMixer recipe 完成，不能把下载阶段的多个文件拼接为最终训练集。

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
  --json
```

## 5. 查询、处理、索引与召回

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse query \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 20 \
  --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dist domain --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op list --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op run quality_score --dataset code_repair_mix --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse op run minhash_dedup --dataset code_repair_mix --arg k=5 --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse contam add --name benchmark --file benchmark.txt --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse decontaminate --against benchmark --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse dataflow agent-run \
  --target "score GSM8K answer-focused SFT rows and keep high-quality rows" \
  --model deepseek-codex \
  --dataset math_sft \
  --trial-rows 20 \
  --expected-outputs math_answer_quality \
  --apply \
  --json

loopai-obtainercli dm --root /data/lakes/code_sft/warehouse index build --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recall \
  --query "buggy and fixed Python code pairs for runtime exception repair" \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 50 \
  --json
```

如果下游任务需要 DataFlow 算子链，不要手工盲选单个 DataFlow operator。`dataflow agent-run` 会让 Codex SDK 先导出试跑样本、按 DataFlow-Skills 规则规划算子链、生成并试跑 pipeline，再按 `sample_id` merge 回 DataMixer。低层 `op run dataflow --arg op=<DataFlowClassName>` 只适合已经明确知道要跑哪个 DataFlow 算子的场景。

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
  --out ./outputs/code_failure_repair_sft_v1/export \
  --model deepseek-codex
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
  --message "Remove buckets whose output falls back to text, then re-export." \
  --model deepseek-codex
```

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
