# BIRD Text2SQL 判分镜像

这个镜像只负责执行和判分 SQL，不加载模型。输入是 Judger 的
`<bench_name>_sample.jsonl`，每行至少包含 `task_id`、`completion`、
`ground_truth`，以及 `db_file` 或 `db_id`。当前 Judger 生成的样本已经包含
`db_file`，所以无需修改样本才能手动试用镜像。

判分遵循经典 BIRD-SQL 的 EX 核心规则：在同一个 SQLite 数据库上执行预测 SQL
和标准 SQL，比较两者的**结果集合**；行顺序和重复行不影响对错。规则参考
[BIRD 官方评测代码](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/main/bird/llm/src/evaluation.py)。
本目录的 `bird_eval.py` 是针对 LoopAI 样本格式编写的独立入口，并非直接复制
官方评测脚本。默认每条 SQL 超时 30 秒；现有进程内 Text2SQL 判分默认 3 秒，
因此两种方式可能对慢查询给出不同结果。

## 本地构建

在仓库根目录运行：

```bash
docker build -t loopai-bird-eval:dev loopai/skills/Judger/docker/bird_eval
```

如果 Docker Hub 连接超时，也可以从
[Docker Official Images 的 AWS 公共镜像](https://gallery.ecr.aws/docker/library/python)
构建同一 Dockerfile：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm \
  -t loopai-bird-eval:dev loopai/skills/Judger/docker/bird_eval
```

镜像只包含 Python 标准库和判分程序，不包含 BIRD 数据库、题目或模型权重。
在 Mac 和 Linux 服务器上分别执行这条构建命令，会得到对应架构的镜像。

构建完成后，用 `docker image ls loopai-bird-eval` 查看本机镜像。
`loopai-bird-eval:dev` 是镜像名称与标签；仓库里的 Dockerfile 是构建配方。

Judger 的 Text2SQL `evaluate` 步骤由
`loopai/skills/Judger/utils/evaluate_bird.py` 调用此镜像；本机缺少镜像时会
自动执行 `docker build`。`setup.py` 只负责把构建文件打进 Python 安装包，
安装包时不会构建镜像。若默认 Docker Hub 无法访问，可在运行 Judger 前设置
`BIRD_EVAL_BASE_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm`。
镜像代码更新后，若本机已有同名镜像，需手动重新 `docker build` 才会生效。

## 判分

先将下面三个变量换成**本机绝对路径**。`DATABASES` 是
`dev_databases` 目录，每个数据库应位于
`$DATABASES/<db_id>/<db_id>.sqlite`。

```bash
SAMPLES=/absolute/path/to/bird_sample.jsonl
DATABASES=/absolute/path/to/dev_databases
OUTPUT=/absolute/path/to/bird_eval_output
mkdir -p "$OUTPUT"

docker run --rm --network none \
  --mount "type=bind,source=$SAMPLES,target=/input/samples.jsonl,readonly" \
  --mount "type=bind,source=$DATABASES,target=/databases,readonly" \
  --mount "type=bind,source=$OUTPUT,target=/output" \
  loopai-bird-eval:dev \
  --samples /input/samples.jsonl \
  --databases /databases \
  --results /output/bird_result.jsonl \
  --summary /output/bird_summary.json
```

样本中原来的 `db_file` 可以是服务器上的绝对路径。容器会从它提取 `db_id`，
到已挂载的 `/databases/<db_id>/<db_id>.sqlite` 读取数据库；不会尝试访问
样本里的服务器路径。

结果 JSONL 保留输入字段，并添加 `passed`、`result`（查询结果的前 200 字符）、
`error` 和 `completion_id`。单条 SQL 执行错误或超时会被记录为未通过；
数据库缺失或标准 SQL 错误则终止评测，避免产生误导性分数。

汇总 JSON 的 `pass_at_k` 与现有 Judger 一样使用 **0–1 小数**。仅当每题恰好
有一个回答时，`execution_accuracy_percent` 才是经典 BIRD EX 的**百分数**；
每题有多个回答时它为 `null`，应查看 `pass_at_k`。汇总还记录镜像里的 Python
和 SQLite 版本。BIRD 官方脚本还会按难度分组；当前 Judger 生成的样本没有
保留难度字段，因此这里暂不报告分组分数，也不计算 VES。

汇总的分母是输入 JSONL 中出现的题目数。正式跑 BIRD 时应再对照题目文件核对
题号和总数，避免漏题被当作正常分数；同一题号对应不同数据库、问题或标准 SQL
时，程序会直接报错。
Judger 自动调用时还会核对生成样本是否覆盖题目文件中的每道题，且每题样本数
等于配置的 `case_num`，避免生成不完整时给出偏高的分数。

## 验证

仓库测试会建立一个临时 SQLite 数据库，运行“正确结果 / 结果不符 / SQL 错误”
三个样本并检查结果文件，不依赖 BIRD 数据或第三方 Python 包：

```bash
python3 -m unittest discover -s tests -p test_bird_eval_container.py -v
```

镜像构建成功后，使用 `BIRD_EVAL_IMAGE=loopai-bird-eval:dev` 运行同一命令，
会额外运行镜像判分和 Judger 容器调用两项测试。

真实 BIRD 成绩还需要与团队保持相同的题目切分、数据库文件及模型生成设置。
