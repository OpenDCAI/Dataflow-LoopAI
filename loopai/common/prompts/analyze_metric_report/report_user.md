请根据以下 metric 评测信息，生成一份中文评测报告。

【输入信息】
- bench_name: {bench_name}
- eval_type: {eval_type}
- task_domain: {task_domain}
- total: {total}
- passed: {passed}
- accuracy: {accuracy}
- primary_metric: {primary_metric}
- primary_score: {primary_score}
- metric_overview_json: {metric_overview_json}
- top_err: {top_err}
- failure_patterns_json: {failure_patterns_json}
- quick_samples_json: {quick_samples_json}
- summary_json: {summary_json}

【任务要求】
你要基于这些信息，输出一份适合研发团队阅读的中文评测报告。报告要自然、专业、简洁，不能只是机械复述 JSON。

【输出结构要求】
请严格按以下结构输出：

【背景介绍】
- 用自然语言介绍该评测任务的目标、输入输出形式、评估重点。
- 必须基于 bench_name、eval_type、task_domain、quick_samples_json 和 summary_json 推断。
- 若信息不足，请说明“基于现有字段做保守判断”。

【评测结果】
- 总结整体表现
- 结合 primary_metric 和 metric_overview_json
- 指出模型优势与短板

【主要失败模式】
- 提炼 3~6 条失败模式
- 每条包含：现象 + 样本证据 + 对指标影响

【优化建议】
- 给出 3~6 条可执行建议
- 必须和失败模式一一对应

【约束】
- 中文输出
- 不要输出 JSON
- 不要复述输入
- 不要编造不存在的信息