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

优先推荐使用 `CODEX_API_KEY` 和 `CODEX_UPSTREAM_BASE_URL`。这些变量只给 Rust 代理使用；Codex 侧只需要配置代理的本地 URL。

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

启动后得到的新 base URL 是：

```text
http://127.0.0.1:15721/v1
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
export CODEX_BASE_URL="http://127.0.0.1:15721/v1"
export CODEX_MODEL="deepseek-chat"
export CODEX_WORKSPACE="/home/xuebinrui/code/loopai/Dataflow-LoopAI"
export CODEX_HOME="/home/xuebinrui/code/loopai/Dataflow-LoopAI/codex_home"

corepack yarn dev "你好，简单介绍一下当前项目"
```

这里 `codex-runner` 不做 DeepSeek 特判，也不自动启动或探测代理。它只把 `CODEX_BASE_URL` 透传给 Codex SDK，所以 DeepSeek 场景必须把 `CODEX_BASE_URL` 配成本地代理 URL：

```text
http://127.0.0.1:15721/v1
```

如果你使用的是原生 Responses API 服务，不需要启动 Rust router，直接把 `CODEX_BASE_URL` 配成该服务自己的 URL 即可。

## 5. 在 LoopAI 后端中使用

如果通过 LoopAI Web 后端调用 Codex，需要确保 `starter.yaml` 里有：

```yaml
system:
  codex_api_key: "你的 DeepSeek API Key"
  codex_base_url: "http://127.0.0.1:15721/v1"
  codex_model: "deepseek-chat"
```

然后启动本地代理：

```bash
./scripts/start_codex_deepseek_proxy.sh
```

再启动 LoopAI 后端。后端通过 `codex-runner` 调用 Codex SDK 时，会直接访问 `codex_base_url`，也就是本地 Rust 代理。

## 6. 常见问题

### Codex SDK 没有走 Rust router

检查 health：

```bash
curl -sS http://127.0.0.1:15721/health
```

如果端口改过，需要同步设置：

```bash
export CODEX_LOCAL_PROXY_PORT=你的端口
```

然后确认 Codex 或 `codex-runner` 的 base URL 已经配置成本地代理 URL：

```text
http://127.0.0.1:15721/v1
```

如果仍然配置成 `https://api.deepseek.com`，Codex SDK 会直接请求 DeepSeek 的 Chat Completions 服务，通常不能按 Responses API 正常工作。

