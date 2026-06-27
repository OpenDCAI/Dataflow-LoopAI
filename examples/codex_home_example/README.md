# `examples/codex_home_example`

这是一个给 LoopAI + Codex Starter 使用的最小示例目录。

## 目录说明

- [AGENTS.md](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home_example/AGENTS.md)
  Codex 在 LoopAI 中扮演 `starter` 时的角色说明
- [config.toml](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home_example/config.toml)
  一个可参考的 `CODEX_HOME` 配置

## 用途

这份示例主要用于说明：

- Codex 如何作为 `starter` 调度 sub-agent
- 如何理解 `task_id` 上下文
- 如何在配置类请求中调用工作区里的 `skills/configer/SKILL.md`
- 如何把 Configer 暴露成可被 Codex hook 拦截的 MCP tools
- 如何通过 MCP tools 调用 Judger / Trainer
- 如何统一解析 LoopAI 的 success / error 返回

## 推荐用法

运行 Codex Runner 时，将 `CODEX_HOME` 指向这个目录，或将其中内容复制到你的实际 `CODEX_HOME`。

项目专用 skill 建议直接维护在工作区：

- [skills/configer/SKILL.md](/home/lpc/repos/Dataflow-LoopAI/skills/configer/SKILL.md)
- [loopai/mcp/server.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/server.py)
  LoopAI Configer 的 MCP server 入口
- [loopai/mcp/tools/configer.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/tools/configer.py)
  将 `loopai.skills.Configer` 包装成 MCP tools
- [loopai/mcp/tools/judger.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/tools/judger.py)
  将 `loopai.skills.Judger` 包装成 MCP tools
- [loopai/mcp/tools/trainer.py](/home/lpc/repos/Dataflow-LoopAI/loopai/mcp/tools/trainer.py)
  将 Trainer 执行与事件读取包装成 MCP tools
- [codex_home/hooks/check_config_write.py](/home/lpc/repos/Dataflow-LoopAI/codex_home/hooks/check_config_write.py)
  一个 `PreToolUse` 示例，在 `configer_update*` 前做写入拦截

如果你直接在当前仓库里试用，建议同时确保：

- `DB_PATH` 指向实际 SQLite 数据库
- 任务级配置时设置 `task_id` 或 `TASK_ID`
- `config.toml` 中的 project trust 路径与你的本地仓库路径一致
- `config.toml` 中的 `mcp_servers.loopai_mcp` 路径与你的本地仓库路径一致

当前示例已经在 [config.toml](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home_example/config.toml) 里添加：

- `loopai_mcp` stdio MCP server
- `configer_*` 配置读写 tools
- `judger_run` / `judger_load_events`
- `trainer_run` / `trainer_load_events`
- `mcp__loopai_mcp__configer_update(_task)` 的 `PreToolUse` hook 示例
