"""
Skill tools for DatasetAgent ReAct loop.

These tools allow the model to discover available domain skills and
inject skill-specific guidance as ToolMessage context.
"""
from __future__ import annotations

import json
from typing import Dict

from langchain_core.tools import tool
from pydantic import BaseModel, Field


TEXT2SQL_FEWSHOT = """你已选择 skill: text2sql。

用途:
- 在准备产出 SFT 映射计划前，为 text2sql 数据提供可复用的映射参考。
- 这是参考模板，不可照抄字段名；必须依据当前数据样本真实字段输出。

推荐拼接策略:
- system: schema + evidence
- user: reasoning + question
- assistant: sql

合格判定要求:
- 在 mapping plan 中必须输出:
  - "quality_label": "qualified" 或 "unqualified"
  - "quality_reason": 说明判定依据
- 对于“单字段混合问题与SQL（如一个 messages 字段既有问题又有SQL）且无稳定切分规则”的数据，建议标记为:
  - "quality_label": "unqualified"
  - 并将 messages 置空或仅保留不可自动处理说明

## 常见格式示例 A: SFT messages 形态
源字段示例:
- schema, evidence, reasoning, question, sql, db_id

映射示例:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "字段边界清晰，可自动映射",
  "messages": [
    {"role": "user", "content": ["reasoning", "question"], "loss_mask": false},
    {"role": "assistant", "content": "sql", "loss_mask": true}
  ],
  "system": ["schema", "evidence"],
  "field_joiners": {
    "system": "\\n\\n",
    "user": "\\n",
    "assistant": "\\n"
  },
  "meta_fields": {"db_id": "db_id"}
}
```

## 常见格式示例 B: Alpaca/指令形态
源字段示例:
- instruction, input, output
- 或 instruction, schema, evidence, output

映射建议:
- user <- instruction
- system <- input (若 input 实际为 schema/evidence，可作为 system 上下文)
- assistant <- output

映射示例:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "instruction/input/output 三段结构稳定",
  "messages": [
    {"role": "user", "content": "instruction"},
    {"role": "assistant", "content": "output"}
  ],
  "system": "input",
  "meta_fields": {}
}
```

## 常见格式示例 C: 扁平 question/schema/sql 形态
源字段示例:
- question, schema, sql
- 可选 evidence, reasoning

映射示例:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "question/schema/sql 字段语义清晰",
  "messages": [
    {"role": "user", "content": ["reasoning", "question"]},
    {"role": "assistant", "content": "sql"}
  ],
  "system": ["schema", "evidence"],
  "field_joiners": {"system": "\\n\\n", "user": "\\n"}
}
```

## 单字典 + record_path 形态
若顶层为单字典，样本在某个列表键下（例如 items），使用:
- record_path: "items"
- content/system 支持路径字段: "task.question", "context.schema", "answer.sql"

## 不合格示例（单字段混合）
```json
{
  "category": "SFT",
  "confidence": 0.4,
  "quality_label": "unqualified",
  "quality_reason": "单字段 messages 混合问题与SQL，无法稳定自动拆分",
  "messages": null,
  "system": null
}
```

请在最终输出中只给出符合 JSON Schema 的 mapping plan。"""


QA_FEWSHOT = """你已选择 skill: qa。

用途:
- 在准备产出 SFT 映射计划前，为问答/选择题数据提供可复用的映射参考。
- 支持常见 QA 数据形态：question+answer、context 抽取式 QA、多选题（options 与答案分离）以及开源数据集的嵌套结构。
- 当数据包含 reasoning 时，建议保证 reasoning 与最终 answer 分离。

合格判定要求:
- 在 mapping plan 中必须输出:
  - "quality_label": "qualified" 或 "unqualified"
  - "quality_reason": 说明判定依据
- 当以下情况任一成立，建议标记为:
  - "quality_label": "unqualified"
  - 并将 messages 置空或仅保留不可自动处理的说明
- “合格”的关键条件（多选题场景）:
  - 题目与选项可分开引用（question 与 options 独立字段或可路径拆分）
  - reasoning 与 answer 可分开引用（system 指向 reasoning，assistant 指向 answer）

## 常见格式示例 1: 通用对话式 QA（question + answer）

json
[
  {
    "question": "苹果的主要产地有哪些？",
    "answer": "苹果的主要产地包括中国、美国、意大利、法国、新西兰等国家。"
  },
  {
    "question": "水的沸点是多少摄氏度？",
    "answer": "在标准大气压下，水的沸点是100摄氏度。"
  },
  {
    "question": "什么是光合作用？",
    "answer": "光合作用是绿色植物利用光能，将二氧化碳和水转化为有机物并释放氧气的过程。"
  }
]

映射示例（SFT）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "question 与 answer 分离清晰，可直接映射；无须额外 system 上下文",
  "messages": [
    {"role": "user", "content": "question", "loss_mask": false},
    {"role": "assistant", "content": "answer", "loss_mask": true}
  ],
  "system": null,
  "field_joiners": {"user": "\\n", "assistant": "\\n"},
  "meta_fields": {}
}
```

## 常见格式示例 2: 抽取式 QA（SQuAD 风格：context + question + answer）

json
[
  {
    "context": "月球是地球唯一的天然卫星，直径约3476公里，距离地球约38万公里。",
    "question": "月球的直径大约是多少？",
    "answer": "3476公里"
  },
  {
    "context": "Python由Guido van Rossum于1991年首次发布。",
    "question": "Python是谁创造的？",
    "answer": "Guido van Rossum"
  }
]

映射示例（SFT）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "context 可作为 system 上下文，question 作为 user 输入，assistant 仅输出抽取答案",
  "messages": [
    {"role": "user", "content": "question", "loss_mask": false},
    {"role": "assistant", "content": "answer", "loss_mask": true}
  ],
  "system": "context",
  "field_joiners": {"system": "\\n\\n", "user": "\\n", "assistant": "\\n"},
  "meta_fields": {}
}
```

## 常见格式示例 3: 多选 QA（question + options 分开；reasoning 与 answer 分开）

json
[
  {
    "question": "下列哪个是哺乳动物？",
    "options": ["A. 金鱼", "B. 猫", "C. 麻雀", "D. 蛇"],
    "reasoning": "哺乳动物具有毛发并用乳汁喂养幼崽，猫符合上述特征。",
    "answer": "B"
  },
  {
    "question": "地球自转一周大约需要多久？",
    "options": ["A. 1小时", "B. 1天", "C. 1月", "D. 1年"],
    "reasoning": "地球自转周期约为 24 小时，因此答案是 1 天。",
    "answer": "B"
  }
]

映射示例（SFT；确保 reasoning/answer 分离）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "question 与 options 分开构造 user 输入；reasoning 作为 system 上下文；assistant 只监督输出 answer，满足推理/答案分离且可稳定映射",
  "messages": [
    {"role": "user", "content": ["question", "options"], "loss_mask": false},
    {"role": "assistant", "content": "answer", "loss_mask": true}
  ],
  "system": "reasoning",
  "field_joiners": {"system": "\\n\\n", "user": "\\n", "assistant": "\\n"},
  "meta_fields": {}
}
```

## 常见格式示例 4: 真实开源数据集风格（简化版，嵌套结构）

json
{
  "version": "1.0",
  "data": [
    {
      "title": "自然常识",
      "paragraphs": [
        {
          "context": "彩虹是气象中的一种光学现象，通常在雨后转晴时出现。",
          "qas": [
            {
              "question": "彩虹一般在什么时候出现？",
              "id": "q001",
              "answer": "雨后转晴时"
            }
          ]
        }
      ]
    }
  ]
}

映射示例（SFT；简化为每条 paragraph 的第 1 个 qas）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "record_path 选取到可包含 context 与 qas 的上层结构；system 取段落 context，user 取 qas.question，assistant 取 qas.answer，实现端到端监督分离",
  "record_path": "data",
  "messages": [
    {"role": "user", "content": "paragraphs.0.qas.0.question", "loss_mask": false},
    {"role": "assistant", "content": "paragraphs.0.qas.0.answer", "loss_mask": true}
  ],
  "system": "paragraphs.0.context",
  "field_joiners": {"system": "\\n\\n", "user": "\\n", "assistant": "\\n"},
  "meta_fields": {"id": "paragraphs.0.qas.0.id"}
}
```

## 不合格示例（无法稳定分离推理与答案）
```json
{
  "category": "SFT",
  "quality_label": "unqualified",
  "quality_reason": "reasoning 与 answer 混在同一字段中，或 answer 字段包含推理过程导致无法保证 assistant 只输出最终答案",
  "messages": null,
  "system": null
}
```

请在最终输出中只给出符合 JSON Schema 的 mapping plan。"""


CODE_GENERATE_FEWSHOT = """你已选择 skill: codegenerate。

用途:
- 在准备产出 SFT 映射计划前，为 code generation / code-writing 领域数据提供可复用的映射参考。
- 支持常见的“题目描述/函数签名 -> 目标实现代码 -> 测试约束”这种结构。
- 这是参考模板，不可照抄字段名；必须依据当前数据样本真实字段输出。

推荐拼接策略:
- humaneval 风格（参考字段: prompt/canonical_solution/test/entry_point）:
  - system: entry_point + test（让模型理解测试约束）
  - user: prompt（函数签名 + docstring/说明）
  - assistant: canonical_solution（目标实现代码）
- mbpp 风格（参考字段: text/code/test_list/test_setup_code）:
  - system: test_setup_code（若非空）+ test_list
  - user: text（题目文字描述）
  - assistant: code（目标实现代码）

合格判定要求:
- 在 mapping plan 中必须输出:
  - "quality_label": "qualified" 或 "unqualified"
  - "quality_reason": 说明判定依据
- 当以下情况任一成立，建议标记为:
  - "quality_label": "unqualified"
  - 并将 messages 置空或仅保留不可自动处理的说明
  - 例如: 目标实现代码字段无法确定；或题目描述与实现代码混在同一字段且无稳定切分规则。
- 你必须确保:
  - messages 至少包含 user 与 assistant 两个 role
  - assistant role 的 content 映射到“实现代码”而非测试代码

## 常见格式示例 A: humaneval（第一条样本）

源字段示例（humaneval 的第一条，节选/完整）:
- prompt:
```text
from typing import List


def has_close_elements(numbers: List[float], threshold: float) -> bool:
    \"\"\" Check if in given list of numbers, are any two numbers closer to each other than
    given threshold.
    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)
    False
    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)
    True
    \"\"\"
```
- canonical_solution:
```text
    for idx, elem in enumerate(numbers):
        for idx2, elem2 in enumerate(numbers):
            if idx != idx2:
                distance = abs(elem - elem2)
                if distance < threshold:
                    return True

    return False
```
- entry_point: has_close_elements
- test:
```text
METADATA = {
    'author': 'jt',
    'dataset': 'test'
}


def check(candidate):
    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True
    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05) == False
    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.95) == True
    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.8) == False
    assert candidate([1.0, 2.0, 3.0, 4.0, 5.0, 2.0], 0.1) == True
    assert candidate([1.1, 2.2, 3.1, 4.1, 5.1], 1.0) == True
    assert candidate([1.1, 2.2, 3.1, 4.1, 5.1], 0.5) == False
```

映射示例（SFT）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "prompt 提供函数签名/说明，canonical_solution 提供目标实现代码，test + entry_point 提供测试约束，分离清晰且可稳定映射",
  "messages": [
    {"role": "user", "content": "prompt", "loss_mask": false},
    {"role": "assistant", "content": "canonical_solution", "loss_mask": true}
  ],
  "system": ["entry_point", "test"],
  "field_joiners": {
    "system": "\\n\\n",
    "user": "\\n",
    "assistant": "\\n"
  },
  "meta_fields": {"task_id": "task_id"}
}
```

## 常见格式示例 B: mbpp（第一条样本）

源字段示例（mbpp 的第一条，节选/完整）:
- text:
```text
Write a function to find the longest chain which can be formed from the given set of pairs.
```
- code:
```text
class Pair(object): 
	def __init__(self, a, b): 
		self.a = a 
		self.b = b 
def max_chain_length(arr, n): 
	max = 0
	mcl = [1 for i in range(n)] 
	for i in range(1, n): 
		for j in range(0, i): 
			if (arr[i].a > arr[j].b and
				mcl[i] < mcl[j] + 1): 
				mcl[i] = mcl[j] + 1
	for i in range(n): 
		if (max < mcl[i]): 
			max = mcl[i] 
	return max
```
- test_list（前几条/完整列表形式）:
```text
['assert max_chain_length([Pair(5, 24), Pair(15, 25),Pair(27, 40), Pair(50, 60)], 4) == 3',
 'assert max_chain_length([Pair(1, 2), Pair(3, 4),Pair(5, 6), Pair(7, 8)], 4) == 4',
 'assert max_chain_length([Pair(19, 10), Pair(11, 12),Pair(13, 14), Pair(15, 16), Pair(31, 54)], 5) == 5']
```
- test_setup_code:（为空字符串）

映射示例（SFT）:
```json
{
  "category": "SFT",
  "quality_label": "qualified",
  "quality_reason": "text 提供题目描述，code 提供目标实现代码，test_list 提供单元测试断言约束，分离清晰且可稳定映射",
  "messages": [
    {"role": "user", "content": "text", "loss_mask": false},
    {"role": "assistant", "content": "code", "loss_mask": true}
  ],
  "system": ["test_list"],
  "field_joiners": {"system": "\\n\\n", "user": "\\n", "assistant": "\\n"},
  "meta_fields": {"task_id": "task_id"}
}
```

## 不合格示例（无法稳定分离实现与约束）
```json
{
  "category": "SFT",
  "quality_label": "unqualified",
  "quality_reason": "数据把题目描述、目标实现代码与测试断言混在同一字段，且缺少稳定切分规则，自动映射风险高",
  "messages": null,
  "system": null
}
```

请在最终输出中只给出符合 JSON Schema 的 mapping plan。"""


SKILL_REGISTRY: Dict[str, Dict[str, str]] = {
    "text2sql": {
        "description": (
            "Text-to-SQL dataset mapping helper: supports schema/evidence -> system, "
            "reasoning/question -> user, sql -> assistant."
        ),
        "fewshot": TEXT2SQL_FEWSHOT,
    },
    "codegenerate": {
        "description": (
            "Code generation dataset mapping helper: supports instruction/prompt + tests -> system/user, "
            "implementation code -> assistant."
        ),
        "fewshot": CODE_GENERATE_FEWSHOT,
    },
    "code_generate": {
        "description": (
            "Alias of codegenerate (kept for compatibility)."
        ),
        "fewshot": CODE_GENERATE_FEWSHOT,
    },
    "qa": {
        "description": (
            "QA dataset mapping helper: question+options -> user, reasoning -> system, answer -> assistant."
        ),
        "fewshot": QA_FEWSHOT,
    },
    "questionanswer": {
        "description": (
            "Alias of qa (questionanswer)."
        ),
        "fewshot": QA_FEWSHOT,
    }
}


class ApplySkillInput(BaseModel):
    skill_name: str = Field(..., description="Name of the skill to apply, e.g. text2sql")


def create_list_skills_tool():
    @tool
    def list_skills() -> str:
        """List available skill names and short descriptions."""
        payload = {
            "skills": [
                {"name": name, "description": info["description"]}
                for name, info in SKILL_REGISTRY.items()
            ],
            "usage_hint": (
                "If one skill matches your domain, call apply_skill with that skill_name "
                "before final mapping generation."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return list_skills


def create_apply_skill_tool():
    @tool(args_schema=ApplySkillInput)
    def apply_skill(skill_name: str) -> str:
        """Apply a skill by name and return guidance/few-shot context."""
        key = (skill_name or "").strip().lower()
        if key not in SKILL_REGISTRY:
            payload = {
                "error": f"skill '{skill_name}' not found",
                "available_skills": sorted(SKILL_REGISTRY.keys()),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        payload = {
            "skill": key,
            "note": (
                "This is reference guidance from skill library. "
                "Do not copy field names directly; adapt to current dataset."
            ),
            "fewshot": SKILL_REGISTRY[key]["fewshot"],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    return apply_skill
