# 快速开始

本页按仓库根目录的最新 README 安装并启动 LoopAI。完成后可使用 WebUI；本地评测、训练和网页采集属于按需启用的能力，见[可选环境](/guide/optional-environments)。

## 1. 安装核心环境

LoopAI 需要 Python `3.11+`；README 推荐 Python `3.12`。以下命令在仓库根目录执行：

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

这会安装核心运行时、API 服务、编排运行时和通用数据处理依赖。

## 2. 安装并登录 Codex

Starter 通过 `codex-sdk` 工作，因此需要可用的 `codex` 命令。选择一种官方安装方式：

```bash
# macOS / Linux 官方安装脚本
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# 或使用 npm
npm install -g @openai/codex
```

macOS 也可以使用 `brew install --cask codex`；Windows 请使用 README 中的 PowerShell 安装命令。安装后检查并首次登录：

```bash
which codex
codex --version
codex
```

首次启动时按提示使用 ChatGPT 账户或 OpenAI API key 登录。接着安装 `codex-runner` 依赖并做构建检查：

```bash
cd codex-runner
yarn
yarn build
cd ..
```

## 3. 配置 `starter.yaml`

所有运行方式都需要仓库根目录的 `starter.yaml`：

```bash
cp examples/config/starter.yaml ./starter.yaml
```

编辑该文件，在模型配置区的 `pool` 中配置至少一个可用模型，并让 `default_model`、`codex_model` 和 `looper_model` 引用相应的 pool 名称。同时确认：

```yaml
system:
  api_port: 8855
  codex_workspace: "<项目根目录>"
  codex_home: "<项目根目录>/codex_home"
```

可选的 Tavily、Kaggle 等凭据保留在 `system.integrations` 中，并优先通过模板中的 `env:变量名` 引用环境变量。不要将真实密钥提交到仓库。配置字段以仓库根目录的 `README.md` 和 `docs/API_KEYS.md` 为准。

## 4. 启动 WebUI

先下载已发布的前端产物，随后启动后端：

```bash
python scripts/download_ui_release.py
python api/start.py
```

在浏览器打开 `http://localhost:8855` 使用 WebUI；API 文档位于 `http://localhost:8855/docs`。如无法自动下载 release 产物，请从 GitHub Release 下载前端 dist 压缩包并解压到 `api/dist`。

## 接下来

- 第一次使用界面：阅读 [WebUI 教程](/guide/webui-tutorial)。
- 无浏览器环境：构建并启动 [TUI](/guide/tui-tutorial)。
- 需要本地推理、网页采集或训练：阅读 [可选环境](/guide/optional-environments)。
