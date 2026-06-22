# `examples/codex_home`

这是一个给 LoopAI + Codex Starter 使用的最小示例目录。

## 目录说明

- [AGENTS.md](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home/AGENTS.md)
  Codex 在 LoopAI 中扮演 `starter` 时的角色说明
- [config.toml](/home/lpc/repos/Dataflow-LoopAI/examples/codex_home/config.toml)
  一个可参考的 `CODEX_HOME` 配置

## 用途

这份示例主要用于说明：

- Codex 如何作为 `starter` 调度 sub-agent
- 如何理解 `task_id` 上下文
- 如何在配置类请求中调用工作区里的 `skills/configer/SKILL.md`
- 如何统一解析 LoopAI 的 success / error 返回

## 推荐用法

运行 Codex Runner 时，将 `CODEX_HOME` 指向这个目录，或将其中内容复制到你的实际 `CODEX_HOME`。

项目专用 skill 建议直接维护在工作区：

- [skills/configer/SKILL.md](/home/lpc/repos/Dataflow-LoopAI/skills/configer/SKILL.md)

如果你直接在当前仓库里试用，建议同时确保：

- `DB_PATH` 指向实际 SQLite 数据库
- 任务级配置时设置 `task_id` 或 `TASK_ID`
- `config.toml` 中的 project trust 路径与你的本地仓库路径一致
