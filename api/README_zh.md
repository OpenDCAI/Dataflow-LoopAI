# LoopAI WebUI 后端

[English](./README.md) | 简体中文

`api/` 是 LoopAI WebUI 的 FastAPI 后端。它负责管理 WebUI 配置、任务记录、数据资源、硬件与运行状态，并为指定任务启动集成在 `loopai` 中的 Starter 工作流。

Trainer 现在已经集成到 LoopAI 的 Agent 图中，常规情况下由 `StarterAgent` 调度启动，不再作为独立 API 服务使用。当前保留的 `/train/*` 路由主要用于兼容已有流程，以及支持训练日志、指标和直接 LLaMA Factory 任务工具。

## 总体 API 框架

```text
Browser / WebUI
      |
      | 同源 HTTP / SSE
      v
FastAPI app (api/app/main.py)
      |
      +-- /config    全局 Starter 配置与 State schema
      +-- /task      WebUI 任务 CRUD 与任务状态快照
      +-- /resource  数据资源登记与文件预览
      +-- /starter   启动、停止、恢复、流式输出 LoopAI StarterAgent
      +-- /train     兼容训练任务、日志和指标接口
      |
      +-- SQLite     api/db/db.sqlite3
      +-- Files      api/dist, api/logs, api/runs, api/configs
      +-- loopai     图式 Agent、memory、checkpointer、store
```

`api/app/main.py` 启动时会：

- 使用 `api/db/db.sqlite3` 注册 Tortoise ORM；
- 挂载 `config`、`task`、`resource`、`starter`、`train` 路由；
- 从 `/` 托管 `api/dist/index.html`；
- 从 `/assets/*` 托管 Vite 构建产物；
- 在 `/docs` 暴露 OpenAPI 文档。

## 目录结构

```text
api/
├── start.py                 # 生产和本地 WebUI 使用的后端启动脚本
├── README.md
├── README_zh.md
├── app/
│   ├── main.py              # FastAPI app、路由注册、静态 dist 托管
│   ├── controllers/         # API 路由模块
│   │   ├── config.py        # Starter 配置与 State schema
│   │   ├── task.py          # WebUI 任务记录与持久化状态
│   │   ├── resource.py      # 数据资源登记与预览
│   │   ├── starter.py       # LoopAI StarterAgent 运行控制与流式输出
│   │   └── train.py         # 兼容训练、日志、指标接口
│   ├── models/              # 请求、响应和数据库模型
│   └── utils/               # starter、config、resource、monitor、train 等工具
├── configs/                 # 后端流程创建的运行配置
├── db/                      # SQLite 数据库目录
├── dist/                    # FastAPI 托管的已发布前端 dist
├── logs/                    # 运行日志
└── runs/                    # 任务运行输出
```

## WebUI Dist 发布与安装

生产环境不需要安装 Node.js、Yarn 或启动 Vite 开发服务器。安装已发布的前端构建产物到 `api/dist` 后，直接启动后端即可。

```bash
python scripts/download_ui_release.py
python api/start.py
```

服务地址：

```text
http://localhost:8855
```

如果 `scripts/download_ui_release.py` 无法获取 release 产物，可以手动从 GitHub Release 页面下载前端 dist 压缩包，并解压到：

```text
api/dist
```

解压后应存在：

```text
api/dist/index.html
```

也可以显式指定 release 来源：

```bash
python scripts/download_ui_release.py \
  --repo OpenDCAI/Dataflow-LoopAI \
  --tag-prefix ui-v \
  --output-dir api/dist
```

前端开发和发布流程见 `docs/Dev_README.md`。前端维护者可运行：

```bash
bash scripts/release_ui.sh [optional-version]
```

该脚本会创建并推送 `ui-v<version>` 标签，触发 GitHub Actions 构建并发布 WebUI dist release asset。

## 启动后端

在仓库根目录以 editable 模式安装 Python 包：

```bash
pip install -e .
```

创建运行配置：

```bash
cp examples/config/starter.yaml ./starter.yaml
```

根据模型服务、输出路径、数据路径和训练环境修改 `starter.yaml`。当前启动脚本仍会校验 `default_states.trainer` 下的 Trainer/LLaMA Factory 字段，因为训练能力仍可在 LoopAI 图中被调度：

```yaml
default_states:
  trainer:
    llamafactory_dir: "/path/to/LLaMA-Factory"
    llamafactory_env_path: "/path/to/env/bin"
```

启动后端：

```bash
python api/start.py
```

常用地址：

```text
WebUI:       http://localhost:8855
API docs:    http://localhost:8855/docs
Health:      http://localhost:8855/health
Service info http://localhost:8855/info
```

如果需要开发前端源码，请在 `ui/` 下启动 Vite。开发服务器会把 `/api/*` 代理到 `http://127.0.0.1:8855/`，生产环境则使用 `api/dist` 中的静态文件。

## API 文档

标准 API 文档由 FastAPI 自动生成：

```text
http://localhost:8855/docs
http://localhost:8855/openapi.json
```

下面是当前项目结构对应的路由概览。

### Config API

挂载在 `/config`。

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/config/config` | 读取全局 Starter 配置。 |
| `POST` | `/config/config` | 更新全局 Starter 配置。 |
| `GET` | `/config/state_schema?language=zh` | 读取 WebUI 可配置的 LoopAI state schema。 |
| `GET` | `/config/list_dir?path=/some/path` | 为路径选择器列出目录和文件。 |

示例：

```bash
curl http://localhost:8855/config/config
```

### Task API

挂载在 `/task`。

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/task/task` | 创建 WebUI 任务并保存配置快照。 |
| `GET` | `/task/task/{task_id}` | 按 `task_id` 读取任务。 |
| `GET` | `/task/list_tasks` | 通过可选的 `search`、`offset`、`limit` 列出任务。 |
| `PUT` | `/task/task` | 更新任务名称或配置。 |
| `DELETE` | `/task/task/{id}` | 按数据库 id 删除任务。 |
| `GET` | `/task/train_status` | 从任务输出目录读取 Trainer 指标。 |

### Resource API

挂载在 `/resource`。

| Method | Path | 说明 |
| --- | --- | --- |
| `GET` | `/resource/resource` | 列出已登记资源。 |
| `GET` | `/resource/resource/count` | 统计已登记资源数量。 |
| `POST` | `/resource/resource` | 登记数据或资源路径。 |
| `PUT` | `/resource/resource/{resource_id}` | 更新资源元数据。 |
| `DELETE` | `/resource/resource/{resource_id}` | 删除资源记录。 |
| `POST` | `/resource/resource/preview` | 预览 JSON/JSONL、CSV/TSV、文本、Markdown、HTML 或日志文件。 |

资源预览既可以使用数据库资源 id，也可以直接使用文件 URL：

```bash
curl -X POST "http://localhost:8855/resource/resource/preview?resource_id=file:///tmp/data.jsonl&limit=5"
```

### Starter API

挂载在 `/starter`。这是 WebUI 的主要运行时 API。

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/starter/agent/start?task_id=<task_id>` | 为指定任务初始化并启动 `StarterAgent`。 |
| `POST` | `/starter/agent/input?text=...` | 向运行中的 Agent 发送用户输入。 |
| `POST` | `/starter/agent/stop` | 停止 Agent 并持久化任务状态。 |
| `GET` | `/starter/agent/status` | 轮询当前 Agent 状态并持久化。 |
| `GET` | `/starter/agent/messages` | 读取当前 state 中的消息。 |
| `GET` | `/starter/agent/message/stream` | 通过 SSE 流式读取 Agent 消息。 |
| `GET` | `/starter/agent/hardware_usage` | 读取 GPU/NPU、CPU 和内存使用情况。 |

典型流程：

```bash
curl -X POST "http://localhost:8855/starter/agent/start?task_id=<task_id>"
curl -X POST "http://localhost:8855/starter/agent/input?text=Improve%20my%20model"
curl "http://localhost:8855/starter/agent/status"
```

### Train 兼容 API

挂载在 `/train`。

这些接口不是启动集成 LoopAI Trainer 工作流的主要方式。常规 WebUI 操作应使用 Starter API。`train` 路由保留用于直接/历史训练任务，以及 UI 访问日志、SwanLab 文件夹和指标。

| Method | Path | 说明 |
| --- | --- | --- |
| `POST` | `/train/` | 从 JSON 配置启动直接训练任务。 |
| `POST` | `/train/upload` | 从上传的 YAML 启动直接训练任务。 |
| `GET` | `/train/status/{task_id}` | 读取直接训练任务状态。 |
| `GET` | `/train/logs/{task_id}` | 读取直接训练日志。 |
| `GET` | `/train/tasks` | 列出直接训练任务。 |
| `DELETE` | `/train/tasks/{task_id}` | 取消直接训练任务。 |
| `GET` | `/train/swanlab-logs/{task_id}` | 读取单个任务的 SwanLab 日志路径。 |
| `GET` | `/train/swanlab-logs` | 列出 SwanLab 日志目录。 |
| `GET` | `/train/metrics/{task_id}` | 读取最近训练指标。 |
| `GET` | `/train/metrics/{task_id}/file` | 下载或读取指标文件。 |
| `DELETE` | `/train/metrics/{task_id}` | 删除单个任务的指标。 |

## 数据与运行文件

后端会在本地保存轻量级 WebUI 状态：

```text
api/db/db.sqlite3    # Tortoise ORM SQLite 数据库
api/dist/            # 已安装的前端构建产物
api/logs/            # 运行日志
api/runs/            # 运行输出
api/configs/         # 生成或上传的配置
```

LoopAI 任务输出路径也会受到 `starter.yaml` 和 WebUI 创建的任务配置快照控制。

## 常见问题

- `GET /` 返回 `Frontend dist is not installed.`：运行 `python scripts/download_ui_release.py`，或手动将 release dist 解压到 `api/dist`。
- `python api/start.py` 提示 `starter.yaml not found`：将 `examples/config/starter.yaml` 复制到仓库根目录并修改配置。
- 提示 `LLaMA Factory CLI not found`：更新 `starter.yaml` 中的 `default_states.trainer.llamafactory_dir` 和 `default_states.trainer.llamafactory_env_path`。
- WebUI 开发服务器无法连接后端：检查 `ui/vite.config.js`，确认 proxy target 指向 `http://127.0.0.1:8855/` 或实际后端地址。
