# ObtainerCLI / DataMixer task3 事故记录与修复验收计划

本文档记录 `task3` 的真实运行事故、可复现环境、已确认的根因、修复与验收顺序。它最初是 `refactor/obtainercli-datamixer` 分支的施工输入；后文的“修复后观察”与长跑证据会随真实验收同步更新，但仍不把未完成全链路验证的训练集视为可用训练产物。

## 1. 事故环境与证据

| 项目 | 实际值 |
| --- | --- |
| 工作分支 | `refactor/obtainercli-datamixer` |
| 项目目录 | `/workspace/binrui/code/Dataflow-LoopAI` |
| task 名称 / ID | `task3` / `5008d610-8b46-4930-89d2-ce4e8b36c20a` |
| LoopAI API | `http://127.0.0.1:8855` |
| Codex 默认模型 | `deepseek-chat`，经 LoopAI response proxy 访问 |
| 本地 vLLM | `qwen3-14b-fp8`，`http://127.0.0.1:8000/v1`，未开启自动工具调用 |
| Python 环境 | `envs/.loopai`，Python 3.12 |
| task3 lake | `.datamixer/finance_lake`，指针 `.loopai/lake.yaml` |
| task3 输出 | `outputs/5008d610-8b46-4930-89d2-ce4e8b36c20a/` |

关键证据：

- `outputs/<task>/acquisition/status.json`：managed acquisition worker 失败，错误是本地 vLLM 缺少 `--enable-auto-tool-choice` / `--tool-call-parser`。
- `.loopai/lake.yaml`：`obtainer_webagent=domain_data_acquisition`，但 `obtainer_webagent_model`、active run、campaign、L1 dataset 都为空。
- `/obtainer/webagent/overview`：L1/L2/L3 均为 0，所有 WebAgent stage 为 `waiting`。
- `outputs/<task>/acquisition/*.py`：外层 Codex 在 worker 失败后自行写入并执行 Hugging Face 下载、入湖和出湖脚本。
- `outputs/<task>/sft_dataset/train.jsonl`：99,482 行，格式可解析，但不代表语义或训练质量合格。

## 2. 先建立的全局契约

### 2.1 模型解析契约

1. ObtainerCLI、`dataset-acquisition-agent`、SearchAgent 和 WebAgent 的**默认模型**必须解析为当前 Codex 默认模型。
2. 缺少模型配置时不得静默回退到本地 vLLM、旧 Starter 默认模型或空模型。
3. 只有具体 LLM 算子允许显式覆盖模型；覆盖必须是该算子的明确参数或配置。没有显式覆盖时，仍使用 Codex 默认模型。
4. 每次 run 必须持久化并返回：`resolved_model`、`webagent_model`、模型来源（`codex_default` 或 `operator_override`）。

### 2.2 managed acquisition 契约

1. 外层 Codex 只能启动 `dataset-acquisition-agent`，不能在 worker 失败后改为手写下载、入湖或出湖脚本。
2. `start`、`status`、`resume` 都必须传同一个 `--run <run_dir>`。
3. worker 启动失败、WebAgent 启动失败、任一处理算子失败，都必须返回非零状态和可读错误；不能被 `nohup ... &`、`head`、管道或后台 shell 掩盖。
4. 正常 acquisition run 必须同时留下 SearchAgent 与 WebAgent 的 run/campaign 状态；WebAgent 必须至少产生可追踪的 L1 记录或明确的失败事件。

### 2.3 数据质量与出湖契约

1. 域标签不能只由文件名、数据集别名或来源名称决定；`--domain finance` 只
   是批次级 metadata。数据集 agent 走批量入湖路径：`dm ingest` 按批次 metadata
   （含必填 `--quality-level`）直接写入全部规范化行，不在入湖时逐条审批。
   逐条质量门禁属于后处理：WebAgent L2 的 `domain_classify`（接收 campaign
   的 `--focus-keywords`）+ `topic_quality_filter` 负责验证主题相关性、
   分类置信度、模型身份和至少两个能在原始内容中逐字回指的语义信号；
   `recipe export` 的 `quality_gates.finance` 仍是 finance 出湖专用门禁。
   来源信息只作 provenance 与质量报告审计，不作为接受或拒绝条件。
2. 去重、许可证、来源、质量检查、benchmark 去污、处理算子执行记录均是出湖前硬门槛。
3. 配比约束作用于最终 export，不作用于 raw lake。数量上限优先于比例；供给不足时必须重新计算可行的最终总量或明确失败，不能静默输出错误比例。

### 2.4 WebAgent 根 URL 契约

1. 搜索和页面检查由 LLM Agent 驱动。Agent 必须通过复数终止工具
   `submit_resource_urls` 一次性提交它在本次检索中判定相关的全部根 URL，不能只
   返回最佳单条 URL。
2. URL 判断采用五维 LLM rubric：`query_coverage`、`source_authority`、
   `content_substance`、`crawl_yield`、`complementary_value`，各维按 1-5 分给出
   可回指证据，再给出 `core`、`supporting`、`exclude` 或 `uncertain` 综合结论。
   单一低分不得否决 URL；`exclude` 至少需要两个独立弱维度和有证据的理由。
   旧 `relevance_score`/`relevant` 字段只保留兼容性，不作为门禁。
3. `core` 和 `supporting` URL 必须全部提交；`uncertain` 由主 Agent 结合页面证据
   复核。禁止确定性 URL 选择 fallback。模型未在步数上限内显式提交完整集合时，
   run 必须失败；不得按关键词分数、可抓取性或候选顺序自动选址，也不得用可抓取
   的无关页面替换被源站拒绝的相关页面。
4. 所有提交根共享 `max_pages` 与 `max_depth` 总预算，根 URL 必须先于子链接尝试。
   因页数预算而未抓取的根必须保留在 `selected_urls` 并写入明确失败记录，不能静默
   丢弃。CLI、campaign report、lineage 和 L1 provenance 必须返回完整根 URL 集合。
5. URL 规范化、去重、SSRF 校验、robots 和抓取预算仍是确定性安全机制；它们不
   得参与相关性判断或资源根选择。

### 2.5 长跑队列与可观测性契约

1. WebAgent、MinerU、DataFlow、逐条领域分类、金融质量门、QA 和 SFT 校验必须
   同时执行；每个算子之间都有 CAS-backed SQLite 持久队列。暂停/恢复不得重做
   已成功的后处理 job。
2. 每个 WebCrawler run 必须即时 flush `pages.jsonl`、`failures.jsonl` 和
   `progress.json`。即使 executor 被中断，也必须能区分 robots 合规跳过、源站
   403/404/405、timeout、DNS/SSRF 和 Playwright runtime 故障。
3. 严格 LLM 算子中单条语义输出无效时，批级重试耗尽后必须隔离到单条；不得把
   同批其他有效记录一起标为 terminal failure。API/transport 故障不得递归拆分，
   避免在服务不可用时放大请求。
4. 无 sudo 环境允许通过 `PLAYWRIGHT_RUNTIME_PREFIX` 使用用户态 Chromium 共享库
   和字体。正式验收必须真实打开普通网页，并验证源站 4xx 不会作为 HTML 入湖；
   单页 4xx 也不得污染后续 Playwright fallback。
5. 数十小时 campaign 必须由独立 watchdog 每 30 秒记录队列、失败类型、服务、
   RSS 和磁盘。terminal pipeline failure、`2001::1` 回归、浏览器系统故障、服务
   连续失联或资源越界时应及时安全中断；robots 与普通源站拒绝只统计、不误停。

## 3. 问题清单、根因与修复前测试

### 问题 1：WebAgent 没有真实加载

**现象**

- lake 中没有 active acquisition/campaign/L1 dataset；WebAgent L1/L2/L3 均为 0。
- 首次命令漏传必填 `--run`，却因后台命令打印成功文本而被误判为已启动。
- 后续补上 `--run` 后，worker 使用了没有工具调用能力的本地 Qwen，失败后外层 Codex 改为直接下载脚本。

**根因**

- 命令构造不符合 CLI 契约。
- 默认模型解析未与 Codex 默认模型统一，`obtainer_webagent_model` 为空。
- 失败没有阻断后续外层直接采集路径。

**先写的测试**

- 单元：默认模型解析为 Codex 默认模型；空值不能回退到本地 vLLM。
- 单元：每个 LLM 算子只有显式 override 时才偏离 Codex 默认模型。
- 集成：`start/status/resume` 缺少 `--run` 必须失败；带 `--run` 必须返回同一 run id。
- 真实 E2E：每个 run 都轮询 overview，断言 `resolved_model`、campaign id、L1 状态和最终事件存在；任一缺失即测试失败。
- 真实 E2E 失败场景：关闭或提供错误模型时，命令必须非零退出，不能进入手写下载 fallback。

**十个必须真实加载 WebAgent 的验收 query**

以下 query 均在真实当前 DataMixer lake 环境运行，每一个都必须生成独立 `--run` 目录并验证 WebAgent 已真实加载（模型非空、campaign/run 非空、L1 或明确失败事件存在）。禁止 mock、禁止用仅 SearchAgent 成功替代。

1. `为提升金融问答与财报解读能力，采集 70% 金融、20% 推理数学、10% 代码 SFT 数据；先登记金融 benchmark，再启动真实 WebAgent 采集权威金融网页。`
2. `面向上市公司财报分析，收集 SEC 10-K/10-Q、业绩电话会、财务比率解读数据，并同时采集推理和 Python 表格处理样本，最终按 70/20/10 出湖。`
3. `构建个人理财与税务问答 SFT 数据；WebAgent 必须采集政府或监管机构网页，SearchAgent 只负责发现可下载数据集。`
4. `构建银行信贷风险与反欺诈训练数据，要求启动 WebAgent 收集监管规则、风险披露与公开教育材料，并登记对应 benchmark。`
5. `构建证券投资组合分析数据，采集基金、债券、股票估值的权威网页，并混入数学推理与代码数据，最终控制到 70/20/10。`
6. `构建金融表格推理数据，重点采集财报和表格问答资料；每批 WebAgent L1 提交后立即进入正文提取、分类和 SFT QA，不等待整次抓取结束。`
7. `构建保险定价与理赔解释数据；请真实运行 WebAgent 和 SearchAgent 两条流，分别报告 run、campaign、L1/L2/L3 数量。`
8. `构建宏观经济指标解读数据，采集央行、统计机构和国际组织网页；要求每条出湖样本带来源和处理 lineage。`
9. `构建金融合规与反洗钱问答数据；必须先启动 WebAgent，若模型或 worker 失败则直接失败并报告，不得退化成临时下载脚本。`
10. `重新执行 task3 同类金融 SFT 需求：benchmark 注册、真实 WebAgent 网页采集、托管 acquisition worker、处理链路和 70/20/10 最终出湖。`

**验收顺序**

1. 先完成模型解析与 CLI `--run`/错误传播测试。
2. 修改模型解析、worker 命令构造和失败阻断逻辑。
3. 审查并修订 `skills/obtainer/SKILL.md` 与 `docs/OBTAINERCLI_USAGE.md` 的命令；实际命令必须含 `--run`，`status/resume` 必须复用该 run，且默认模型语义与实现一致。
4. 运行上述十个真实 query；逐个保存 run、campaign、L1/L2/L3、模型解析和失败原因。

### 问题 2：finance 标签存在严重语义污染

**现象**

- 79,633 条标为 finance 的记录里，`Finance_Alpaca_v2` 金融关键词覆盖约 36.8%，`gbharti_finance_alpaca` 约 55.9%。
- 抽样出现电影评论、宠物、旅游、通用营销、动物知识等非金融内容。

**根因**

- task3 下载脚本只从通用字段中取 `instruction/question/problem/prompt` 与 `output/answer/...`，非空即保留。
- 随后无条件把整份来源写成 `domain="finance"`；没有 schema 映射或逐条 LLM
  领域分类与金融语义证据验证。

**修复后观察**

数据集 agent 的批量入湖路径 `dm ingest` 只做 metadata 驱动写入：每条规范化
记录按批次 `--quality-level`、`--domain` 和 tags 直接入湖，不清除、不重新
分类、不逐条 approve/drop。`--domain finance` 只是批次级 metadata，不构成
逐条证明。逐条质量门禁移到后处理：WebAgent L2 的 `domain_classify`（campaign
`--focus-keywords`）+ `topic_quality_filter` 负责验证主题相关性、分类置信度、
模型身份和至少两个不同类别且 evidence 可在原始 content 中逐字找到的
`semantic_signals`；`recipe export` 的 `quality_gates.finance` 仍是 finance
出湖专用门禁。不满足门禁的组不得出湖并保留
`finance_quality_report.json`。来源 URI、数据集 ID 和来源名称继续写入
provenance/report，但不再配置或执行金融来源白名单。

问题 1 验收通过后，在十个真实 run 的最终 export 上统计字段有效率、金融分类
通过率、语义信号通过率、平均置信度、随机人工抽样和拒绝原因。不能只根据
数据集名称判断领域；任何最终样本缺少合格 LLM 分类证据时不得出湖。

### 问题 3：数据没有经过应有的后处理链路

**现象**

- task3 直接向 `obtainercli_audit/*.jsonl` 写入，再由临时 `build_sft_dataset.py` 直接拼接为 `train.jsonl`。
- 当前 lake 的 WebAgent L1/L2/L3 都为 0，没有网页提取、分类、grounded QA、校验等处理证据。
- 结果中缺少完整的 operator run、处理参数、质量 finding、source license、可追踪 lineage 和 export manifest。

**根因**

- managed worker 失败后，外层 Codex 绕过了 Obtainer Skill 的托管 acquisition 与出湖约束，手写下载/入湖/出湖脚本。
- 没有“未完成处理链路不得 export”的守卫，也没有把算子异常写回 agent 状态。

**真实样例联测（使用当前 lake）**

- 在当前 `.datamixer/finance_lake` 创建隔离测试 run/namespace，不覆盖现有 task3 文件。
- 选择带可识别来源、重复、非金融内容和 benchmark 重合项的真实记录。
- 断言入湖后依次有 processing operator run、输入/输出计数、拒绝清单、quality finding、lineage、export manifest；缺一项即拒绝 export。
- 人为触发一个算子失败，断言 CLI、run status、事件流和最终报告均显示失败；不能只在子进程 stderr 中消失。

### 问题 4：最终比例错误地从 raw 供给静默截断

**现象**

- 目标是 70/20/10，最终却是 finance 80.0%、reasoning/math 8.5%、code 11.4%。

**根因**

- `build_sft_dataset.py` 用 raw 总量计算三类 target；对每类执行 `min(target, available)` 后直接采样。
- reasoning/math 只有 8,473 条，低于约 22,752 条 target；脚本没有重新求可行总量、补齐来源或把失败暴露为不可出湖。

**与问题 3 的联测要求**

- 由真实处理链路的合格 L3/L4 记录，而不是 raw 文件，计算最终 export 配比。
- 数量限制优先于比例限制；当某类供给不足时，重新计算最大可行总量，或返回 `ratio_unreachable` 并阻断 export。
- export manifest 必须同时写入 raw 数、各处理阶段保留数、可用数、目标数、最终数和最终比例。

### 问题 5：benchmark 只是元数据登记，没有参与数据校验与去污

**现象**

- task3 的 `benchlist` 只有名称、类型、描述；没有 `problem_path`、split、来源或已加载的 benchmark 样本。
- 没有证据表明入湖或出湖时将 benchmark 样本与训练数据逐条比对。

**根因**

- benchmark 注册 API 仅写入 task state；后续 acquisition、ingest、operator、recipe/export 没有消费它。

**测试与验收**

- 测试先建立真实 benchmark fixture，注册后验证路径、格式和可读取样本。
- 对每个 benchmark 的 exact match、归一化 match、近重复样本，分别在入湖和出湖阶段验证去除/隔离。
- 入湖和出湖结果必须输出 `benchmark_decontamination` manifest：输入数、命中数、拒绝数、阈值、benchmark version/fingerprint。
- benchmark 路径不存在、格式非法或去污算子失败时，阻断 acquisition/export。

### 问题 6：出湖处理算子是否未执行或执行失败没有反馈

**现象**

- task3 最终文件由临时脚本直接写出；不存在可证明的出湖处理算子运行记录。
- 当前脚本只检查 instruction/output 非空，未做去重、领域、许可证、来源、质量、安全或 benchmark 检查。

**根因**

- 问题 3 的托管链路被绕过；同时 CLI/agent 缺少“算子未运行”“算子失败”“算子产物不完整”的统一失败反馈。

**与问题 3/4 的联合审查**

1. 对当前 lake 的真实记录跑完整处理与 export 测试，记录每个 operator 的输入、输出、过滤数、错误和 lineage。
2. 在一个算子中注入可预期失败，验证 parent run 失败、Web/API/CLI 同步可见，且不产生可训练 export。
3. 仅当问题 3 的链路完整、问题 4 的最终比例达标、问题 5 的 benchmark 去污通过后，才允许生成 SFT export。

### 问题 7：Tavily 单点失败会让深搜返回空结果

**修复前行为**

- 通用 `WebTools` 只正确区分 Jina 与 DuckDuckGo，其余 `search_engine`
  值（包括 `bing`）都会进入 Tavily。
- Tavily 缺少 key、返回错误或空结果时，只降级到可选的
  `langchain-community` DuckDuckGo 工具。运行环境缺少 `ddgs`、搜索站点触发
  验证码或结果文本没有稳定 URL 时，SearchAgent deepsearch 得到空结果。
- DataMixer WebCrawler 虽已有多个 provider，但显式 `tavily` 模式不执行
  fallback，也没有统一返回实际 provider、失败尝试和 LLM 摘要状态。

**实现后的契约**

1. `auto` 和显式 `tavily` 都按 Tavily -> Bing -> Baidu -> DuckDuckGo HTML
   继续尝试；普通 Bing HTML 被反爬页替代时，使用 Bing RSS 输出作为同一
   provider 的无脚本后备。
2. 搜索结果必须保留 title、URL、原始 `search_snippet`、实际 provider 与
   `provider_attempts`。某个 provider 的异常不能被吞掉或伪装成成功。
3. Bing、Baidu、DuckDuckGo HTML 的前 N 条结果会抓取网页正文，并由当前
   DataMixer 默认模型（或 WebAgent 显式模型）批量生成 grounded summary、
   relevance score 和 relevant 判定。网页抓取失败时允许仅总结原始摘要；
   LLM 失败时保留原始摘要，不丢弃搜索结果。
4. SearchAgent 的 DuckDuckGo deepsearch 不再依赖 Tavily key，也不再静默
   skip；通用 `WebTools` 返回稳定的 `URL:` 行供后续网页读取。

**2026-07-31 真实诊断与验收**

- 使用调用方提供但未落盘的 Tavily key 请求真实 API，网络、DNS、代理和
  鉴权链路均可到达；服务返回 HTTP 432，错误为当前套餐 usage limit 已耗尽。
  因此本次 Tavily 不可用的直接原因是账户套餐用量上限，不是代码连接故障。
- 当前出口 IP 下，Bing 普通 HTML、Baidu 和 DuckDuckGo HTML 会触发安全验证；
  Bing RSS 可正常返回结构化结果。实现不能把 HTTP 200/202 验证页视为有效
  搜索结果。
- 真实 query `SEC EDGAR search filings` 的完整链路结果：Tavily HTTP 432 ->
  Bing fallback 成功 3 条 -> `deepseek-chat` 摘要 3/3 成功；SEC Search
  Filings、EDGAR Full Text Search、Investor.gov EDGAR 三条相关性得分均为
  1.0。达到“fallback 命中且真实 LLM 摘要成功”的停止条件后，不再继续调用
  外部搜索或 LLM API。
- 定向回归覆盖 Tavily 失败后切 Bing、LLM 摘要成功、LLM 失败保留原摘要、
  通用 `WebTools` 暴露实际 provider，以及 DuckDuckGo deepsearch 不再跳过。

## 4. task3 当前产物的明确处置

- `outputs/5008d610-8b46-4930-89d2-ce4e8b36c20a/sft_dataset/train.jsonl` 仅保留为事故复现和质量测试输入。
- 不将其用于 SFT 训练、模型评测结论或 benchmark 去污通过证明。
- 修复验收使用当前 lake 的隔离 run/namespace，避免覆盖事故证据；验收通过后再创建新的生产 run。

## 5. 完成定义

只有同时满足以下条件，才允许宣称 ObtainerCLI/DataMixer 的金融 SFT 流程可用：

1. 十个真实 query 均真实加载 WebAgent，并保存 run/campaign/L1-L3 证据。
2. 默认模型与 Codex 默认模型一致，显式算子 override 可审计。
3. managed worker 无 direct-download fallback，任意失败可见且阻断后续阶段。
4. 领域质量、去重、来源/许可证、benchmark 去污与处理算子链路都有可查询产物。
5. 最终 export 的比例基于合格出湖记录计算；不可达时明确失败而非静默偏离。
6. 每次 export 都有可复现 manifest、lineage、quality report 和 benchmark decontamination report。

## 6. 2026-08-03 Code 领域真实链路验收

本轮用于验证 Code 领域新契约，不代表 task3 金融流程的十个 query 已全部完成。
复用了持久 campaign `webcampaign-f1da20c1e6e74dbc`，配置保持
`max_depth=4`、`max_pages=100000`，并同时开启逐级持久队列和 L1 -> L2 -> L3
后处理。MinerU-HTML `http://127.0.0.1:7986`、Qwen vLLM
`http://127.0.0.1:8000` 和 LoopAI response proxy
`http://127.0.0.1:8855` 均先通过真实健康检查；WebAgent 规划使用
`deepseek-chat`，DataMixer 分类与 SFT 算子使用 `qwen3-14b-fp8`。

本轮恢复前为 L1/L2/L3 = 344/7/7。恢复并执行真实网页、MinerUHTML、原生
DataFlow 和 Qwen 链路后，在达到合同级验证条件时主动停止，最终物化数量为：

| 数据集 | 数量 |
| --- | ---: |
| `code_web_l1` | 1213 |
| `code_web_l2` | 78 |
| `code_web_l3` | 15 |

15 条 L3 全部满足以下证据：

- `qa_model=qwen3-14b-fp8`，`code_sft_valid=true`；
- 每条均记录六个 `open-dataflow==1.0.10` 原生 Code 过滤算子；
- 5 条的 `code` 为第一标签，10 条为第二标签，证明不再要求第一标签；
- 样本 `smp-91316abe7dcd49af` 的标签为
  `["Python programming", "code"]`，最终 810 字符 Python 代码由 Qwen
  组合四个来源代码块生成，与任一来源块均不完全相同，但通过 Python 语法和
  未定义全局变量检查，证明不再要求生成代码与原始代码逐字一致。

停止时没有继续消耗 100000 页预算。进程退出后使用持久队列的恢复接口将中断
批次重置为 `pending`：pipeline 中 0 条 `running`、938 条 `pending`，三个
WebAgent 子任务均为 `pending`。campaign 状态保留
`completed_with_errors` 及“executor exited”原因，以准确表示本次人工停止；
后续可用同一 run id 执行 `webagent campaign resume`，不会丢失已成功或已拒绝
的记录。

定向回归结果为 `tests/test_datamixer_web_pipeline.py` 16/16、
`tests/test_datamixer_webagent_campaign.py` 16/16。真实验证与单测共同覆盖：Code
标签位于前三、生成代码允许改写、原生算子审计、本地 Qwen 路由、同步逐级队列
以及中断恢复。
