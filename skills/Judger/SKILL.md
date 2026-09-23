# Judger Skill

## Purpose

无 LangGraph 的独立评测流水线。支持四种任务类型：

- **code** — 代码生成评测（evalplus 的 HumanEval+ / MBPP+，或 LiveCodeBench），计算 pass@k
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
| `eval_max_tokens` | `16384` | 最大输出 token 数（含思考推理），bench 可覆盖。vLLM 以 `--generation-config vllm` 启动，模型目录 `generation_config.json` 的 `max_new_tokens` 不会再把上限压低（如 Qwen3-8B-Base 的 2048） |
| `eval_enable_thinking` | 不设置 | 思考模式开关（None 跟随模型默认 / True 开 / False 关），bench 可覆盖 |
| `eval_batch_size` | `10` | 宿主 `generate` 阶段每批并发多少条 prompt；只对 code 的 evalplus 分支和 text2sql 生效（LiveCodeBench 在容器里自己生成，不吃这个值），bench 可覆盖 |
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
      "name": "livecodebench_codegen",
      "task_type": "code",
      "problem_path": "data/livecodebench/test.jsonl",
      "format_type": "livecodebench",
      "lcb_scenario": "codegeneration",
      "case_num": 1
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
| `problem_path` | ✅ 必填 | ✅ 必填 | ✅ 必填 | ✅ 必填 | 问题文件路径；code 按 `format_type` 分别校验：evalplus 的 HumanEval+ / MBPP+ jsonl（查字段/前缀/题数）或 LiveCodeBench 的 `test.jsonl`（只查字段，题数随 release 变） |
| `case_num` | 可选 10 | 可选 10 | — | 可选 10 | 每问题样本数；code 决定出现哪些 `pass@k`（见下面「code bench 的数据契约」），math 同时作为 val_n |
| `batch_size` | 可选 10（仅 evalplus） | 可选 10 | — | — | 宿主 `generate` 阶段每批并发多少条 prompt；`format_type=livecodebench` 时无效果，bench 设了覆盖全局 |
| `temperature` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_temperature` |
| `top_p` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_top_p` |
| `top_k` | — | — | — | 可选 | 覆盖全局 `eval_top_k`，math 请求采样参数 |
| `min_p` | — | — | — | 可选 | 覆盖全局 `eval_min_p`，math 请求采样参数 |
| `max_tokens` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_max_tokens` |
| `enable_thinking` | 可选 | 可选 | 可选 | 可选 | 覆盖全局 `eval_enable_thinking`，`false` 强制关闭思考 |
| `format_type` | 可选 | — | — | — | code 必填：`humaneval+` / `mbpp+` / `livecodebench` 三个值（其他写法直接报错），决定判哪个数据集、走哪套判分后端 |
| `lcb_scenario` | 条件必填 | — | — | — | 只在 `format_type=livecodebench` 时可配且**必须配**（`codegeneration` / `selfrepair` / `testoutputprediction` / `codeexecution`）；evalplus 的 bench 上带它会直接报错 |
| `text2sql_dir` | — | ✅ 必填 | — | — | SQLite 数据库目录 |
| `eval_type` | — | — | ✅ 必填 | — | `key2_qa` / `key1_text_score` 等 |
| `key_mapping` | — | — | 可选 | — | 字段映射，可自动推断 |

**Per-bench 可选覆盖：** 上表里标注「可选」的字段既能设在全局，也能设在单个 bench 里。bench 里设了就覆盖全局值，没设就回落全局默认 —— 用于「某个评测集需要特殊生成参数」的场景（例如某个 code 评测集需要更低温度、或某个 text2sql 评测集要关闭思考模式）。

**主/附加区别：**

| | 主任务 | 附加任务 |
|---|---|---|
| 执行顺序 | 先 | 后 |
| 失败策略 | 记录失败 + `_save_task_progress` + 退出 | 记录失败，继续 |

### code bench 的数据契约（`problem_path` 要准备什么）

code bench 只认 `format_type` 一个开关，取值就三个；`problem_path` 必须是**对应后端的
原格式 jsonl**（`data/` 在 `.gitignore` 里，自己生成一次即可）。字段缺了、前缀不对、
题数不符都会在 `validate` 阶段 `emit_error`，报错消息里直接带生成命令。

| `format_type` | `problem_path` 要什么 | 题数 / task_id 前缀 | 生成命令 |
|---|---|---|---|
| `humaneval+` | evalplus 的 HumanEval+ jsonl | 164，前缀 `HumanEval/` | `python -c "import shutil; from evalplus.data.humaneval import _ready_human_eval_plus_path; shutil.copy(_ready_human_eval_plus_path(), 'data/evalplus/humaneval_plus.jsonl')"` |
| `mbpp+` | evalplus 的 MBPP+ jsonl | 378，前缀 `Mbpp/` | 同上，换成 `evalplus.data.mbpp._ready_mbpp_plus_path` |
| `livecodebench` | 见下面的 scenario 表（还要配 `lcb_scenario`） | 随 release 变 | 见下面 |

两个 evalplus 数据集的必需字段：`task_id` / `prompt` / `entry_point` /
`canonical_solution` / `base_input` / `plus_input` / `atol`，最后一个测试字段
HumanEval+ 叫 `test`、MBPP+ 叫 `assertion`（这两个不参与判分，是用来确认「这确实是
官方那一份」的标记 —— 原始 HumanEval / sanitized-mbpp 都缺 `base_input` 等字段）。

`base_input` / `plus_input` 就是 base / plus 两套用例，判分时同一份代码两套都跑，
得到 `base_status` / `plus_status`：

- `base`：原题自带的用例（HumanEval 每题中位 7 条、MBPP 中位 3 条），宽；
  `base_pass@k` 就是「官方原版测试」口径
- `plus`：evalplus 自动生成的扩展输入（HumanEval+ 每题中位 972 条、合计 12.3 万；
  MBPP+ 中位 105 条、合计 4.0 万），专治「只对题目给的样例输入正确」的解法

plus 通过必然 base 通过，反之不然，所以 `base_pass@1 >= plus_pass@1`。主指标
`pass@1` 取 **plus 口径**（严格的那个），`base_pass@1` 留作对照

⚠️ **别用 `get_mbpp_plus()` + `write_jsonl` 生成**：`get_*_plus()` 会把输入反序列化成
`complex` / `tuple` / `set`，而 evalplus 的 `write_jsonl` 是裸 `json.dumps`，MBPP+ 的
`Mbpp/124`、`Mbpp/252`（复数输入）会直接 `TypeError`。必须复制它缓存的原始 jsonl。

`format_type=livecodebench` 时 `lcb_scenario` 必填，两者一起决定要哪份数据集、要哪些
字段。漏写、取值未知、或把它写在 evalplus 的 bench 上都由 `_preflight_benches` 直接
报错（`CONFIG_ERROR`）；它没有默认值 —— 运行时缺省会落到 `codegeneration`，而
`selfrepair` 和它共用同一份数据集与字段，静默跑错看不出任何异常：

| `lcb_scenario` | 数据集 | 题数 | 必需字段 |
|---|---|---|---|
| `codegeneration` | `code_generation_lite` 的 `testN.jsonl` | 400 (v1) → 1055 (v6) | `question_id, question_content, platform, contest_date, difficulty, starter_code, public_test_cases, private_test_cases, metadata` |
| `selfrepair` | 同一份代码生成题（会先自动跑一遍 `codegeneration` 当输入） | 同上 | 同上 |
| `testoutputprediction` | `test_generation` | 442 | `question_id, question_title, question_content, contest_id, contest_date, difficulty, test, starter_code, function_name, test_id` |
| `codeexecution` | `execution-v2`（老的 `livecodebench/execution` 缺 `contest_date` 等字段，加载会报错） | 479 | `question_id, id, contest_id, contest_date, difficulty, function_name, code, input, output, numsteps, problem_id` |

LCB 只查字段、**不校验题数**（`test.jsonl` 带 base64 私有用例、release_v1 就 1.2 GB，
validate 只读文件头部几行）。`testoutputprediction` / `codeexecution` 是一题多份样本
（442 行 = 182 题；479 行 = 92 题），产物里 `task_id` 会带 `test_id` / `id` 后缀，
不加后缀会撞车。

三种 `format_type` 的产物一致：`<bench>_sample.jsonl`（模型原始输出）/
`<bench>_result.jsonl`（逐样本，带 Analyzer 判因要读的 `passed`）/
`<bench>_summary.json`（evalplus 分支还多一份容器抽出来的
`<bench>_sample-sanitized.jsonl`）。`metrics` 全是百分数（两位小数），
但**两个后端支持哪些 `pass@k` 不一样，且都受 `case_num`（每题样本数 n）限制**：

| `format_type` | 支持的 k | 说明 |
|---|---|---|
| `humaneval+` / `mbpp+` | 1 / 10 / 100 | evalplus 写死这三个；`pass@1` 是 plus 口径，另有 `base_pass@k` / `plus_pass@k` |
| `livecodebench` = `codegeneration` / `selfrepair` | 1 / 5 / 10 / 20 / 40 / 50 / 75 / 100 / 125 / 150 / 200 / 500 / 1000 | 上游 `codegen_metrics` 的默认 k 列表 |
| `livecodebench` = `testoutputprediction` | 1 / 5 | 上游 `k_list=[1, 5]` |
| `livecodebench` = `codeexecution` | 只有 1 | 上游只算 pass@1 |

所有后端的共同前提：**`case_num`（= n）必须 ≥ k，否则那一项不出现**（两边都是
`total >= k` 才输出）。所以 `pass@10` / `pass@100` 只能靠把 `case_num` 提到 10 / 100 换
（样本量与判分时间同比例增长）；示例配置里 `case_num=1`，任何后端都只有 `pass@1`。

两条影响排期的点：LCB 镜像是仓库内本地构建的
（`loopai/skills/Judger/docker/livecodebench`，缺镜像时自动 `docker build`，首次要几分钟；
改了 `src/` 或换了上游版本都要重建），且**一次容器跑完整份数据集、不支持按题续跑**，中途
挂了就整轮重来。

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
                   → evaluate → kill_vllm_cleanup → finish
    code(lcb):     validate → kill_vllm → start_vllm
                   → evaluate_livecodebench → kill_vllm_cleanup → finish
    text2sql:      validate → kill_vllm → start_vllm → generate
                   → evaluate → kill_vllm_cleanup → finish
    general_text:  validate → eval_general_text → finish
    math:          validate → kill_vllm → start_vllm → evaluate_math (Docker) → kill_vllm_cleanup → finish
  → 收集结果到 bench_result / extra_bench_result
```

**`evaluate` 步骤（code）在 evalplus 官方镜像里跑**：宿主机把模型原始样本
`<bench>_sample.jsonl` 挂进 `ganler/evalplus:latest`，容器里先抽取、再跑官方
`evalplus.evaluate` 用 HumanEval+ / MBPP+（base
官方用例 + plus 扩展用例）判分，结果落回 `<bench>_result.jsonl` /
`<bench>_summary.json`。这边不构建镜像、不改判分代码；`metrics` 是百分数口径，
`pass@1` 取 plus 口径，另有 `base_pass@1` / `plus_pass@1`。

**`format_type=livecodebench` 的分工不同**：`evaluate_livecodebench` 一步里，容器用
`--vllm_base_url` 回调本机 vLLM **自己生成**（宿主机没有 generate / sanitize），再用
LCB 自带的用例判分；容器把结果写在挂进去的 `/app/output`，宿主机读回来转成
`<bench>_sample.jsonl` / `_sanitized.jsonl` / `_result.jsonl` / `_summary.json`。
因为要连宿主机的 vLLM，容器用 `--network host`（不是 evalplus 的 `--network none`）。
宿主机不参与生成，所以 `batch_size` 对它无效 —— Judger 只转发 `--n` / `--temperature`
/ `--top_p` / `--max_tokens` / `--enable_thinking`（`selfrepair` 另加 `--codegen_n`），
容器内并发走 LCB 自己的默认值（`--num_process_evaluate` 12、`--cache_batch_size` 100）。
metrics 同样是百分数，`pass@1` 是 LCB 自己的口径（会出现哪些 `pass@k` 见前面
「code bench 的数据契约」里的表）。
`eval_enable_thinking` 会透传成容器的 `--enable_thinking`：思考模型不关掉思考链的话，
`max_tokens` 会被思考吃光、样本里没有代码（实测 Qwen3-8B / 15 题：开着思考时 13 条
`solution` 为空、pass@1 = 13.33；关掉后只剩 1 条为空、pass@1 = 66.67，生成也快 6 倍）。

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
| `--batch-size` | 宿主生成阶段批大小（仅 code 的 evalplus 分支和 text2sql；LCB 无效果） | 否 |
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
