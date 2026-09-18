# Judger Skill

## Purpose

无 LangGraph 的独立评测流水线。支持四种任务类型：

- **code** — 代码生成评测（evalplus 的 HumanEval+ / MBPP+），计算 pass@k
- **text2sql** — SQL 生成评测，SQLite 执行校验
- **general_text** — 通用文本评测（One-Eval DataFlowEvalTool）
- **math** — 数学/AIME 评测（生成、答案提取和判分在 Docker 镜像内完成）

## How to Invoke

**唯一入口：`loopai.skills.Judger.run()`**

`DB_PATH` 和 `TASK_ID` 从环境变量自动获取：

```bash
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> \
python -c "from loopai.skills.Judger import run; run()"
```

或通过 CLI：

```bash
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> loopai-judger
```

首次修改 `setup.py` 后需重新安装项目才会生成 `loopai-configer` /
`loopai-judger` 命令；开发环境也可直接用
`python -m loopai.skills.Configer.cli` 和 `python -m loopai.skills.Judger.cli`。

## Configuration

配置通过 **Configer skill** 写入 `TaskModel.state`，分两部分：

### 全局字段（state["judger"] 顶层，所有 bench 共享）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `eval_model_path` | 无 | 模型路径（必填） |
| `eval_temperature` | `0` | 采样温度，bench 可覆盖 |
| `eval_top_p` | `0.95` | Top-P 采样，bench 可覆盖 |
| `eval_max_tokens` | `16384` | 最大输出 token 数（含思考推理），bench 可覆盖 |
| `eval_enable_thinking` | 不设置 | 思考模式开关（None 跟随模型默认 / True 开 / False 关），bench 可覆盖 |
| `eval_batch_size` | `10` | 生成阶段每批并发多少条 prompt，仅 code/text2sql 用，bench 可覆盖 |
| `eval_case_num` | `10` | 每问题样本数，bench 可覆盖 |
| `eval_vllm_tensor_parallel_size` | `1` | vLLM 张量并行数 |
| `eval_vllm_gpu_memory_utilization` | `0.9` | vLLM GPU 显存利用率 |
| `cuda_visible_devices` | `"0"` | 指定 GPU |
| `output_dir` | `"./outputs"` | 输出根目录 |
| `eval_model_name` | 空 | `/v1/models` 暴露的模型名；留空使用 `eval_model_path` |
| `eval_top_k` / `eval_min_p` | `-1` / `0` | math 请求采样参数 |

### Bench 配置（state["judger"]）

所有评测集通过 `benchlist` 和 `extra_benchlist` 列表配置。**格式必须是 JSON 数组**（`[{...},{...}]`），**不是** JSONL（每行一个对象）：

```json
{
  "benchlist": [
    {
      "name": "gsm8k",
      "task_type": "general_text",
      "problem_path": "/data/gsm8k/test.jsonl",
      "eval_type": "key2_qa",
      "key_mapping": {}
    },
    {
      "name": "human_eval",
      "task_type": "code",
      "problem_path": "data/evalplus/humaneval_plus.jsonl",
      "case_num": 10,
      "batch_size": 10,
      "format_type": "humaneval+"
    },
    {
      "name": "bird_dev",
      "task_type": "text2sql",
      "problem_path": "/data/bird/dev.jsonl",
      "text2sql_dir": "/data/bird/dev_databases",
      "case_num": 10,
      "batch_size": 10
    },
    {
      "name": "aime26",
      "task_type": "math",
      "problem_path": "/data/aime26_test.jsonl",
      "case_num": 2
    }
  ],
  "extra_benchlist": []
}
```

**bench entry 字段：**

| 字段 | code | text2sql | general_text | math | 说明 |
|---|---|---|---|---|---|
| `name` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 | bench 标识 |
| `task_type` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 | `code` / `text2sql` / `general_text` / `math` |
| `problem_path` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 | 问题文件路径；code 必须是 evalplus 的 HumanEval+ / MBPP+ 数据集 jsonl（validate 按 `format_type` 查字段/前缀/题数） |
| `case_num` | 可选 10 | 可选 10 | — | 可选 10 | 每问题样本数；math 同时作为 val_n |
| `batch_size` | 可选 10 | 可选 10 | — | — | 生成阶段每批并发多少条 prompt（仅 code/text2sql），bench 设了覆盖全局 |
| `temperature` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_temperature` |
| `top_p` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_top_p` |
| `top_k` | — | — | — | 可选 | 覆盖全局 `eval_top_k`，math 请求采样参数 |
| `min_p` | — | — | — | 可选 | 覆盖全局 `eval_min_p`，math 请求采样参数 |
| `max_tokens` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_max_tokens` |
| `enable_thinking` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_enable_thinking`，`false` 强制关闭思考 |
| `format_type` | 可选 | — | — | — | code 必填：只有 `humaneval+` / `mbpp+` 两个值（其他写法直接报错），决定判哪个 evalplus 数据集 |
| `text2sql_dir` | — | ✅ 必填 | — | — | SQLite 数据库目录 |
| `eval_type` | — | — | ✅ 必填 | — | `key2_qa` / `key1_text_score` 等 |
| `key_mapping` | — | — | 可选 | — | 字段映射，可自动推断 |

**Per-bench 可选覆盖：** 上表里标注「可选」的字段既能设在全局，也能设在单个 bench 里。bench 里设了就覆盖全局值，没设就回落全局默认 —— 用于「某个评测集需要特殊生成参数」的场景（例如某个 code 评测集需要更低温度、或某个 text2sql 评测集要关闭思考模式）。

**主/附加区别：**

| | 主任务 | 附加任务 |
|---|---|---|
| 执行顺序 | 先 | 后 |
| 失败策略 | 记录失败 + `_save_task_progress` + 退出 | 记录失败，继续 |

### 预填写流程

```
1. configer_get_task(schema="states", section="judger", task_id="<task_id>")
2. 将缺失字段告知用户，征得确认后写入
3. configer_update_task("judger", {"benchlist": [...], "eval_model_path": "..."}, task_id="<task_id>")
```

## Pipeline

每个 bench entry 独立跑一遍完整流水线：

```
对每个 bench:
  _apply_bench_to_state → 注入 bench 字段到 state["judger"]
  → 按 task_type 选流水线:
    code:          validate → kill_vllm → start_vllm → generate
                   → sanitize → evaluate → kill_vllm_cleanup → finish
    text2sql:      validate → kill_vllm → start_vllm → generate
                   → evaluate → kill_vllm_cleanup → finish
    general_text:  validate → eval_general_text → finish
    math:          validate → kill_vllm → start_vllm → evaluate_math (Docker) → kill_vllm_cleanup → finish
  → 收集结果到 bench_result / extra_bench_result
```

**`sanitize` 步骤（仅 code）**：从模型输出里提取可执行的 Python。单独成一步是为了
让"原始输出"和"提取后"都留档 —— 排查"评测挂掉是模型写错还是提取错了"时，对比
`<bench>_sample.jsonl` 和 `<bench>_sanitized.jsonl` 就够了。

**`evaluate` 步骤（code）在 evalplus 官方镜像里跑**：宿主机把模型原始样本
`<bench>_sample.jsonl` 挂进 `ganler/evalplus:latest`，容器里先跑官方
`evalplus.sanitize` 抽取、再跑官方 `evalplus.evaluate` 用 HumanEval+ / MBPP+（base
官方用例 + plus 扩展用例）判分，结果落回 `<bench>_result.jsonl` /
`<bench>_summary.json`。这边不构建镜像、不改判分代码；`metrics` 是百分数口径，
`pass@1` 取 plus 口径，另有 `base_pass@1` / `plus_pass@1`。上面那个宿主机 `sanitize`
步骤只用来留档对比，**不在判分路径上**。详见 `docs/JUDGER_CODE_EVALPLUS.md`。

提取方式是**语法驱动**的：先看整段能否 `ast.parse`，不行就从 `def <entry_point>`
往后长取最长的合法片段，最后丢掉顶层非定义语句（模型的"示例/自测"会被 exec 真的
执行，里面写错的 assert 会把整条样本判错）。**不用 markdown 围栏正则** —— 围栏不是
任何地方定下的约束，而「先给函数定义、再给 Example Usage」是最常见的输出形状，
按围栏取块很容易拿到只有调用、没有定义的示例段。详见 `utils/sanitize.py`。

## Output

### stdout（emit_success）

```json
{
  "ok": true,
  "data": {
    "bench_result": [
      {"bench_name": "gsm8k", "task_type": "general_text",
       "output_result_path": "...", "metrics": {"accuracy": 0.94}}
    ],
    "extra_bench_result": [
      {"bench_name": "human_eval", "task_type": "code",
       "output_result_path": "...", "metrics": {"pass@1": 0.85}}
    ],
    "metrics": {"gsm8k": {"accuracy": 0.94}, "human_eval": {"pass@1": 0.85}}
  }
}
```

### 目录结构

```
outputs/<task_id>/
└── judger/
    └── <version_id>/
        ├── judger.pkl                  ← 事件流，load_events() 读取
        ├── vllm.log                    ← 本次运行的 vLLM 输出，排查崩溃/OOM 看这里
        ├── gsm8k/                      ← bench_name 子目录
        │   ├── text_eval_summary_*.json
        │   └── gsm8k_*_steps/
        ├── human_eval/
        │   ├── human_eval_sample.jsonl              ← 模型原始输出（generate）
        │   ├── human_eval_sanitized.jsonl           ← 自研提取器留档（不参与判分）
        │   ├── human_eval_sample-sanitized.jsonl    ← evalplus 官方抽取（判分用的输入）
        │   ├── human_eval_sample-sanitized.eval_results.json  ← evalplus 原始判定
        │   ├── human_eval_result.jsonl              ← 逐样本判定结果（evaluate）
        │   ├── human_eval_summary.json              ← pass@k 汇总（evaluate）
        │   └── log.txt
        ├── aime26/                     ← math bench
        │   └── aime26_result.json
        └── bird_dev/
```

### Configer 持久化

`_save_task_progress` 写入 `state.judger.bench_result` 和 `state.judger.extra_bench_result`，Analyzer 从中读取。

`math` 分支的数学评测逻辑由固定名称 `math-eval-loopai` 镜像提供。Judger
运行 `evaluate_math` 步骤时会先检查本地镜像；不存在则自动从
`loopai/skills/Judger/docker/math_eval` 构建（使用 `--network host`），无需手工
构建。运行时数据集只读挂载到容器，结果目录挂载到 `/outputs`；容器通过 host
network 访问 Judger 启动的 8911 vLLM 服务，不挂载宿主机 Conda 环境。

数学数据集必须是本地 JSON、JSONL 或 Parquet 文件。评测器自动将以下字段别名
归一化为 `problem` 和 `answer`：问题支持 `problem/question/prompt/query/input`，
答案支持 `answer/target/final_answer/solution`，因此不需要传入 `dataset` 类型参数。

### CLI 参数覆盖

安装项目后可直接使用 `loopai-judger`。默认从数据库任务读取配置；命令行参数
仅覆盖本次运行，不修改数据库：

```bash
DB_PATH=/path/to/api/db.sqlite3 \
loopai-judger \
  --task-id math-aime26-20260911-192302-ebdf0ac3 \
  --model-path /path/to/model \
  --dataset-path /path/to/aime26_test.jsonl \
  --cuda-visible-devices 4 \
  --case-num 2 \
  --max-tokens 38912
```

如果传入 `--config-path`，则配置文件会先覆盖并保存到指定任务的数据库状态，
然后再执行评测：

```bash
DB_PATH=/path/to/api/db.sqlite3 \
loopai-judger \
  --task-id math-aime26-20260911-192302-ebdf0ac3 \
  --config-path examples/config/math_bench.json
```

配置文件支持 `.json`、`.yaml`、`.yml`，内容可使用 `judger` 或
`default_states.judger` 结构。`task_id` 可从命令行、环境变量或配置文件读取，
优先级依次为命令行、环境变量、配置文件。

全部参数：

| 参数 | 作用 | 持久化 |
|---|---|---|
| `--db-path` | SQLite 数据库路径 | 否（写入 `DB_PATH`） |
| `--task-id` | 任务 id；优先级高于 `TASK_ID` 环境变量 | 否 |
| `--config-path` | JSON/YAML 配置，**先写库再运行** | **是** |
| `--output-dir` | 覆盖输出根目录 | 否 |
| `--problem-path` / `--dataset-path` | 覆盖评测数据集路径（两个名字是同一个参数） | 否 |
| `--model-path` | vLLM 模型路径 | 否 |
| `--model-name` | vLLM 对外模型名；留空取模型路径末段 | 否 |
| `--temperature` / `--top-p` / `--top-k` / `--min-p` / `--presence-penalty` | 采样参数 | 否 |
| `--batch-size` | 生成阶段批大小（仅 code/text2sql） | 否 |
| `--case-num` | 每问题样本数；math 即 `val_n` | 否 |
| `--max-tokens` | 最大生成 token 数 | 否 |
| `--tensor-parallel-size` / `--gpu-memory-utilization` | vLLM 启动参数 | 否 |
| `--cuda-visible-devices` | 可见 GPU 编号 | 否 |
| `--enable-thinking` / `--no-thinking` | 思考模式开关（互斥） | 否 |
| `--resume` | 复用上次运行的 `version_id`（输出目录不变）；**步骤不会跳过** | 否 |

除 `--config-path` 外都是**只影响本次运行**的环境变量覆盖（`JUDGER_*` / `CUDA_VISIBLE_DEVICES`），不写数据库。

## Error Handling

失败统一由 `emit_error(exc, stream_writer=writer)` 收口：配置错误等可预期的失败由
各步骤显式调用；其余异常由 `loopai.skills.Judger.run` 兜底，同样转成结构化 payload。
两种路径都会：

- stdout 输出 `{"ok": false, ...}`
- judger.pkl 写入 `status=failed`
- taskruntime 表标记失败

所有 error `recoverable=true`，Codex 可引导用户修复后重试。

vLLM 的清理不依赖流水线步骤：无论评测成功、失败，还是 `emit_error` 直接退出进程，
`run_judger_pipeline` 都会在 `finally` 里收掉**本次运行启动的** vLLM，不留占着 GPU
和 8911 端口的孤儿进程（没启动过则不会碰该端口）。

## Environment Variables

| 变量 | 来源 | 默认值 |
|---|---|---|
| `DB_PATH` | 环境变量 | 必填 |
| `TASK_ID` | 环境变量 | 必填 |
| `OUTPUT_DIR` | 环境变量 | `./outputs` |
| `CUDA_VISIBLE_DEVICES` | 环境变量 | `"0"` |
