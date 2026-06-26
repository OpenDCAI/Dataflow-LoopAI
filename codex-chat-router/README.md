# codex-chat-router

Small standalone Rust proxy for Codex Responses API to OpenAI-compatible Chat Completions providers.

The conversion, streaming, and history modules under `src/ccswitch/` are vendored from `cc-switch` and kept with its MIT license in `CCSWITCH_LICENSE`. Local files outside that tree provide only the standalone HTTP server and small compatibility shims.

## Run

```bash
LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY=... ./scripts/start_codex_deepseek_proxy.sh
```

The local Responses base URL is `http://127.0.0.1:15721/v1` by default. Build artifacts and temporary files are directed to the project-local `.cache_codex/` directory by the startup script.
