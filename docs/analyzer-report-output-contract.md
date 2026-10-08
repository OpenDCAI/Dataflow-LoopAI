# Analyzer 统一报告交付格式

Code、Text2SQL 使用与 Math Rollout 报告相同的七份文本和一个训练计划 JSON，另外附带一份包含错因与短评的新 OJ。不改变 Judger 的原始评分；在报告收尾阶段按 Bench 分开生成交付目录，并自动关联此前已完成版本。

## 固定文件

| 文件 | 内容 |
| --- | --- |
| `01_数据集背景与评测概览.txt` | 数据用途、输入来源、全量通过/失败计数与逐次正确率 |
| `02_完整分析与审计报告.txt` | 全量失败审计、模型分析、分桶依据、Rollout、训练阶段证据及跨轮对比 |
| `03_最终报告.txt` | 可直接汇报的总结、SFT 二分结论、RL 小试建议与前后变化 |
| `04_模型改进建议.txt` | 需要补强的具体能力与验证方法 |
| `05_数据爬取与构造建议.txt` | 数据来源、样本结构、能力桶预算及质量验收 |
| `06_Rollout五档能力分析.txt` | 按同题同轮正确比例分五档，归纳各档题型与全部已有失败短评 |
| `07_SFT与RL训练阶段评估.txt` | 是否达到 SFT 转段条件、是否建议 RL 小试、支持与反对证据 |
| `08_training_plan.json` | 布尔决策、训练领域 tag、SFT/RL 用途和题号溯源 |
| `09_oj_enriched.jsonl` / `.json` | 原始 Judger OJ 的增强副本，错误样本增加总错因和一句话短评 |

文本为 UTF-8 BOM、CRLF 换行，便于 Windows 打开。JSON 使用标准 UTF-8；正文不附加原始 JSON 数据。旧时间戳报告、checkpoint、历史索引和模型阶段缓存留在运行目录，不放进九文件交付目录。

## 目录与下游入口

Code/Text2SQL 默认位置：

```text
<runtime_output_dir>/评测最终报告/<code或text2sql>/<Bench>/
```

`report_bundle_root` 可修改总目录；相对路径相对于本轮版本目录。Math 已有的目录及 `math_*` 路径字段保持兼容。Math Rollout 同时提供下面的通用清单，下游不必依赖中文父目录名或时间戳。

统一从返回状态的 `analyzer.report_artifacts` 读取：

`run_analyzer_standalone_payload(...)` 返回的 `data.result.report_artifacts` 也提供同一清单。CLI 的 `--print-result` 输出最终 state，应读取其中的 `analyzer.report_artifacts`；Skill `run(...)` 的成功包则从 `data.state.analyzer.report_artifacts` 读取。

```json
{
  "Bench名称": {
    "schema_version": "1.0",
    "task_type": "code",
    "dataset": "Bench名称",
    "directory": "实际生成的绝对目录",
    "files": {
      "summary": "01文件路径",
      "report": "02文件路径",
      "final_report": "03文件路径",
      "suggestions": "04文件路径",
      "obtainer": "05文件路径",
      "rollout": "06文件路径",
      "training": "07文件路径",
      "training_plan": "08文件路径",
      "enriched_oj": "09文件路径"
    }
  }
}
```

单 Bench 额外提供 `training_plan_path`、`rollout_report_path`、`training_stage_report_path`、`enriched_oj_path`。多 Bench 请遍历清单或 `enriched_oj_paths`，不使用单文件别名。Code/Text2SQL 的内部 `analyze_output_summary_path` 仍指向 JSON，避免破坏断点续跑；交付概览从 `files.summary` 读取。

## Code：按 Bench 读取 Judger / EvalPlus 产物

Code 支持新的 EvalPlus Bench 文件组，也保留旧 `passed` / `correct` 布尔值 OJ 的读取。Bench 名称来自显式配置、Judger summary 的 `task` 或文件名前缀，不限定为 HumanEval / MBPP。

| Judger 文件 | Analyzer 用途 |
| --- | --- |
| `<bench>_result.jsonl` | 主输入，逐次作答的 `solution`、`base_status`、`plus_status`、失败输入和身份 |
| `<bench>_summary.json` | 主评分口径 `pass_source`、数据集哈希、官方 pass@k 与计数校验 |
| `<bench>_sample-sanitized_eval_results.json` | 可选原始评测证据，与主结果核对内容和哈希 |
| `<bench>_sample-sanitized.jsonl` | 可选实际送测代码，核对是否与主结果的 `solution` 一致 |
| `<bench>_sanitized.jsonl` | 可选自行提取代码，审计与实际送测代码的差异 |
| `<bench>_sample.jsonl` | 可选原始模型回答留档，不能替代实际送测代码判因 |

先解压归档并保留每个 Bench 的文件组。`analyzer.eval_result_path` 可指定单个结果文件、对应 summary、单 Bench 目录、包含多个 Bench 子目录的父目录，或上述路径的列表。目录发现仅扫描本层，或本层无结果时扫描下一层，不递归扫描历史版本。不要传入 sample、sanitized 或原始 eval_results 作为主输入。

```json
{
  "analyzer": {
    "analyze_task_type": "code",
    "eval_result_path": ["./judger/humaneval/", "./judger/mbpp/"]
  }
}
```

也兼容 `judger.bench_result` 的路径映射与 `{ "Bench名称": { "result_path": "...", "task_type": "code" } }`。每个 Bench 生成独立的九文件目录。

评分不重新调用评测器：`pass_source=plus` 要求 base 与 plus **同时**为 `pass`；`pass_source=base` 只看 base。这与 [EvalPlus 官方评测实现](https://github.com/evalplus/evalplus/blob/master/evalplus/evaluate.py) 一致。增强测试在报告中以 `+` 后缀区别基础测试；summary 缺失时默认 plus 并明确警告，不把缺失状态当成失败。未知状态、重复作答标识或已提供产物相互冲突会中止并报告原因。

01/02/03 报告补充基础通过、增强通过、基础通过但增强失败，以及清洗/送测差异审计；08 JSON 提供 `evaluation_protocol` 和 `judger_evaluations`。Judger 的 pass@k 独立保留，不用样本通过率替换。各题 rollout 数不一致时，pass@1 按题等权，可能与逐次作答通过率不同。

判因只根据真实证据：`fail_tests` 是失败输入，不是期望值或异常栈；空数组不等于通过。原回答中的 Markdown 不直接算成实际代码的语法错误。原题干、参考答案未提供时，不把生成代码的 docstring 当作标准题干。配套文件按题号与明确的 completion_id / 唯一作答匹配，无法可靠配对时不按行号猜。

自行提取代码中存在、实际送测代码中缺失的函数单列为预处理差异，先复核清洗/送测链路，不能直接归因为模型能力缺陷。其失败仍进入全量统计，但不直接产生训练补数预算或训练领域需求。其他代码差异仅作为审计提醒，不自动认定为错误。每题只有一次作答时不据此推断多次采样稳定性。

## 新 OJ 的保留规则

- 不覆盖输入文件；保留全部成功与失败记录、记录顺序及原始字段值。
- 仅在错误记录增加 `overall_error_tag` 与 `short_critique`。Code/Text2SQL 输出 JSONL；Math 嵌套 Rollout 输入保留原 JSON 层级，两个字段加在具体失败的 `generations[]` 上，不修改题目级摘要。
- Code/Text2SQL 在已有逐条判因请求中同时请求一句话短评，不额外增加一轮逐条模型调用。兼容旧记录中的 `brief_analysis` / `judge.reason`；诊断缺失则明确标注，不编造内容。
- Code/Text2SQL 导出前校验源文件摘要、记录数、身份、作答与评分；源文件被修改或记录错位时拒绝拼接。
- 新 Code OJ 仍保留原始 `solution`、`base_status`、`plus_status` 等字段，不把内部归一化用的 `passed`、`completion` 或元数据强行加到公开 OJ；配套文件也参与来源摘要校验。
- 旧 checkpoint 若没有原始来源信息，只能复制当时保留的增强记录；`enriched_oj_sources[Bench].original_source_verified=false` 明确标识，不能承诺恢复已丢失的原字段。新运行保存来源信息。
- 所有错误都进入统计，短评抽样或正文题例数量不改变统计分母。

## 自动跨轮对比

标准目录为 `<output>/<task_id>/analyzer/<version_id>/`。同一任务、同一任务类型、同一 Bench 下，第二个及以后的版本自动比较最近一次已完成报告；第三轮起同时保留与首轮的累计对比。不同任务或不同 Bench 不自动混比。

同一版本续跑不算新一轮，恢复时保留原来可见的基准范围。报告完成后才登记历史索引；未完成、失败的版本不作为自动基准。旧版本没有索引时，只导入七份报告、训练 JSON 和可核验 OJ 均存在且计数一致的交付目录。

对比写入 02、03，以及 `08_training_plan.json.historical_comparison`；不增加第八份文本报告。状态中也提供 `historical_comparisons[Bench]`。内容包括：

- 两轮总作答、通过、失败、逐次正确率与全量错因次数变化。
- 按稳定题号与题干/参考答案/数据库身份匹配共同题目，比较每题全部作答的通过比例；不把随机 rollout 的第几个序号强行配对。
- 改善、退步、持平题数与最多各 20 个题例；题例截断不截断统计。
- 题目集合、采样参数、同题作答数及指标变化的审计提醒。指标未知或不一致时不计算可比提升；缺少可靠共同题目时明确标注证据不足。
- 新 Code 还检查 base/plus 口径、测试集哈希及评测器标识；口径变化、哈希不同或哈希缺失时，不计算可比提升。保留原 summary 和历史索引，才能核验新旧 Bench 版本。

对比是描述性证据，不是训练收益的因果证明。判因规则或判因模型变化也可能影响错因标签分布。

可以用 `baseline_result_paths` 按 Bench 指定历史 OJ，或单 Bench 使用 `baseline_result_path`，覆盖自动选择。对于没有指标元数据的平铺 Math 基准，需额外明确 `baseline_metric` 才能确认指标可比。指定路径无效时报告原因，不静默换用其他基准。不要删除旧报告目录及 `.analyzer_report_history`，否则无法追溯完整轮次。

## JSON 语义

三个方向共享 `schema_version`、`task_type`、`evaluation`、`sft_completed`、`is_sft`、`is_rl`、`domains`、`excluded_questions`、`input_warnings`、`historical_comparison` 等字段。

- `sft_completed`：本次评测范围是否满足预设 SFT 转段条件。只能是布尔值；证据缺失时为 `false`，不代表模型历史上没有做过 SFT。
- `is_sft`：是否建议继续 SFT 补强或准备 SFT 候选数据。
- `is_rl`：是否建议收集 RL 候选数据开展小规模试验，不授权自动启动训练。
- `domains[].tag`：训练领域；`question_tags` 保留题型标签；`training_stage` 为 `sft` 或 `rl`，对应 `is_sft` / `is_rl`。
- `tag_type=question_topic`：来自 Judger 题型或模型根据题干推断，具体来源在 `question_refs[].tag_source`。
- `tag_type=capability`：缺少题干时，仅根据已有诊断形成能力需求；此时 `question_tags=[]`，不把错因伪装成题型。
- `question_refs` 只用于溯源；不得直接把 benchmark 题目、答案或复刻题回收训练。

训练领域分流沿用 Math 的证据规则；Code/Text2SQL 原有的细粒度能力桶和预算算法仍用于 02/04/05，不用错误占比直接替代训练收益。

## 分档与证据边界

好：全部通过；较好：75% 至不足 100%；中等：50% 至不足 75%；较差：大于 0 至不足 50%；差：全部失败。

Code/Text2SQL 按 Bench、运行配置、题号及题干/数据库身份区分同题作答。统计使用 Judger 明确的 `passed` / `correct` 布尔值，或上述 EvalPlus 状态规则，不重新判分；重复作答标识或相互冲突的正误字段会报错。

所有失败均计入错因统计。短评优先读取 `short_critique`，其次原有 `brief_analysis` 或 `judge.reason`；错因兼容总标签、细粒度分类及 `judge.tags`。缺失诊断明确显示缺失，不编造。

如果每题只有一条，五档报告仍存在，但只有好/差有样本；不能推断多次采样稳定性。未声明预期采样数，或声明数与实际数不符，会写入审计警告。缺少 `formatted`、`truncated` 等证据不会补成通过；执行超时不等于生成截断，语法通过不等于格式合规。

`rl_readiness_thresholds`、`sft_completion_thresholds` 可覆盖工程初筛门槛；Math 原来的 `math_*_thresholds` 仍兼容。这些阈值不是研究证明的普适训练收益标准。

## 缓存与测试模式

报告新增模型阶段按输入、模型配置和提示内容缓存。已有的同一输入报告可以复用；新阶段失败不登记为成功，续跑时继续缺失阶段。不会添加输出 token 上限。

Code/Text2SQL 的逐批判因 checkpoint 同时核对输入文件及配套文件摘要、任务类型和 batch size，避免仅凭失败条数相同而复用另一份数据的判因结果。旧 checkpoint 没有此指纹时不复用逐批判因缓存。

`report_quick=true` 仅用于 Code/Text2SQL 的离线格式检查，生成明确标注“未调用模型”的规则版正文。默认是 `false`，会请求配置的分析模型。旧 `quick_brief` 仍只控制 Code/Text2SQL 的短评生成，不关闭报告模型评审。

Math 非 Rollout 输入仍保留原来的五份报告行为；本次统一对齐的是 Math 已有的七份 Rollout 交付格式，未改动 General Text 的链路。
