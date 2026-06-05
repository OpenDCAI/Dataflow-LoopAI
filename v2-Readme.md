# Dataflow-LoopAI V2 使用说明

本文档面向当前 `v2` 分支，重点说明从零开始安装、前端构建迁移、`codex-runner` 安装以及启动方式。

和主 README 相比，当前分支有两个关键区别：

1. 不能直接使用主 README 里的 `download_ui_release.py` 下载主线 UI 发布包。
2. 需要额外安装 `codex-cli` 和 `codex-runner`，否则 V2 的 Codex 能力无法正常工作。

## 1. 环境准备

建议先准备以下基础环境：

- Python 3.12
- Node.js 20
- Yarn 4
- Conda 或 Miniconda

如果你计划使用训练能力，还需要本地准备好 LLaMA-Factory 环境。当前仓库里的 [api/start.py](/home/lpc/repos/Dataflow-LoopAI/api/start.py) 启动时会检查 `llamafactory_dir` 和 `llamafactory_env_path`，因此这两个路径也要提前配置正确。

## 2. 安装 LoopAI Python 依赖

这部分和主 README 基本一致。

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

如果后续要使用网页采集等能力(WebCrawler)，建议再补一次：

```bash
playwright install
```

## 3. 安装并构建前端

当前 `v2` 分支不能直接复用主 README 中的直接下载 UI 发布包。

原因是主 README 对应的是 `main` 分支的 UI，而当前 `v2` 分支对前端做了改动，所以需要参考 [ui/README.md](/home/lpc/repos/Dataflow-LoopAI/ui/README.md) 本地安装 Node 环境并手动构建。

### 3.1 安装 Node.js 20

推荐使用 `nvm`：

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
```

安装完成后重新加载 shell：

```bash
source ~/.bashrc
```

如果你使用的是 `zsh`，则执行：

```bash
source ~/.zshrc
```

安装并切换到 Node 20：

```bash
nvm install 20
nvm use 20
nvm alias default 20
```

检查版本：

```bash
node -v
npm -v
```

其中 `node` 建议为 `v20.x.x`。

### 3.2 安装 Yarn

```bash
corepack enable
corepack prepare yarn@stable --activate
yarn -v
```

### 3.3 安装前端依赖并构建

进入前端目录：

```bash
cd ui
yarn
yarn build
```

构建完成后，会生成前端产物目录 `ui/dist`。

### 3.4 将前端 dist 复制到后端

当前后端会直接服务 `api/dist`，因此需要手动把构建结果拷贝过去：

```bash
cd /home/lpc/repos/Dataflow-LoopAI
rm -rf api/dist
cp -r ui/dist api/dist
```

如果你不想删除原目录，也可以先备份再覆盖。总之最终要保证后端启动时能读到 `api/dist`。

## 4. 安装 Codex 相关依赖

这一部分是当前 V2 使用流程里的重点。

### 4.1 先安装 codex-cli

`codex-runner` 依赖 `@openai/codex-sdk`，而实际运行前通常要先把本机的 `codex-cli` 环境准备好。

目前建议按 OpenAI 官方当前方式直接全局安装：

```bash
npm install -g @openai/codex
```

安装完成后先验证命令可用：

```bash
which codex
codex --version
```

能正确输出版本号，说明 `codex-cli` 已安装成功。

如果你希望先在本机完成 Codex 登录，也可以执行：

```bash
codex login
```

不过对于本项目来说，运行时主要还是依赖 `starter.yaml` 中的 `codex_api_key`、`codex_model` 和 `codex_base_url`。

### 4.2 安装 codex-runner 依赖

进入 `codex-runner` 目录安装依赖：

```bash
cd /home/lpc/repos/Dataflow-LoopAI/codex-runner
yarn
```

建议再执行一次构建检查：

```bash
yarn build
```

### 4.3 测试 codex-runner 是否正常

至少做下面几项检查：

1. `codex` 命令可用：

```bash
codex --version
```

2. `codex-runner` 依赖安装正常：

```bash
cd /home/lpc/repos/Dataflow-LoopAI/codex-runner
yarn build
```

3. 如需进一步验证，可做一次最小运行测试：

```bash
cd /home/lpc/repos/Dataflow-LoopAI/codex-runner
CODEX_API_KEY=your_key \
CODEX_MODEL=your_model \
CODEX_BASE_URL=your_base_url \
CODEX_WORKSPACE=/home/lpc/repos/Dataflow-LoopAI \
yarn dev "hello"
```

如果能正常返回事件流或最终响应，说明 `codex-runner` 已基本可用。

## 5. 配置 starter.yaml

所有运行模式都需要仓库根目录下存在 `starter.yaml`。

先复制模板：

```bash
cd /home/lpc/repos/Dataflow-LoopAI
cp examples/config/starter.yaml ./starter.yaml
```

当前分支里，下面这些 `system` 字段建议视为必填：

```yaml
system:
  codex_api_key: "sk-..."
  codex_model: "gpt-4o-mini"
  codex_base_url: "http://127.0.0.1:3000/v1"
  codex_workspace: "/home/lpc/repos/Dataflow-LoopAI"
  codex_home: "/home/lpc/repos/Dataflow-LoopAI/codex_home"
```

说明如下：

- `codex_api_key`：Codex 调用使用的 API Key
- `codex_model`：Codex 使用的模型名
- `codex_base_url`：模型服务接口地址
- `codex_workspace`：Codex 工作目录，默认就是当前项目根目录
- `codex_home`：Codex 的本地配置目录，默认建议填项目下的 `./codex_home`

如果你暂时还没有最终的 Codex 参数，也可以先随便填占位值，启动 UI 之后再到系统配置页面里补齐或修改。

除了上面的 Codex 配置，下面这些原有配置也仍然需要按你的实际环境填写：

- `tavily_api_key`
- `kaggle_username`
- `kaggle_key`
- `llamafactory_dir`
- `llamafactory_env_path`

特别注意：

1. `codex_workspace` 默认就是项目根目录 `/home/lpc/repos/Dataflow-LoopAI`。
2. `codex_home` 默认建议使用项目根目录下的 `/home/lpc/repos/Dataflow-LoopAI/codex_home`。
3. `api/start.py` 启动时会检查 LLaMA-Factory 路径，如果 `llamafactory_dir` 或 `llamafactory_env_path` 不正确，后端可能无法启动。

## 6. 启动服务

当前推荐流程就是先完成以下三步：

1. 安装 LoopAI 的 Python 依赖
2. 完成前端依赖安装、构建，并把 `ui/dist` 复制到 `api/dist`
3. 安装 `codex-cli`，并在 `codex-runner` 下执行 `yarn`

全部就绪后，在项目根目录启动后端：

```bash
cd /home/lpc/repos/Dataflow-LoopAI
python api/start.py
```

默认访问地址：

```text
http://localhost:8855
```

API 文档地址：

```text
http://localhost:8855/docs
```

## 7. 推荐的最小安装顺序

如果你只想快速跑起来，按下面顺序做即可：

1. 安装 Python 依赖

```bash
conda create -n loopai python=3.12
conda activate loopai
pip install uv
uv pip install -e .
```

2. 构建前端并复制产物

```bash
cd ui
yarn
yarn build
cd ..
rm -rf api/dist
cp -r ui/dist api/dist
```

3. 安装并验证 Codex

```bash
codex --version
cd codex-runner
yarn
yarn build
```

4. 配置 `starter.yaml`

```bash
cp examples/config/starter.yaml ./starter.yaml
```

填好至少 `codex_api_key`、`codex_model`、`codex_base_url`、`codex_workspace`、`codex_home`，以及你本地可用的 `llamafactory_dir`、`llamafactory_env_path`。

5. 启动

```bash
python api/start.py
```

## 8. 常见问题

### 8.1 为什么不能直接执行 `python scripts/download_ui_release.py`？

因为那是主线 `main` 分支 UI 的发布包流程，当前 `v2` 分支前端已经改动，必须本地构建 `ui/dist` 后再手动复制到 `api/dist`。

### 8.2 `codex-runner` 已经 `yarn` 了，为什么还不能用？

因为当前链路除了 Node 依赖，还依赖本机可用的 `codex-cli` 和正确的 `codex_*` 配置。至少要确认：

- `codex --version` 正常
- `starter.yaml` 里的 `codex_api_key`、`codex_model`、`codex_base_url` 已配置
- `codex_workspace`、`codex_home` 路径有效

### 8.3 Codex 参数能不能先不填？

可以先填占位值，把 UI 启起来后再到系统配置里修改；但如果你马上要在 V2 里调用 Codex 能力，就必须先配置成真实可用的值。
