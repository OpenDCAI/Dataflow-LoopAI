# Math 多 Rollout 输入与训练阶段报告

Math 路线支持 Judger 已完成评测的嵌套 JSON：

```json
{
  "data": [{"id": 1, "problem": "题干", "answer": 0}],
  "eval": [{
    "dataset": "math_benchmark",
    "val_n": 2,
    "results": [{
      "problem_id": 1,
      "ground_truth": 0,
      "val_n": 2,
      "num_correct": 1,
      "generations": [
        {"full_generation": "完整作答", "predicted_answer": "0", "correct": true, "formatted": true, "truncated": false},
        {"full_generation": "另一份完整作答", "predicted_answer": "1", "correct": false, "formatted": true, "truncated": false}
      ]
    }]
  }]
}
```

## 对齐口径

- 读取 `eval[].results[].generations[]`，而不是只读取 `data` 或题目级代表作答。
- 逐次 `correct` 必须为布尔值；不重新调用 OneEval 覆盖 Judger 的结论。报告主指标名称为 `judger_correctness`。
- 保留评测轮次、题号、rollout 序号；同题十轮不是十道独立题。
- `val_n` 与实际作答数不符时拒绝生成完整报告，避免把部分回传误认成全对。
- `num_correct` 或运行汇总与逐次评分冲突时报告审计差异，按逐次 `correct` 计算。
- `formatted`、`truncated` 缺失表示证据未提供，不计作自动通过。`extraction_rate` 在此输入中沿用 Judger 的 `formatted` 标记，不是重新计算的答案提取器结果。
- 原始输入文件不修改。增强 OJ 保留原 `data`、`eval`、每轮参数和每条 generation 的原有字段，仅给失败的 generation 增加 `overall_error_tag` 与 `short_critique`。

## 五档

分档单位为同一轮同一题的完整 rollout 组，使用该组的 N。边界不重叠。

| 档位 | 正确比例 | N=12 的正确次数 |
| --- | --- | --- |
| 好 | 100% | 12 |
| 较好 | 75% 至不足 100% | 9–11 |
| 中等 | 50% 至不足 75% | 6–8 |
| 较差 | 大于 0 至不足 50% | 1–5 |
| 差 | 0% | 0 |

先逐轮统计，再给出各档题型分布、逐题通过次数和同题跨轮变化。原文件有题型则保留；缺失时分析模型读取全部独立题干进行分类，包含全对题，不根据错误标签推断题型。

新 Rollout 报告按档读取**全部失败短评**，分批归纳后合并，每档记录覆盖数。这不受普通报告 `critique_samples_per_tag=5` 的抽样限制。推断出的题型、最终答案评分和错误归因属于不同证据，报告分别说明。

## 输出

原有五份文本报告继续生成。多 Rollout 输入额外生成：

1. `06_Rollout五档能力分析.txt`：各档题型、全部短评形成的错误画像、适合的独立训练数据、逐轮明细与跨轮波动。
2. `07_SFT与RL训练阶段评估.txt`：规则初筛、模型综合判断、支持和反对理由、缺失证据和验证方案。
3. `08_training_plan.json`：二分 SFT 转段结果、训练用途布尔值、需要收集的题型标签、具体题号依据和数据要求。正文仍为人类可读文字，不嵌入整段 JSON。

完整分析报告同时包含这两个章节，最终报告附上结论和阅读入口。Math 文本使用 UTF-8 BOM 和 CRLF，方便 Windows 阅读。

题型识别和已完成的短评分档归纳、训练阶段评审分别缓存在运行目录的 `rollout_report_cache`。模型归纳失败时不把规则兜底计为完整模型评审，可以从 `analyze_metric_report` 续跑。`metric_report_quick=true` 只生成规则预览，明确标注未进行模型评审。

## SFT / RL 判断依据

SFT 转段结论只输出是或否，RL 独立判断。JSON 的布尔值不是字符串：

- `sft_completed`：是否达到**本次评测范围**的预设 SFT 转段条件；必要证据缺失则保守判 `false`，在理由里解释缺项，而不是输出第三种结论。
- `is_sft`：是否建议继续 SFT 补强或收集 SFT 候选数据，**不是**“是否完成 SFT”。
- `is_rl`：是否建议收集 RL 候选数据用于小规模试验。`rl_scope="pilot_only"`，不自动触发训练。
- `domains[]`：`tag`/`question_tags` 引用已有或模型依据题干推断的题型；每条有 `training_stage`（`sft` 或 `rl`）、互斥的 `is_sft`/`is_rl`、`question_refs`、真实计数、理由及具体 `data_requirements`。

数据分流先按独立题目再合并题型，避免同题型的容易题掩盖困难题。同题跨轮描述性正确率低于 50% 优先 SFT；不低于 50%、存在轮内对错混合组且全局 RL 初筛通过时进入 RL 候选。全对题保留回归而非自动补数；评分异常或未知题型不直接变成训练需求。同一题型可有不同用途，但题目引用不重复。该初始规则不声称预测训练收益，应靠小试校准；模型基于全量短评归纳补充收集理由和样本要求，不覆盖计数及布尔值。

默认 SFT 转段条件须全部满足，且判因完整、配置可比和输入一致性检查通过。门槛不是论文定律，不代表认证训练历史或全部数学能力。可配置：

```json
"math_sft_completion_thresholds": {
  "min_rollout_accuracy": 0.90,
  "min_format_rate": 0.95,
  "max_truncation_rate": 0.05,
  "max_all_wrong_group_fraction": 0.05
}
```

- 支持信号：基础成功率、可可靠判分的输出格式、同题有对有错的轨迹、可针对性修复的题型和错误。
- 反对或限制信号：低成功率、大量截断、格式不稳定、疑似指标误判、没有组内奖励差异、缺少完整判因。
- 即使初筛通过，也仅建议有条件的小规模 RL 试验。SFT 转段的二分结果仅在上述评测范围和配置门槛下成立；全面放行还需要独立验证集和 Reward 审计。
- 在仅二元正确性 reward 的 GRPO 中，全对和全错组的组内相对优势为零。这不表示它们对所有 RL 算法或包含过程奖励的任务都没有用。
- 重复评测的跨轮合计不是独立题数、单轮 pass@120 或一个 GRPO 训练组。不同模型/采样配置应先逐轮比较。

默认门槛是工程初筛参数，不是论文提供的通用标准，可在 Analyzer 配置中覆盖：

```json
"math_rl_readiness_thresholds": {
  "min_rollout_accuracy": 0.5,
  "min_mixed_group_fraction": 0.1,
  "min_format_rate": 0.95,
  "max_truncation_rate": 0.05,
  "max_metric_anomaly_fraction": 0.05
}
```

方法参考：[DeepSeekMath / GRPO](https://arxiv.org/abs/2402.03300)、[DAPO 动态采样](https://arxiv.org/abs/2503.14476)、[DeepSeek-R1](https://arxiv.org/abs/2501.12948)。这些论文启发组内奖励差异与训练阶段设计；并没有验证上述默认数值门槛。

## 运行

在状态配置中设置 `analyzer.analyze_task_type="math"`，将 `analyzer.eval_result_path` 指向嵌套 JSON，填好分析模型与端点。API Key 沿用现有 `ANALYZER_API_KEY` 环境变量或 `analyzer.analyze_api_key` 配置，不写入版本库。

推理模型的思考与正文可能共享输出预算。若现有逐条判因预算导致空正文，可显式设置 `math_llmaj_max_output_tokens_per_case: null`，并不设置 `math_llmaj_max_tokens`；此时判因不发送输出 token 上限。未配置该字段时保留原有默认预算，报告正文的生成设置不变。

```bash
python -m loopai.skills.Analyzer.cli \
  --config-path /path/to/math_rollout_config.json \
  --thread-id math-rollout-analysis \
  --print-result
```

入口继续走 `metric_recommend -> metric_score -> math_llmaj_label -> analyze_metric_report -> finish`。前两个节点识别到该结构后直接导入已评测的逐次分数；旧的平铺 Math 和其他垂域输入保留原流程。

报告形成的能力需求应用于生成与评测题隔离的新训练数据，不能直接回收本 benchmark 的答案或复刻题训练后继续用同一测试集声称泛化收益。
