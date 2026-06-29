你是“数据构造 + 评测改进 + 训练对齐”的专家。

【输入】
- bench_name: {bench_name}
- eval_type: {eval_type}
- task_domain: {task_domain}
- total={total}, passed={passed}
- primary_metric={primary_metric}, primary_score={primary_score}
- top_err={top_err}
- failure_patterns_json={failure_patterns_json}
- by_stage_json={by_stage_json}
- quick_samples_json={quick_samples_json}
- summary_json={summary_json}

【目标】
把评测结果转化为下一轮数据构造与训练策略。

【输出结构】

1) 失败模式画像
- 4~8 条失败模式
- 每条包含：触发特征 + 检索关键词 + 样本结构

2) 数据构造策略
- 6~12 条可执行方案
- 每条包含：来源 + 筛选规则 + 标签设计

3) 训练配方
- 数据比例（不同类型）
- SFT / DPO / RL 分配
- 如何构造偏好对

4) 评测与奖励改进
- reward 设计
- 判因字段补充
- 测试用例优化

5) 下一轮优先级
- P0 / P1 / P2
- 每条带验收标准

【约束】
- 不要空话（禁止“增加数据”）
- 必须结合 quick_samples
- 如果信息不足，要说明假设
- 中文输出