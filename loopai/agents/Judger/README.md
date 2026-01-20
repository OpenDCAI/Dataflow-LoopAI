# Judger Agent说明文档
Judger 是 Dataflow-LoopAI 中用于 **模型样例生成和样例编译评测** 的评测型 Agent。  
该模块基于符合格式要求的Jsonl数据文件，在不重新运行模型的前提下，对问题集生成所需的样例，并自动评测。期间会产出样例文件(Jsonl文件)和评测结果文件(Jsonl)

Judger 主要面向 **代码生成（Code Generation）** 与 **Text-to-SQL / SQL 生成** 等任务场景，强调评测的**系统性与复用性**。

## 一、功能概览

Judger 提供以下核心能力：

- 数据格式化（目前暂时适配human-eval数据集）
- 样例生成（支持自定义样例数）
- 样例评测（生成评测结果，并附带错误类型）
- 输出评测日志以及生成、评测结果
---

## 二、Pipeline 结构

Judger 由五个顺序执行的 Node 构成，形成一条线性分析流水线：

```text
check_required_fields
        ↓
vllm_kill_node
        ↓
vllm_start_node
        ↓
data_format_node
        ↓
generate_node
        ↓
evaluate_node
        ↓
vllm_kill_node
```
---
## 三、Node 功能说明

### 1️⃣ check_required_fields（参数验证）

🟢 输入
- 配置的参数

🟡 功能
- 判断是否有必要参数缺失
- 判断用户是否已经自定义成功启动了vllm服务，如果已启动将会跳转至`data_format_node`

🔵 输出
- 缺失的参数信息

### 2️⃣ vllm_kill_node（vllm启动）

🟡 功能
- 当`eval_base_url`未填写时，将先确保本地vllm关闭，确认只启动一次进程

### 3️⃣ vllm_start_node（vllm启动）

🟢 输入
- eval_vllm_port【int】: vllm本地启动参数——port，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效。例如8911。
- eval_vllm_tensor_parallel_size【int】: vllm本地启动参数——tensor_parallel_size，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效。例如1。
- eval_vllm_gpu_memory_utilization【float】: vllm本地启动参数——gpu_memory_utilization，用于本地启动vllm服务的参数之一，当参数eval_base_url未设置或为空时生效。例如0.9。
- eval_env_configs【str】：评估模型vllm启动环境参数。例如`{"CUDA_VISIBLE_DEVICES": "0","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}`单行环境配置的字符串，内容为json格式的配置项内容。

🟡 功能
- 如果这四个配置参数存在空值且`eval_base_url`已配置，则认为是用户已提前启动了vllm。否则将调用这四项参数启动vllm。
	
### 4️⃣ data_format_node（格式匹配）

🟢 输入
- eval_format_type【str】【选项】：评估模型问题格式化类型。例如`human-eval`。
- eval_problem_path【str】：评估模型问题路径。例如`/root/brjverl/dataflow/examples/scripts/data/human-eval-v2-20210705.jsonl`。
- output_dir【str】：输出文件目录。例如`/root/brjverl/dataflow/examples/scripts/output/`。

🟡 功能
- 如果eval_format_type不为空，则会进行数据格式化的过程，将原始数据转换为符合评测要求的格式的数据。目前仅适配human-eval，以及dev_bird_for_oj。输出的文件将会以 {{problem文件的名称}}_format.jsonl 存放于 output_dir/{{task_id}}/ 下。后续样例生成及评测将以该文件进行。
- 如果eval_format_type不为空，则后续样例生成及评测将以{{eval_problem_path}}的文件进行。

🔵 输出
- eval_format_type不为空情况下输出格式化后的×××_format.jsonl文件。
	
### 5️⃣ generate_node（样例生成）

🟢 输入
- data_format_node所选用的问题集。
- eval_task_type【str】【选择】: 问题类型。可选`code`、`text2sql`。
- eval_text2sql_dir【str】: 评估模型text2sql数据库目录。仅当问题类型为`text2sql`时生效。
- eval_batch_size【int】: 批处理数量。默认为10。
- eval_case_num【int】: 单问题生成样例数。默认为10。

🟡 功能
- 生成样本，以 {{problem文件的名称}}_sample.jsonl 存放于 output_dir/{{task_id}}/ 下。

🔵 输出
- 样本×××_sample.jsonl文件。

### 6️⃣ evaluate_node（评测样例）

🟢 输入
- generate_node所生成的样例集。
- data_format_node所选用的问题集。

🟡 功能
- 评测样本，从生成的文本中提取代码部分进行拼接评测。记录通过情况以及错误信息。以 {{problem文件的名称}}_result.jsonl 存放于 output_dir/{{task_id}}/ 下。
- 记录结果于output_dir/{{task_id}}/log.txt中。

🔵 输出
- 评测结果×××_result.jsonl文件。
- 评测记录log.txt文件

## 四、评测数据格式要求
### code
- 任务编号（task_id）：题号，如`{问题集名}/{序号}`。
- 问题提示词（prompt）：函数定义+问题描述提示（以多行注释形式写在函数定义下），为了减少处理过程，需保证大模型生成的结果为完整函数。如`def return1():\n    \"\"\"This function has no input parameters, and your task is to make it return the integer 1.\n    \"\"\"`。
- 进入测试函数名（entry_point）：如`return1`。
- 标准程序（canonical_solution）：如`def return1():\n    return 1`。需要完整的(包含函数定义)代码。
- 测试用例（test_list）：如`["assert return1() == 1"]`。需要为测试用例列表，其中的函数名需要和`entry_point`一致。
### text2sql
- 任务编号（task_id）：题号，如`{问题集名}/{序号}`。
- 问题提示词（prompt）
- 数据库（db_id）：如`toxicology`。`toxicology.sqlite`数据库文件在`{{eval_text2sql_dir}}`下的`toxicology`目录下。
- 测试问题（question）：如`What is the highest eligible free rate for K-12 students in the schools in Alameda County?`
- 标准回答（ground_truth）：如`def return1():\n    return 1`。需要完整的(包含函数定义)代码。

## 五、运行样例
```
# %%
from loopai.agents import JudgerAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

# with open('api_key.txt', 'r') as f:
#     api_key = f.read().strip()

sg = JudgerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "1"}}

# %%
graph = sg()

graph.invoke({
    "judger":{
        'eval_model_path': '/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/',
        # 'eval_base_url': 'http://127.0.0.1:8911/v1',
        'eval_base_url': '',
        'eval_api_key': "EMPTY",
        'eval_temperature': 0.7,
        'eval_top_p': 0.95,
        'eval_task_type': 'text2sql',
        'eval_problem_path': '/root/brjverl/dataflow/examples/scripts/data/dev_bird_for_oj_1.jsonl',
        'eval_format_type': '',
        'eval_text2sql_dir': '/root/brjverl/dataflow/examples/scripts/database/',
        'output_dir': '/root/brjverl/dataflow/examples/scripts/output/',
        'eval_batch_size': 10,
        'eval_case_num': 1,
        'eval_vllm_port': 8911,
        'eval_vllm_tensor_parallel_size': 1,
        'eval_vllm_gpu_memory_utilization': 0.9,
        'eval_vllm_command': 'python -m vllm.entrypoints.openai.api_server --model /root/brjverl/models/Qwen2.5-Coder-7B-Instruct/ --port 8911 --tensor-parallel-size 1 --trust-remote-code --gpu-memory-utilization 0.9 --enable-auto-tool-choice --tool-call-parser hermes',
        'eval_env_configs':'{"CUDA_VISIBLE_DEVICES": "0","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}'
    },
    "task_id": 10002,
}, config=config)

# %%
```
