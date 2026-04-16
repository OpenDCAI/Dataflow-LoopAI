node_docs = {
        """QueryUnderstandNode": "解析 user_query, 将自然语言需求转换为结构化字段:
        is_eval_task / domain / specific_benches / model_path / special_request 等，
        如果不是重新推荐bench的任务而是做其他任务需要跳转到此处。""",

        """BenchSearchNode": "基于解析后的需求，推荐合适的 benchmark 名称，并写入
         state.benches / state.bench_info, 记录两个子 Agent 的结果。如果需要重新推荐bench,
         则跳转到此处。

         注意一旦选择跳转到此处，请为state_update中更新domain和specific_benches两个字段值, 这两个字段的含义如下:
          - domain: 评测任务的领域，如 ["text", "math", "code", "reasoning", ...]，可以写多个标签，只要是相关的领域都可以，注意同一个标签可以写多个不同的别名，以方便检索时匹配，包括但不限于简写等
          - specific_benches: 由用户提出的必须评测的指定 benchmark 列表，没有则填写 None。
        注意这次针对的是用户新的需求而重设的这两个字段用于新的检索。
         """,
    }