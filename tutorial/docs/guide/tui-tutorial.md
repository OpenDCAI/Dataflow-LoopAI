# TUI 教程

LoopAI TUI 是面向终端的交互界面：它连接正在运行的 LoopAI 后端，提供任务管理、对话输入、Looper 控制和 node 运行态查看。它适合没有浏览器的服务器、SSH 会话或希望用键盘完成操作的场景；它不替代 WebUI 中的数据湖、复杂配置编辑和状态检查能力。

<video controls preload="metadata" style="width: 100%; max-width: 960px">
  <source src="https://github.com/OpenDCAI/Dataflow-LoopAI/releases/download/res-v1.0.0/Looper-Tui.mp4" type="video/mp4" />
  你的浏览器不支持视频播放；请[下载 Looper TUI 演示视频](https://github.com/OpenDCAI/Dataflow-LoopAI/releases/download/res-v1.0.0/Looper-Tui.mp4)。
</video>

## 启动前准备

先完成根目录的[快速开始](/guide/quick-start)：后端必须已启动，且 `starter.yaml` 中的模型配置可用。

```bash
# 仓库根目录：启动后端
python api/start.py

# 新开一个终端：构建并启动 TUI
cd tui
yarn
yarn build
yarn start
```

默认连接地址为 `http://127.0.0.1:8855`。如后端在其他机器或端口，启动前覆盖地址：

```bash
VITE_LOOPAI_API_BASE_URL=http://your-host:8855 yarn dev
```

开发时可直接运行 `yarn dev`。若后端 OpenAPI 有变更，则在后端可访问时执行 `yarn api` 重新生成 API 客户端。

## 界面与基本流程

启动后先显示首页。输入 `/tasks` 进入任务列表；没有任务时可用 `/new <名称>` 创建。选择任务后按 `Enter`，或输入 `/now`，即可进入当前任务视图。

在当前任务视图，直接输入不以 `/` 开头的文本并按 `Enter`，文本会作为该任务的对话请求提交给 Starter。TUI 会刷新会话、任务状态与 node 信息；开启 Looper 的任务可继续由 Looper 推进后续对话。

推荐的使用节奏是：

1. 启动后端，再启动 TUI。
2. 输入 `/tasks`，用 `n` 或 `/new <名称>` 创建任务。
3. 用 `j` / `k` 或方向键选择任务，按 `Enter` 打开。
4. 在 `/now` 视图输入目标，例如“评测当前代码模型”。
5. 查看 node 卡片、State、Custom Info、Runtime 和 Assistant 面板；必要时输入 `/refresh`。
6. 使用 `/stop` 停止当前会话，或 `/stop_looper` 停止 Looper 自动接管。

## 任务管理

| 操作 | 命令 | 快捷键 |
| --- | --- | --- |
| 打开任务列表 | `/tasks` | — |
| 创建任务 | `/new <名称>` | `n` |
| 重命名当前任务 | `/rename <名称>` | `r` |
| 删除当前任务 | `/delete` | `d` |
| 打开当前任务 | `/now` | `Enter`（任务列表中） |
| 刷新任务和状态 | `/refresh` | — |
| 返回首页 | `/home` | — |

`j` / `k` 或 `↑` / `↓` 用于在任务列表中选择任务；`PageUp` / `PageDown` 可快速移动。删除任务会删除服务端的对应任务记录，执行前应确认目标任务。

## 当前任务视图

`/now` 会显示当前任务名、任务 ID、整体状态和可用 node 数量。node 看板会列出 Looper、Trainer、Judger、Analyzer 和 Obtainer 的运行状态及关键字段；用 `←` / `→` 或 `h` / `l` 切换当前查看的 node。

下方会话区域分为四类信息：

- **State**：当前 node 的状态字段。
- **Custom Info**：node 产生的进度和自定义运行信息。
- **Runtime**：工具调用和运行时事件。
- **Assistant**：助手的对话输出。

按 `Tab` 在这四个面板之间切换焦点。`↑` / `↓` 或 `j` / `k` 滚动当前面板；`PageUp` / `PageDown` 快速滚动。Runtime 与 Assistant 默认跟随最新内容，手动滚动后可用 `End` 回到末尾。

## 对话与控制命令

| 命令 | 作用 |
| --- | --- |
| `/h` 或 `/help` | 显示命令帮助。 |
| `/clear` | 清空当前任务的会话。 |
| `/stop` | 终止当前会话。 |
| `/stop_looper` | 终止或抑制当前任务的 Looper 自动接管。 |
| `/quit` | 退出 TUI。 |

`Esc` 会清空当前输入并关闭提示/详情面板；`Ctrl+C` 或 `Ctrl+Q` 也可退出。普通对话只能在 `/now` 任务视图中提交；若尚未创建任务，先使用 `/new <名称>`。

## 常见问题

- **无法加载任务或状态**：确认 `python api/start.py` 正在运行，并检查 TUI 连接的地址是否与后端端口一致。
- **远端后端无法连接**：通过 `VITE_LOOPAI_API_BASE_URL` 指向可访问的后端地址，并确认该端口的网络策略允许访问。
- **没有 node 数据**：任务刚创建或尚未提交对话时是正常现象；提交请求后使用 `/refresh` 查看状态。
- **Looper 未继续推进**：确认任务配置已启用 Looper；可用 `/stop_looper` 主动停止已启动或等待接管的 Looper。
- **需要编辑全局配置、资源池或数据湖**：请使用 WebUI；TUI 目前专注任务和对话运行态。
