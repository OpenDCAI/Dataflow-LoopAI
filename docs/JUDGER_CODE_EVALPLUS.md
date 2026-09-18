# Judger code 评测：evalplus 官方镜像

code 分支的判分交给 [evalplus](https://github.com/evalplus/evalplus) 的**官方镜像**
`ganler/evalplus:latest` 跑：镜像里已经装好 evalplus、也烤好了 HumanEval+ / MBPP+
（`Dockerfile` 里的 `RUN python3 -c "... get_human_eval_plus(); get_mbpp_plus()"`），
所以**这边不构建镜像、不 vendor 源码、不改判分代码**，判的是 evalplus 的官方口径。

**分工**（和 math 分支同构）：vLLM 启动与采样仍是 Judger 自己的步骤；
「抽取 + 判分」整段在容器里，跑的是镜像自带的 `evalplus.sanitize` 和 `evalplus.evaluate`。

## 1. 一次评测长什么样

```
validate → kill_vllm → start_vllm → generate → sanitize → evaluate → kill_vllm_cleanup → finish
                                      └ vLLM    └ 留档      └ evalplus 官方镜像
                                                            sanitize + evaluate
```

| 步骤 | 做什么 | 产物（`outputs/<task_id>/judger/<version_id>/<bench>/`） |
| --- | --- | --- |
| `generate` | 宿主机调 vLLM，每个问题 `eval_case_num` 份答案 | `<bench>_sample.jsonl` |
| `sanitize` | 宿主机用自研提取器留一份可执行代码（**只用于对比排查，不在判分路径上**） | `<bench>_sanitized.jsonl` |
| `evaluate` | 把 `_sample.jsonl` 挂进官方镜像，先 `evalplus.sanitize` 再 `evalplus.evaluate` | `<bench>_result.jsonl`、`<bench>_summary.json`、`<bench>_sample-sanitized.jsonl`、`<bench>_sample-sanitized.eval_results.json` |

容器命令就是官方两个 CLI 串起来（后处理也在官方镜像里，不在这边提取）：

```bash
docker run --rm --network none -v <bench_dir>:/work ganler/evalplus:latest sh -c \
  'evalplus.sanitize --samples /work/humaneval_sample.jsonl \
   && evalplus.evaluate --dataset humaneval --samples /work/humaneval_sample-sanitized.jsonl'
```

镜像缺失时会自动 `docker pull ganler/evalplus:latest` 一次（可以用 `CODE_EVAL_IMAGE`
换镜像名）。拉不到不硬猜：直接 `emit_error` 报出来，并给出补救办法 ——
没有 docker 命令是 `DEPENDENCY_ERROR`；`docker pull` 失败（无网 / registry 不通 /
镜像名写错）是 `EXTERNAL_SERVICE_ERROR`，报错里带上 docker 的原因，以及
「联网机器 `docker pull` + `docker save | gzip` → 本机 `docker load -i`」的离线搬运命令。
容器里 `evalplus` 非 0 退出同理（`EXTERNAL_SERVICE_ERROR`，容器日志照旧实时透传到 stdout）。

为什么不直接 `evalplus.evaluate --model ... --backend vllm` 一把梭：那样生成也在容器里，
但 vLLM 生命周期、采样参数（temperature/top_p/thinking/case_num）和
`<bench>_sample.jsonl` 这个契约都归 Judger（Analyzer / DataFlow 读它）。只传
`--samples` 时 evalplus **不做后处理**，所以抽取显式调 `evalplus.sanitize`。

`--network none`：vLLM 在宿主机、数据集在镜像里，判分环境不该联网。

## 2. 为什么不让 evalplus 顺手把生成也做了

`evalplus.evaluate --model M --backend B` 确实会走 `run_codegen` → `codegen` →
`sanitize` → 判分，一条龙。我们没这么用，原因如下（想复现 leaderboard 数字时可以切过去）：

| | Judger 生成（当前） | evalplus 自带 codegen |
| --- | --- | --- |
| 生成在哪里 | 宿主机 vLLM，判分容器 `--network none`、不碰 GPU | 容器内 `--backend vllm/hf`（**官方镜像没装 vllm/torch**，`pip install ".[perf]"` 只带了 evalplus 本体），或 `--backend openai --base-url` 指向外部服务 |
| prompt | Judger 的 chat 模板 | `tokenizer.chat_template` 决定 chat/base，或用 `--force-base-prompt` 强制 base；`openai` 后端只有 chat |
| 采样参数 | 全局/bench 的 `eval_temperature`、`top_p`、`max_tokens`(默认 16384)、`enable_thinking`、`case_num`、`batch_size` | evalplus 自己的默认值：`temperature=0`、`n_samples=1`、`max_new_tokens=768`、`batch_size=1`；`--greedy` 还会强制 bs=1/n=1/temp=0 |
| 产物 | `<bench>_sample.jsonl`（Judger 契约，Analyzer / DataFlow 读它） | `evalplus_results/<dataset>/<model>_<backend>_temp_<t>.jsonl`（sanitized 与 `.raw` 两份） |
| 断点复用 | Judger 的 step / checkpoint | 按目标文件里已有的样本数 resume |
| 可比性 | 需要在报告里说明自己的采样口径 | 与 evalplus leaderboard 可比 |

实际取舍：只有在「要想办法复现官方数字」时才有必要把生成也交出去；那也得自己做一个装了
vllm/torch 的镜像，或者用 `--backend openai` 指向 Judger 的 vLLM（等于换掉 prompt 和采样
参数）。日常评测保持现在的分工，改动最小、产物也仍然归 Judger 管。

## 2. 数据契约（题目文件必须自己提供）

`problem_path` 必须是 **evalplus 数据集格式的 jsonl**，按 bench 的评测集分别校验：

| bench 的 `format_type` | 必需字段 | task_id 前缀 | 题数 |
| --- | --- | --- | --- |
| `humaneval` | `task_id, prompt, entry_point, canonical_solution, base_input, plus_input, atol, test` | `HumanEval/` | 164 |
| `mbpp` | 同上，但最后一个是 `assertion` 而非 `test` | `Mbpp/` | 378 |

字段缺了、前缀不对、题数不对都会在 `validate` 阶段 `emit_error`，消息里点名缺哪些字段、
缺了多少行，并直接给出生成命令（`utils/evaluate_code.py` 的 `check_problem_file`）。原因有两个：

* 判分在容器里用镜像自带的 HumanEval+（164 题）/ MBPP+（378 题），
  `evalplus.evaluate` 要求**每题都有样本**，`task_id` 对不上就直接失败；
* 原始 MBPP 是 974/500 题、`sanitized-mbpp.json` 只有 427 题且缺 `test`/`base_input`，
  **都不是** MBPP+ 的格式，混用会被 `validate` 当场拦下。

生成这份文件（一次性；宿主机装了 evalplus 即可。`data/` 在 `.gitignore` 里，不会被提交）：

```bash
# 直接复制 evalplus 缓存的官方原始 jsonl（推荐，零损耗）
python -c "import shutil; from evalplus.data.humaneval import _ready_human_eval_plus_path; shutil.copy(_ready_human_eval_plus_path(), 'data/evalplus/humaneval_plus.jsonl')"
python -c "import shutil; from evalplus.data.mbpp import _ready_mbpp_plus_path; shutil.copy(_ready_mbpp_plus_path(), 'data/evalplus/mbpp_plus.jsonl')"
```

⚠️ 别用 `get_mbpp_plus()` + `write_jsonl` 那种写法：`get_*_plus()` 会把输入反序列化成
`complex`/`tuple`/`set`，而 evalplus 的 `write_jsonl` 是裸 `json.dumps`，碰到 MBPP+ 的
`Mbpp/124`、`Mbpp/252`（复数输入）会直接 `TypeError`。`check_problem_file` 报错时给出的
就是上面这两条复制命令。

顺带一提，宿主机数据集和镜像里的数据集是同一份：宿主机 `get_human_eval_plus_hash()` =
`fe585eb4df8c88d844eeb463ea4d0302`，正是容器判分结果里的 `dataset_hash`；MBPP+ 也逐字
比对过（两侧摘要同为 `9454b57664910c46`）。所以宿主机导出、容器判分不会出现题目对不上的情况。

bench 配置（`examples/config/code_bench_gov.json` 是一份可直接跑的样例）：

```json
{"name": "humaneval", "task_type": "code",
 "problem_path": "data/evalplus/humaneval_plus.jsonl",
 "format_type": "humaneval+", "case_num": 1}
```

code bench 只认 `format_type` 这一个字段（没有 `code_task` 之类的第二种写法），
取值只有 `humaneval+` / `mbpp+` 两种，其他写法（`humaneval`、`human-eval`、
`MBPP+` …）一律在 `validate` 阶段报错 —— 判分用的是官方镜像里那份数据集，
名字对不上就该当场拦下，不猜。`eval_case_num` 就是每题样本数，直接作为 pass@k 的 n。

## 3. 指标口径

`<bench>_summary.json` / `state.judger.metrics` 都是百分数：

| key | 含义 |
| --- | --- |
| `pass@1` | **plus 口径**（base 官方用例 + evalplus 扩展用例都过），主指标 |
| `base_pass@1` / `plus_pass@1` | 两个口径的明细，便于看「换更严的测试后掉多少分」 |
| `passed` / `samples` / `failed_task_count` | 通过样本数 / 总样本数 / 一个都没过的题数 |

`pass@k` 直接用 evalplus 结果里的值；镜像里的 evalplus 是 0.3.x 时结果文件不带
`pass_at_k`，这时按它的 `estimate_pass_at_k` 同一公式现算（`utils/evaluate_code.py`）。

## 4. 怎么跑 / 怎么回归

```bash
# 全流程（起本地 vLLM + 生成 + 容器判分）
python examples/scripts/run_judger_standalone.py \
    --config-path examples/config/code_bench_gov.json --print-result

# 单测（不需要 docker）
/opt/conda/envs/loopai/bin/python -m pytest tests/test_code_eval_container.py -q
```

## 5. 已知边界

* **判分镜像不钉版本**：`latest` 会随上游重建，数据集/超时口径可能跟着变。要复现旧分数就
  用 `CODE_EVAL_IMAGE=ganler/evalplus:<tag 或 digest>` 固定住。
* **容器 `--rm`，ground-truth 缓存不跨次保留**：每次判分都会重算一遍期望输出
  （HumanEval ~20s，MBPP+ 更久）。想省这点时间可以把宿主机缓存一起挂进去
  （`-v ~/.cache/evalplus:/root/.cache/evalplus`，注意必须挂一份**含数据集**的缓存目录，
  否则容器离线拉不到数据）。
* 判分超时是 evalplus 写死的（新版 `min_time_limit=4s` + `gt_time_limit_factor=4`），
  Judger 不提供覆盖开关 —— 改它等于换口径，分数不可与公开数字比较。
* 判分口径里的抽取是 **evalplus 自己的** `sanitize`（tree-sitter）；宿主机
  `utils/sanitize.py` 那份只用于留档对比。两者对「哪段是答案」的判断可能不同，
  `<bench>_sanitized.jsonl` 和 `<bench>_sample-sanitized.jsonl` 对着看就能定位
  是提取差异还是模型本身的问题。
* 挂载路径一律绝对：`output_dir` 默认是相对路径，`_bench_paths` 会 resolve 一次
  （docker 只接受绝对路径，塞相对路径报的是 `includes invalid characters for a local
  volume name`，跟挂载看起来毫无关系）。
