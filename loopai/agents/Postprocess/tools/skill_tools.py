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


SKILL_REGISTRY: Dict[str, Dict[str, str]] = {
    "text2sql": {
        "description": (
            "Text-to-SQL dataset mapping helper: supports schema/evidence -> system, "
            "reasoning/question -> user, sql -> assistant."
        ),
        "fewshot": TEXT2SQL_FEWSHOT,
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
