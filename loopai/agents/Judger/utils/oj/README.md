## 文件结构
├── data.py：处理json文件的读取<br>
├── evaluate.py：评估处理一般python代码文件，需要调用execution.py<br>
├── evaluate_sql.py：评估处理SQL代码问题文件，需要调用execution_sql.py文件<br>
├── execution.py：将拼接的代码执行并返回运行结果的运行文件<br>
├── evaluate_sql.py：将提取拼接的SQL代码执行并返回运行结果的运行文件<br>
├── generate.py：样例生成文件，包含了SQL的生成<br>
└── format.py：数据格式处理文件，将数据处理成符合评测要求的数据格式，当前已经适配humaneval的，其他格式需要自定义编写相应的处理函数<br>
## 使用说明
在judger_agent.py中设定好相应的节点和数据流<br>
```
builder.add_node("check_required_fields", self.get_check_required_fields_node())
builder.add_node("data_format", self.data_format_node)
builder.add_node("generate", self.generate_node)
builder.add_node("evaluate", self.evaluate_node)
builder.add_edge("check_required_fields", "data_format")
builder.add_edge("data_format", "generate")
builder.add_edge("generate", "evaluate")
builder.set_entry_point("check_required_fields")
builder.set_finish_point("evaluate")
```
如上设定了data_format数据格式处理节点、generate数据样例生成节点、evaluate数据评测节点。并且以先数据格式处理，再数据样例生成，最后数据评测的流程进行。


接着进行参数配置，到generate.py中检查主要生成入口函数generate_sample或generate_sample_sql的参数，其中num_samples_per_task代表每个问题生成的样例数量。

接着进行运行脚本的配置：<br>
```
# %%
from loopai.agents import JudgerAgent
from loopai.memory import checkpointer, store
from loopai.agents.Starter.tools.check_motivation import check_motivation

from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console()

# 使用API需要文件则使用
# with open('api_key.txt', 'r') as f:
#     api_key = f.read().strip()

sg = JudgerAgent(checkpointer=checkpointer, store=store)

# %%
config = {"configurable": {"thread_id": "1"}}

# %%
graph = sg()

graph.invoke({
    'eval_model_path': '/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/',
    'eval_base_url': 'http://127.0.0.1:8911/v1',
    'eval_api_key': "EMPTY",
    'eval_temperature': 0.7,
    'eval_top_p': 0.95,
    'eval_test_case_path': '/root/brjverl/dataflow/examples/scripts/sample/test_2.jsonl',
    'eval_problem_path': '/root/brjverl/dataflow/examples/scripts/data/test_2.jsonl',
    'eval_result_path': '/root/brjverl/dataflow/examples/scripts/result/test_2.jsonl',
    'eval_batch_size': 10,
}, config=config)

# %%
```
- `eval_model_path`表示模型的路径
- `eval_temperature`表示温度参数
- `eval_test_case_path`表示样例生成的存放路径
- `eval_problem_path`表示问题的存放路径
- `eval_result_path`表示结果的存放路径