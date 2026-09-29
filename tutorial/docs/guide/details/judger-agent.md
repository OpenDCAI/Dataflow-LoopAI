# Judger Agent 详细指南

`JudgerAgent` 是 LoopAI 闭环中的评测节点，负责把“当前模型表现如何”这件事测清楚。

当前实现是一套**独立的函数流水线**（不依赖 LangGraph），一次运行评测一个或多个
bench（`benchlist` / `extra_benchlist`）。

## 核心职责

- 执行评测任务：`code` / `text2sql` / `general_text` / `math`
- 启动本地 vLLM：`code` / `text2sql` 用它采样，`math` 的评测器直接访问这个服务；
  `general_text` 走 One-Eval 子进程，不需要 vLLM
- 算分：
  - `code`：两个判分后端，由 bench 的 `format_type` 选 ——
    `humaneval+` / `mbpp+` 走 **evalplus 官方镜像**（`ganler/evalplus:latest`）判
    HumanEval+ / MBPP+；`livecodebench` 走 `livecodebench:latest`，**生成和判分都在
    容器里**（容器通过 `--vllm_base_url` 回调本机 vLLM）。两者都产出 pass@k
  - `text2sql`：宿主生成模型回答，`loopai-bird-eval:dev` 容器在 BIRD SQLite
    数据库上执行预测 SQL 与标准 SQL，比较结果并产出 pass@k
  - `general_text`：One-Eval DataFlowEvalTool 子进程
  - `math`：`math-eval-loopai` Docker 评测器
- 产出样本、分数与结构化结果，供 Analyzer 使用

## 运行方式

`Judger` 是独立命令行工具 `loopai-judger`（入口 `loopai.skills.Judger.cli:main`），
也会作为子进程被 Codex / 后端调用（`loopai.skills.Judger.run`）。

运行前需要两个环境变量：

| 环境变量 | 说明 |
| --- | --- |
| `DB_PATH` | SQLite 数据库路径（任务状态与配置存储） |
| `TASK_ID` | 任务 ID |

```bash
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> loopai-judger
DB_PATH=api/db/db.sqlite3 TASK_ID=<task_id> loopai-judger --output-dir /data/outputs
```

常用参数（完整列表见 `skills/Judger/SKILL.md`）：

| 参数 | 作用 |
| --- | --- |
| `--config-path` | JSON/YAML 配置，**先覆盖并写库再运行**（`{"judger": {...}}` 或 `{"default_states": {"judger": {...}}}` 结构） |
| `--output-dir` | 覆盖输出根目录（等价 `OUTPUT_DIR`） |
| `--problem-path` / `--dataset-path` | 覆盖评测数据集路径 |
| `--model-path` / `--model-name` | vLLM 模型路径 / 对外模型名 |
| `--temperature` `--top-p` `--top-k` `--min-p` `--presence-penalty` | 采样参数 |
| `--batch-size` `--case-num` `--max-tokens` | 宿主生成阶段批大小（LCB 分支无效果）/ 每问题样本数 / 最大生成 token |
| `--tensor-parallel-size` `--gpu-memory-utilization` `--cuda-visible-devices` | vLLM 启动参数 |
| `--enable-thinking` / `--no-thinking` | 思考模式开关（互斥） |
| `--resume` | 复用上次运行的 `version_id`（输出目录不变）；**步骤不会跳过** |

除 `--config-path` 外都是只影响本次运行的覆盖（内部转成 `JUDGER_*` 环境变量），
不写数据库。

也可以直接用示例脚本跑一份配置文件（会打印最终 payload）：

```bash
python examples/scripts/run_judger_standalone.py \
    --config-path examples/config/code_bench_gov.json --print-result
```

## 配置模型：benchlist / extra_benchlist

配置不是单个 `eval_problem_path`，而是**评测集列表**：

- `benchlist`（主任务）：某个 bench 失败会终止整个流水线
- `extra_benchlist`（附加任务）：失败只记录，不影响主任务

每个 bench 是一个 dict：

| 字段 | code | text2sql | general_text | math | 说明 |
| --- | --- | --- | --- | --- | --- |
| `name` | ✅ | ✅ | ✅ | ✅ | 评测集名称，同时作为输出目录名 |
| `task_type` | ✅ | ✅ | ✅ | ✅ | `code` / `text2sql` / `general_text` / `math` |
| `problem_path` | ✅ | ✅ | ✅ | ✅ | 问题文件路径；code 按 `format_type` 要求 evalplus 的 HumanEval+ / MBPP+ 或 LiveCodeBench 的目标数据集（见下） |
| `format_type` | ✅ | — | — | — | code 用它选判分后端：`humaneval+` / `mbpp+` / `livecodebench`（其他写法在预检阶段报错） |
| `lcb_scenario` | 条件必填 | — | — | — | 只在 `format_type=livecodebench` 时可配且**必须配**：`codegeneration` / `selfrepair` / `testoutputprediction` / `codeexecution` |
| `case_num` | 可选 | 可选 | — | 可选 | 每问题样本数（= pass@k 的 n，决定出现哪些 k；math 即 `val_n`），默认 10 |
| `batch_size` | 可选（仅 evalplus） | 可选 | — | — | 宿主 `generate` 阶段每批并发多少条 prompt；`format_type=livecodebench` 时无效果，默认 10 |
| `temperature` / `top_p` / `max_tokens` / `enable_thinking` | 可选 | 可选 | 可选 | 可选 | 覆盖同名全局字段 |
| `top_k` / `min_p` | — | — | — | 可选 | math 请求采样参数 |
| `text2sql_dir` | — | ✅ | — | — | SQLite 数据库目录 |
| `eval_type` | — | — | ✅ | — | `key2_qa` / `key1_text_score` 等 |
| `key_mapping` | — | — | 可选 | — | 字段映射，不填则自动推断 |

`general_text` 支持的 `eval_type`：`key2_qa`、`key2_q_ma`、`key3_q_choices_a`、
`key3_q_choices_as`、`key3_q_a_rejected`、`key1_text_score`。

### Per-bench 覆盖

上表标「可选」的字段既能设在全局，也能设在单个 bench 里：bench 里设了就覆盖全局、
只对该 bench 生效，没设就回落全局默认。用于「某个评测集需要特殊生成参数」的场景：

```json
{
  "benchlist": [
    {"name": "human_eval_default", "task_type": "code",
     "problem_path": "data/evalplus/humaneval_plus.jsonl", "format_type": "humaneval+"},
    {"name": "human_eval_cold", "task_type": "code",
     "problem_path": "data/evalplus/humaneval_plus.jsonl", "format_type": "humaneval+",
     "temperature": 0.3, "enable_thinking": false, "max_tokens": 4096}
  ]
}
```

### 预检

主任务的所有 bench 会在**启动 vLLM 之前**一次性校验（必填字段、`task_type`、
`problem_path` 是否存在、code 的 `format_type` 是否合法、`lcb_scenario` 是否合法且与
`format_type` 配套）：有问题直接
`emit_error`（`CONFIG_ERROR`），不会跑到第 N 个 bench 才发现配错。附加任务的问题
降级为告警并跳过该 bench。

`lcb_scenario` 必须显式配：漏写、取值未知、写在 evalplus 的 bench 上（该字段只在
LiveCodeBench 侧生效）三种情况都在预检报错。它没有默认值 —— 运行时缺省会落到
`codegeneration`，而 `selfrepair` 和它共用同一份数据集与字段，漏写只会静默跑错
scenario。

## 全局配置字段

以下字段对整个任务生效，均支持环境变量覆盖：

| 字段 | 默认值 | 说明 | 环境变量 |
| --- | --- | --- | --- |
| `eval_model_path` | - | 被评测模型路径；为空时尝试从 trainer checkpoint 推断 | `JUDGER_MODEL_PATH` |
| `eval_model_name` | 模型路径末段 | vLLM 对外模型名 | `JUDGER_MODEL_NAME` |
| `eval_temperature` | `0` | 模型温度 | `JUDGER_TEMPERATURE` |
| `eval_top_p` | `0.95` | top-p 采样累计概率阈值 | `JUDGER_TOP_P` |
| `eval_top_k` / `eval_min_p` / `eval_presence_penalty` | `-1` / `0.0` / `0.0` | 采样参数（math 会用） | `JUDGER_TOP_K` / `JUDGER_MIN_P` / `JUDGER_PRESENCE_PENALTY` |
| `eval_enable_thinking` | 不设置 | 是否开启思考模式（Qwen3 的 `enable_thinking`）；`true`/`false` 通过 `chat_template_kwargs` 显式开关，不设置跟随模型默认 | `JUDGER_ENABLE_THINKING` |
| `eval_max_tokens` | `16384` | 最大输出 token 数（含推理 token）；vLLM 启动带 `--generation-config vllm`，模型 `generation_config.json` 的 `max_new_tokens` 不再覆盖此值 | `JUDGER_MAX_TOKENS` |
| `eval_batch_size` | `10` | 宿主 `generate` 阶段批大小；只对 code 的 evalplus 分支和 text2sql 生效 | `JUDGER_BATCH_SIZE` |
| `eval_case_num` | `10` | 每问题样本数 | `JUDGER_CASE_NUM` |
| `eval_vllm_tensor_parallel_size` | `1` | vLLM 张量并行大小 | `JUDGER_TENSOR_PARALLEL_SIZE` |
| `eval_vllm_gpu_memory_utilization` | `0.9` | vLLM GPU 显存利用率 | `JUDGER_GPU_MEMORY_UTILIZATION` |
| `cuda_visible_devices` | `0` | 可见 GPU 编号 | `CUDA_VISIBLE_DEVICES` |

## 流水线步骤

完整步骤列表：`validate`、`kill_vllm`、`start_vllm`、`generate`、`evaluate`、
`evaluate_livecodebench`、`kill_vllm_cleanup`、`eval_general_text`、`evaluate_math`、
`finish`。

每个 bench 独立跑一遍：

```
code:          validate → kill_vllm → start_vllm → generate → evaluate
               → kill_vllm_cleanup → finish
code(lcb):     validate → kill_vllm → start_vllm → evaluate_livecodebench
               → kill_vllm_cleanup → finish
text2sql:      validate → kill_vllm → start_vllm → generate → evaluate
               → kill_vllm_cleanup → finish
general_text:  validate → eval_general_text → finish
math:          validate → kill_vllm → start_vllm → evaluate_math → kill_vllm_cleanup → finish
```

各步骤职责：

- `validate`：校验必填字段、`problem_path` 是否存在、题目文件格式是否正确。
- `kill_vllm`：先清掉可能残留的 vLLM 进程（端口 `8911`）。
- `start_vllm`：用 `eval_model_path` / 张量并行 / 显存利用率启动本地 vLLM，成功后写
  `eval_base_url`；日志落盘到 `<output_dir>/<task_id>/judger/<version_id>/vllm.log`。
- `generate`：按 `batch_size` 分批调 vLLM 采样（**并发度**，不影响分数），写
  `<bench_name>_sample.jsonl`。只有 code 的 evalplus 分支和 text2sql 走这一步。
- `evaluate`：**code 的 evalplus 分支的代码提取和判分都在官方容器里**（
  `evalplus.sanitize` + `evalplus.evaluate`，宿主机不做提取）；text2sql 将生成的
  JSONL 和 BIRD 数据库只读挂入本地 `bird_eval` 容器，执行 SQL 并回写结果与
  metrics。容器输出实时透传终端；code 判分看起来卡住时可以配合
  `docker top <容器ID>` 看是不是单核 100% 在跑 `evalplus.sanitize`。
- `evaluate_livecodebench`：LCB 分支的生成 + 判分，整段在 `livecodebench:latest` 里
  （宿主机没有 generate / sanitize）。容器用 `--network host` 连宿主机的 vLLM，把
  结果写在挂进去的 `/app/output`，宿主机读回来转成 Judger 的契约文件。宿主机不参与
  生成，所以 `batch_size` 对它无效（并发走容器内 LCB 自己的默认值）。
- `kill_vllm_cleanup`：评测结束后关闭 vLLM。清理不依赖这一步：无论成功、失败还是
  `emit_error` 直接退出，`run_judger_pipeline` 都会在 `finally` 里收掉**本次运行
  启动的** vLLM（没启动过就不碰 8911 端口）。
- `eval_general_text` / `evaluate_math`：交给各自的外部评测器（见下）。

## 评测数据集要求

### `code` 任务

题目必须是**判分侧认的那份数据集**，按 `format_type` 分两条。

#### `format_type=humaneval+` / `mbpp+`（evalplus）

判分在官方镜像里用它自带的那一份（HumanEval+ 164 题 / MBPP+ 378 题），
`evalplus.evaluate` 要求**每题都有样本**：

| `format_type` | 必需字段 | `task_id` 前缀 | 题数 |
| --- | --- | --- | --- |
| `humaneval+` | `task_id, prompt, entry_point, canonical_solution, base_input, plus_input, atol, test` | `HumanEval/` | 164 |
| `mbpp+` | 同上，最后一个是 `assertion` 而非 `test` | `Mbpp/` | 378 |

字段缺了、前缀不对、题数不对，都会在 `validate` 阶段 `emit_error`
（`INVALID_INPUT`，消息里点名缺哪些字段/多少行，并给出导出命令）。原始 HumanEval、
原始/sanitized MBPP 都不是这个格式，会被当场拦下。

`base_input`（原题自带的用例）和 `plus_input`（evalplus 生成的扩展输入）是同一份代码
要跑的两套测试：`base` 宽、`plus` 严，同一份代码两套都跑，得出 `base_status` /
`plus_status`。plus 通过必然 base 通过，反之不然。主指标 `pass@1` 取 plus 口径，
`base_pass@1` 作对照（实测 Qwen3-8B：base 61.59% vs plus 56.10%）。

数据集不在仓库里（`data/` 被 gitignore），每个环境导出一次：

```bash
# 官方 release（等价于 evalplus 自己的缓存内容，md5 与官方镜像一致）
python examples/scripts/download_evalplus_data.py            # 默认写 data/evalplus/
python examples/scripts/download_evalplus_data.py --from-cache   # 离线：直接复制本机 evalplus 缓存
```

bench 样例：

```json
{"name": "humaneval", "task_type": "code",
 "problem_path": "data/evalplus/humaneval_plus.jsonl",
 "format_type": "humaneval+", "case_num": 1, "batch_size": 10}
```

#### `format_type=livecodebench`（LiveCodeBench）

`lcb_scenario` 决定要哪份数据集和哪些字段（**只查字段、不校验题数** —— 题数随 release
版本变，且 `testN.jsonl` 带 base64 私有用例、release_v1 就 1.2 GB，只读文件头部）：

| `lcb_scenario` | 数据集 | 题数 | 必需字段 |
| --- | --- | --- | --- |
| `codegeneration` | `code_generation_lite` 的 `testN.jsonl` | 400 (v1) → 1055 (v6) | `question_id, question_content, platform, contest_date, difficulty, starter_code, public_test_cases, private_test_cases, metadata` |
| `selfrepair` | 同一份代码生成题（跑之前会自动先跑一遍 `codegeneration` 当输入） | 同上 | 同上 |
| `testoutputprediction` | `test_generation` | 442 | `question_id, question_title, question_content, contest_id, contest_date, difficulty, test, starter_code, function_name, test_id` |
| `codeexecution` | `execution-v2`（老的 `execution` 缺 `contest_date` 等字段，加载会报错） | 479 | `question_id, id, contest_id, contest_date, difficulty, function_name, code, input, output, numsteps, problem_id` |

数据集同样不在仓库里，各下一次即可（`test.jsonl` 就是 release_v1，要别的版本下
`test2.jsonl` … `test6.jsonl`）：

```bash
mkdir -p data/livecodebench
curl -L -o data/livecodebench/test.jsonl \
  https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/test.jsonl
python -c "from datasets import load_dataset; load_dataset('livecodebench/test_generation', split='test').to_json('data/livecodebench/test_generation.jsonl', orient='records', lines=True)"
python -c "from datasets import load_dataset; load_dataset('livecodebench/execution-v2', split='test').to_json('data/livecodebench/execution.jsonl', orient='records', lines=True)"
```

`testoutputprediction` / `codeexecution` 是一题多份样本（442 行 = 182 题，479 行 = 92 题），
产物里的 `task_id` 会带 `test_id` / `id` 后缀，不带后缀会撞车。判分镜像
`livecodebench:latest` 由仓库内 `loopai/skills/Judger/docker/livecodebench` 本地构建
（缺镜像时自动 build，首次要几分钟），一次容器跑完整份数据集、**不支持按题续跑**。

bench 样例：

```json
{"name": "livecodebench_codegen", "task_type": "code",
 "problem_path": "data/livecodebench/test.jsonl",
 "format_type": "livecodebench", "lcb_scenario": "codegeneration", "case_num": 1}
```

### `text2sql` 任务

| 字段名 | 含义 | 说明 |
| --- | --- | --- |
| `task_id` | 题目标号 | |
| `prompt` | 模型输入提示 | |
| `db_id` | 数据库名称 | `dbName.sqlite` 应在 `{text2sql_dir}/dbName/` 下 |
| `question` | 自然语言问题 | |
| `ground_truth` | 标准 SQL | |

`generate` 已将模型原始回答写入 `<bench>_sample.jsonl` 的 `completion`，并从题目
文件复制 `ground_truth`、`question`，根据 `text2sql_dir` 与 `db_id` 写入 `db_file`。
`evaluate` 调用 `utils/evaluate_bird.py`：先核对每题有 `case_num` 条样本，再检查
`loopai-bird-eval:dev` 镜像；若本机没有此镜像，就从
`loopai/skills/Judger/docker/bird_eval` 自动构建。容器只负责执行与比较 SQL，
不加载模型。它把逐条判定写到 `<bench>_result.jsonl`，将题数、样本数、pass@k、
容器内 Python/SQLite 版本写到 `<bench>_summary.json`。`case_num=1` 时汇总中还有
`execution_accuracy_percent`（BIRD EX 百分数）。目前不计算 BIRD 的难度分组或 VES。
Dockerfile 和评测入口的手动构建、试跑方法见
[`bird_eval/README.md`](../../../../loopai/skills/Judger/docker/bird_eval/README.md)。
`setup.py` 仅把这些文件打包到 Python 安装包；安装时不会构建镜像。

### `general_text` 任务

通用 JSONL，**字段名不强制**：配置了 `key_mapping` 就直接用，否则由
`_generate_key_mapping` 按 `eval_type` 扫描前几行自动推断（常用键：
`input_question_key`、`input_target_key`、`input_pred_key`、`input_choices_key`、
`input_label_key` 等）。

### `math` 任务

**没有固定数据集清单**，也不按数据集名去 HuggingFace 拉取：只要是本地 JSON /
JSONL / Parquet，字段别名会被自动归一化 —— 问题支持
`problem`/`question`/`prompt`/`query`/`input`，答案支持
`answer`/`target`/`final_answer`/`solution`。AIME / MATH(-500) / GSM8K / AMC 这类
「题干 + 数值或 LaTeX 答案」的数据集都能直接跑，不需要传数据集名。

判分在 `math-eval-loopai` 镜像里用 `math_verify` 做 LaTeX 等价比较：

- 标准答案先取 `####` 后缀（GSM8K 风格），再取 `\boxed{...}`，都没有就用原字符串
- 模型输出必须带 `\boxed{...}`（prompt 里已要求），取不到就记
  `[No boxed answer found]`，该样本判错并拉低 `format_rate`

要注意的是：`answer` 必须是能和 `\boxed{...}` 里那点内容直接比较的答案。选择题只有在
`answer` 就是字母/数值时才对得上 —— 存的是选项序号或整段选项文本时会全判错；需要执行
代码才能判分的数据集也不属于这条流水线（走 `code` 分支）。metrics 见
`skills/Judger/SKILL.md`。

## 输入与输出

**输入**：模型信息、评测任务定义（benchlist / extra_benchlist）、数据集与评测配置。

**输出目录**：

```
<output_dir>/<task_id>/judger/
├── judger.pkl                  ← 事件流，load_events(task_id, output_dir) 读取
├── vllm.log                    ← 本次运行的 vLLM 日志（排查崩溃 / OOM）
└── <version_id>/
    └── <bench_name>/
        ├── <bench>_sample.jsonl                       ← 模型原始输出
        ├── <bench>_sample-sanitized.jsonl             ← evalplus 官方抽取（判分输入，仅 evalplus 分支）
        ├── <bench>_sample-sanitized_eval_results.json ← evalplus 原始判定（仅 evalplus 分支）
        │                                              （新版镜像写成 .eval_results.json，两种都认）
        ├── <model_repr>/                              ← LCB 分支：容器写的原生产物
        │   └── Scenario.<scenario>_<n>_<temperature>[_eval_all].json
        ├── <bench>_result.jsonl                       ← 逐样本判定（带 Analyzer 判因读的 passed）
        └── <bench>_summary.json                       ← pass@k 汇总；text2sql 还记录 EX 与环境版本
```

各任务类型的 metrics 口径：

| task_type | metrics |
| --- | --- |
| `code`（evalplus） | 百分数：`pass@1`（plus 口径，主指标）、`base_pass@1`、`plus_pass@1`、`passed`、`samples`、`failed_task_count` |
| `code`（LiveCodeBench） | 百分数：`pass@1`（主指标）和其余 `pass@k`，没有 `base_` / `plus_` 前缀；题数、样本数、`failed_task_count` 只在 `<bench>_summary.json` |
| `text2sql` | 小数：`pass@1` / `pass@10` / `pass@100`（仅输出 `case_num >= k` 的项）；`case_num=1` 时 BIRD EX 百分数在 summary 中 |
| `math` | 百分数：`pass@n`、`average@n`、`majority_vote@n`、`format_rate`（n = `case_num`） |
| `general_text` | 评测器返回的统计（如 `accuracy`） |

两个 code 后端支持的 `pass@k` 不一样，且都要 `case_num >= k` 才会出现：evalplus 固定
1 / 10 / 100；LiveCodeBench 是 1 / 5 / 10 / 20 / 40 … 1000（`codeexecution` 只有
`pass@1`，`testoutputprediction` 是 1 / 5）。示例配置里 `case_num=1`，所以只有
`pass@1`。详见 `skills/Judger/SKILL.md` 的「code bench 的数据契约」。

结果聚合到 `bench_result` / `extra_bench_result` 并写入数据库任务状态（供 Analyzer
读取）；stdout 的 `emit_success` payload 里 `metrics` 是**按 bench_name 索引的 JSON
字符串**。

失败统一由 `emit_error` 收口：stdout 输出 `{"ok": false, "error": {"code": ...}}`，
事件流标记失败，taskruntime 记录失败原因，Codex 可引导用户修复后重试。

## 在闭环中的位置

Judger 通常是闭环里真正开始执行的第一层。没有这一步，后续分析、数据获取和训练都缺少可靠依据。

## 使用时最该关注什么

- 本地 vLLM 是否起得来（显存、GPU 编号、端口 8911 是否被占）
- 每个 bench 的 `task_type` / `problem_path` 是否正确；code 还要看 `format_type` 选了哪个
  后端，以及（LCB）`lcb_scenario` 是否和目标数据集配套
- code bench 的数据集是否已准备：evalplus 要 HumanEval+ / MBPP+ 且与镜像里那份对得上；
  LiveCodeBench 要 `testN.jsonl`（release 版本决定题数）/ `test_generation` / `execution-v2`
- 结果路径与 metrics 是否生成
- 输出样例是否足以支撑后续问题分析
