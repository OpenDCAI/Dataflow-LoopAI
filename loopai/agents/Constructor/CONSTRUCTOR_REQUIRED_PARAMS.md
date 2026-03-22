# Constructor 必填参数说明

本文档说明 `Constructor` 在独立状态命名空间 `state["constructor"]` 下的入参与默认行为。

## 一、参数来源

- **直接传入 constructor**：上游在进入 `ConstructorAgent` 前显式写入 `state["constructor"]`。
- **从 obtainer 兼容继承**：`ConstructorAgent.start_node` 会在 `constructor` 缺失时从 `state["obtainer"]` 同名字段补齐。
- **代码默认值**：若仍缺失，部分字段使用 `ConstructorAgent` 或节点内默认值。

## 二、必须输入参数（强约束）

以下参数在执行后处理/映射时必须可用，否则流程会失败或提前结束：

- `constructor.model_path`
- `constructor.base_url`
- `constructor.api_key`

说明：
- `postprocess_node` 会校验以上三项。缺失任意一项时会写入 `state["exception"]` 并中断后处理。
- 如果你不在 `constructor` 里传，必须保证这些值可从 `obtainer` 兼容同步得到。

## 三、流程必需但可由系统推导/兜底的参数

- `constructor.intermediate_data_path`
  - 后处理成功后自动写入；清洗和映射阶段依赖此路径存在。
- `constructor.category`（推荐显式传 `PT` 或 `SFT`）
  - 缺失时多处逻辑会回退默认 `PT`，但不建议依赖默认值。
- `output_dir`（全局字段）
  - 缺失时默认 `./output`。

## 四、Constructor 专有配置（非必填，均有默认）

- `constructor.max_samples_before_cleaning`（默认 `20000`）
- `constructor.llm_timeout`（默认 `120.0`）
- `constructor.max_retries`（默认 `3`）
- `constructor.max_concurrent_mapping`（默认 `10`）
- `constructor.default_mapping_format`（默认 `alpaca`）
- `constructor.debug`（默认 `false`）

## 五、Obtainer -> Constructor 传参注意事项

- 当前主流程保持 `obtain_node -> constructor_node` 不变。
- 为兼容旧流程，`ConstructorAgent.start_node` 会将以下关键字段从 `obtainer` 同步到 `constructor`（仅在 `constructor` 对应字段缺失时）：
  - 模型配置：`model_path/base_url/api_key/temperature`
  - 任务上下文：`user_query/datasets_background/category/subtasks`
  - 构造配置：`llm_timeout/max_retries/max_concurrent_mapping/max_samples_before_cleaning/default_mapping_format/debug`
  - 映射运行态：`intermediate_data_path/confirmed_format/pending_format/mapping_auto_mode/confirmation_result/mapping_user_intent/mapping_selected_format_id/mapping_custom_description`

建议：新接入方优先直接写 `state["constructor"]`，逐步去掉对 `state["obtainer"]` 的依赖。
