# Codex DeepSeek 本地代理使用教程

这个项目里新增了一个本地 Rust 代理 `codex-chat-router`，用于让 Codex SDK 使用 DeepSeek 这类 OpenAI Chat Completions 兼容接口。

它对 Codex SDK 暴露 OpenAI Responses API：

```text
http://127.0.0.1:15721/v1
```

内部会把请求转换成 DeepSeek Chat Completions API，再把响应转换回 Codex SDK 需要的 Responses API/SSE 格式。

## 1. 配置 DeepSeek Key

在项目根目录执行：

```bash
cd /home/xuebinrui/code/loopai/Dataflow-LoopAI

export CODEX_API_KEY="你的 DeepSeek API Key"
export CODEX_MODEL="deepseek-chat"
export CODEX_UPSTREAM_BASE_URL="https://api.deepseek.com"
```

也可以使用 DeepSeek 命名的环境变量：

```bash
export DEEPSEEK_API_KEY="你的 DeepSeek API Key"
export DEEPSEEK_MODEL="deepseek-chat"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
```

优先推荐使用 `CODEX_API_KEY`。

## 2. 启动本地代理

在项目根目录执行：

```bash
./scripts/start_codex_deepseek_proxy.sh
```

默认监听：

```text
http://127.0.0.1:15721/v1
```

如果想换端口：

```bash
export CODEX_LOCAL_PROXY_PORT=15722
./scripts/start_codex_deepseek_proxy.sh
```

## 3. 测试代理是否正常

另开一个终端执行：

```bash
curl -sS http://127.0.0.1:15721/health
```

看到下面结果说明代理已启动：

```json
{"ok":true}
```

再测试一次 Responses API：

```bash
curl -sS -N http://127.0.0.1:15721/v1/responses \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","stream":true,"input":"只回复 pong"}'
```

正常情况下会看到 `response.output_text.delta` 等 SSE 事件。

## 4. 使用 Codex SDK

进入 `codex-runner`：

```bash
cd /home/xuebinrui/code/loopai/Dataflow-LoopAI/codex-runner

export CODEX_API_KEY="你的 DeepSeek API Key"
export CODEX_BASE_URL="https://api.deepseek.com"
export CODEX_MODEL="deepseek-chat"
export CODEX_WORKSPACE="/home/xuebinrui/code/loopai/Dataflow-LoopAI"
export CODEX_HOME="/home/xuebinrui/code/loopai/Dataflow-LoopAI/codex_home"

corepack yarn dev "你好，简单介绍一下当前项目"
```

`codex-runner` 会自动检测 Rust 本地代理：

- 如果 `http://127.0.0.1:15721/health` 可用，就使用 Rust router。
- 如果 Rust router 没启动，就回退到 TypeScript fallback 代理。
- 本地路由启用时会禁用 WebSocket，使用 HTTP/SSE，避免 DeepSeek 不支持 WebSocket 导致重连错误。

如果你明确不想使用 Rust router，可以设置：

```bash
export CODEX_USE_RUST_CHAT_ROUTER=0
```

## 5. 在 LoopAI 后端中使用

如果通过 LoopAI Web 后端调用 Codex，需要确保 `starter.yaml` 里有：

```yaml
system:
  codex_api_key: "你的 DeepSeek API Key"
```

然后启动本地代理：

```bash
./scripts/start_codex_deepseek_proxy.sh
```

再启动 LoopAI 后端。后端通过 `codex-runner` 调用 Codex SDK 时，会自动走本地路由。

## 6. 常见问题

### 代理启动后 Codex SDK 还是没有走 Rust router

检查 health：

```bash
curl -sS http://127.0.0.1:15721/health
```

如果端口改过，需要同步设置：

```bash
export CODEX_LOCAL_PROXY_PORT=你的端口
```

或直接指定：

```bash
export CODEX_CHAT_ROUTER_BASE_URL="http://127.0.0.1:15721/v1"
```

### GitHub/系统盘空间不足

启动脚本默认把 Rust 构建目录放到：

```text
/data/xuebinrui/cargo-target/loopai-codex-chat-router
```

不会把大量构建产物写到系统盘。

### DeepSeek 报 tool_calls 相关错误

Rust router 已包含 `cc-switch` 的 history 恢复逻辑，会在 Codex 后续轮次只发送 `function_call_output` 时，自动补回上一轮 assistant tool call，避免 DeepSeek 报：

```text
An assistant message with 'tool_calls' must be followed by tool messages
```
