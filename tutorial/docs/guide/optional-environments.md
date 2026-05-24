# 可选环境

安装完主环境后，并不代表所有 Agent 都已经具备完整运行条件。对于本地评测和本地训练，通常还需要额外准备独立环境。

## 为什么需要可选环境

LoopAI 的不同阶段依赖并不完全一样：

- Starter 更偏对话编排与状态管理
- Judger 可能需要本地推理服务
- Analyzer 可能依赖外部大模型服务
- Obtainer 和 Constructor 主要使用主环境，但网页/Kaggle 数据流程需要 Playwright 浏览器
- Trainer 会依赖训练框架

这些依赖往往和 CUDA、PyTorch、推理框架或训练框架强相关，因此不建议全部塞进一个环境中。

## 推荐的环境拆分

```bash
# LoopAI 主环境
conda create -n loopai python=3.12

# 本地评测 / 分析时配合 vLLM 的环境
conda create -n loopai-vllm python=3.10

# Llama-Factory 训练环境
conda create -n loopai-llamafactory python=3.10

# verl 训练环境（暂未支持）
conda create -n loopai-verl python=3.10
```

## 这些环境分别做什么

### `loopai`

主环境承载 Starter、WebUI 后端、Obtainer、Constructor 以及常规图执行。

如果要使用 Obtainer 的网页抓取或 Kaggle 下载流程，需要在该环境中额外安装 Playwright 浏览器：

```bash
conda activate loopai
playwright install
```

Constructor 的后处理、清洗和格式映射也运行在主环境中，通常不需要单独的 Conda 环境。

### `loopai-vllm`

主要给 Judger 或部分分析场景使用。

典型用途：

- 本地启动 vLLM
- 承载评测模型推理
- 作为 OpenAI-compatible 服务被 LoopAI 调用

如果 `judger.eval_base_url` 为空，Judger 通常会根据配置尝试拉起本地服务，因此相关环境路径要提前准备好。

### `loopai-llamafactory`

主要给 Trainer 使用。

当前训练侧最重要的实际场景是：

- 基于 Llama-Factory 做 SFT

因此如果你计划在 WebUI 中走到训练阶段，就需要提前准备：

- `trainer.llamafactory_dir`
- `trainer.llamafactory_env_path`

## `loopai-verl`

这个环境可以预留出来，但当前教程中可以明确说明：

- `verl` 暂未支持作为正式可用训练路径

也就是说，文档里可以提到它的规划位置，但第一次上手时不用优先准备它。

## 一个更实用的理解方式

如果只是想先把系统跑起来：

- 只需要主环境 `loopai`

如果要做网页抓取或 Kaggle 数据获取：

- 在主环境里额外执行 `playwright install`

如果要做本地评测：

- 再准备 `loopai-vllm`

如果要做训练：

- 再准备 `loopai-llamafactory`

这样更符合大多数用户第一次上手的成本控制。
