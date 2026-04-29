# 快速开始

这一页只解决第一件事：把 LoopAI 安装好，并确认你已经具备继续阅读后续教程的基础环境。

## 基础要求

- Python `3.12`
- 建议使用 Conda 管理环境
- Node.js `20+`
  说明：Node 主要用于本教程站和前端相关工作，不是运行 LoopAI 后端的核心前提

## 安装核心环境

推荐先准备一个最小可运行的主环境：

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

这一步安装的是 LoopAI 的核心运行时，足够支持：

- Starter 主流程
- WebUI 后端
- 常规图执行与状态管理
- 一部分基础数据处理能力

## 准备基础配置

LoopAI 需要仓库根目录存在 `starter.yaml`：

```bash
cp examples/config/starter.yaml ./starter.yaml
```

至少先补齐 `system` 中最关键的字段：

```yaml
system:
  starter_api_key: ""
  starter_model_path: ""
  starter_model_name: ""
  starter_base_url: ""
  tavily_api_key: ""
  kaggle_username: ""
  kaggle_key: ""
```

这些字段主要用于：

- 配置 Starter 使用的模型服务
- 配置外部数据检索凭据
- 让后续评测、分析、数据获取等流程能顺利接上

## 到这里你已经完成了什么

完成本页后，你已经具备了继续进行 WebUI 教程或终端教程的基础前提。但如果你后续还要做本地评测、本地训练，通常还需要额外环境。

下一步建议先阅读 [可选环境](/guide/optional-environments)。
