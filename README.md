# Dataflow-LoopAI

Dataflow-LoopAI is an intelligent system with self-optimization capabilities that automatically detects and evaluates generation deficiencies in LLMs within specific domains. Through dialog-based active data retrieval and self-driven optimization mechanisms, it enables continuous co-evolution between data and models.

```markdown
User  ⇄  Manager（控制逻辑） ⇄  LangGraph（状态机）
                 │
                 ├── 普通问答：直接返回
                 └── 复杂任务：进入图（评估 → 挖掘 → 训练）
```