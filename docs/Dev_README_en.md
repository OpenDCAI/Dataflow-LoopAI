# Dataflow-LoopAI Development Guide

[简体中文](./Dev_README.md) | English

Dataflow-LoopAI is a **self-evolving** system built around the Starter, Looper, and a set of nodes / skills for evaluation, analysis, data acquisition, training, and continuous iteration.

```text
User  <->  Starter (Codex SDK)  <->  Node (Skill)
                         |
                         +-- Common question -> Direct response
                         +-- Complex task    -> Closed-loop execution
                                              (Evaluation -> Analysis -> Data Acquisition -> Training)
```

---

## Project Layout

The structure below reflects the **current repository state** and also marks the recommended location for new development:

```text
Dataflow-LoopAI/
├── api/                       # WebUI backend, FastAPI service, task APIs, responseProxy, DB integration
│   ├── app/controllers/       # starter / task / config routes
│   ├── app/services/          # starter sessions, looper, task runtime services
│   ├── app/utils/             # config, monitoring, credential migration helpers
│   ├── db/                    # SQLite database directory
│   └── dist/                  # Published frontend dist served by FastAPI
│
├── codex-runner/              # Codex runner that bridges the local Codex runtime and LoopAI event flow
│
├── docs/                      # Documentation and assets
│   └── assets/                # Images and diagrams
│
├── examples/                  # Example scripts and runnable cases
│   └── scripts/               # Startup, test, and standalone scripts
│
├── loopai/                    # Core Python implementation
│   ├── agents/                # Historical / compatibility directory; still contains BaseAgent and Obtainer
│   │   ├── BaseAgent/         # Base agent / node capability wrappers
│   │   └── Obtainer/          # Legacy data acquisition implementation
│   │
│   ├── common/                # Shared tools, event flow, exceptions, prompts, etc.
│   ├── mcp/                   # MCP server and tools
│   ├── schema/                # State, model pool, event, and system config schema
│   ├── skills/                # Current recommended directory for new capability implementations
│   │   ├── Analyzer/
│   │   ├── Configer/
│   │   ├── Judger/
│   │   ├── Looper/
│   │   ├── ObtainerCLI/
│   │   └── Trainer/
│   └── utils/                 # Common helpers
│
├── skills/                    # Runtime skill definitions consumed by the system (SKILL.md)
│   ├── Analyzer/
│   ├── Configer/
│   ├── Judger/
│   ├── Trainer/
│   └── obtainer/
│
├── scripts/                   # Project scripts such as UI release and proxy startup helpers
│
├── tui/                       # Terminal UI for task management, main chat, and node status views
│
└── ui/                        # Vue 3 + Vite WebUI frontend source
```

### Recommended Development Location

Compared with earlier versions, the development structure has changed:

1. New sub-agents / skills should no longer be added to `loopai/agents` by default.
2. New capability implementations should primarily live under `loopai/skills`.
3. The root-level `skills` directory is used for the `SKILL.md` files actually consumed by the system.
4. `loopai/agents` still contains historical and compatibility code, so documentation should follow the repository as it exists rather than assuming that directory is empty.

A practical way to think about it is:

- `loopai/skills`: Python implementations, tools, runners, and execution logic
- `skills`: runtime skill definitions, mainly `SKILL.md`
- `loopai/agents`: historical implementations, compatibility layers, and not-yet-fully-migrated modules

---

## Core Skills / Nodes

This developer guide now uses **Skill / Node** terminology instead of maintaining a separate Core Agents section.

### Starter

- The system entry point built around `codex-sdk`
- Handles user conversation, intent routing, node scheduling, and overall loop progression

### Looper

- Maintains continuity between the user conversation and the Starter
- Uses conversation history to summarize context, fill follow-up parameters, and push the next step forward automatically
- Helps prevent the loop from being interrupted just because the next user turn is already implied

### Judger

- Runs evaluations and emits metrics, results, and logs

### Analyzer

- Analyzes evaluation results and extracts failure patterns, insights, and structured conclusions

### ObtainerCLI / DataMixer Web Acquisition

- Handles dataset search, download, lake ingest, export, and web collection flows

### Trainer

- Handles training orchestration, config generation, execution, log streaming, and result management

Note: `Configer` still exists for configuration reads/writes and runtime updates, but it is no longer listed here as a core skill.

---

## Installation And Dev Startup

### Python Dependencies

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv pip install -e .
```

### WebUI Frontend Development

For production or normal WebUI usage, prefer downloading the published frontend dist:

```bash
python scripts/download_ui_release.py
```

Only use the steps below when you need to modify or debug `ui/` source code.

#### 1. Install NVM

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc  # or ~/.zshrc
```

#### 2. Install Node.js 20 And Yarn

```bash
nvm install 20
nvm use 20
nvm alias default 20

corepack enable
corepack prepare yarn@stable --activate
```

#### 3. Start Frontend Dev Mode

```bash
cd ui
yarn
yarn dev
```

If the backend is not running on `127.0.0.1:8855`, update the proxy settings in `ui/vite.config.js`.

### TUI Development

`tui/` is the terminal UI for environments where a browser is not convenient.

```bash
cd tui
yarn
```

Dev mode:

```bash
yarn dev
```

Build and start:

```bash
yarn build
yarn start
```

### codex-runner Development

`codex-runner/` bridges the local Codex runtime and LoopAI.

```bash
cd codex-runner
yarn
yarn build
```

---

## New Skill / Node Development Conventions

The old “how to define a new agent” guidance is no longer the preferred model. It is replaced by the node / skill development pattern below. A useful way to read this section is in the same order you would usually build a new node.

### 1. Recommended Directory Layout

For new capabilities, prefer a structure like this:

```text
loopai/skills/<SkillName>/
├── __init__.py
├── runner.py
├── cli.py              # add only when needed
├── utils/
├── nodes/              # if the skill has complex node logic
└── ...

skills/<SkillName>/SKILL.md
```

Conventions:

1. `loopai/skills/<SkillName>/__init__.py` should be the unified import entry.
2. `runner.py` is usually the main execution entry.
3. Add `cli.py` only when an independent CLI is actually needed.
4. If you add a CLI, also update the `entry_points` section in [setup.py](/home/lpc/repos/Dataflow-LoopAI/setup.py).
5. The runtime-facing skill definition should live in `skills/<SkillName>/SKILL.md`.

Current `console_scripts` examples already present in `setup.py`:

```python
entry_points={
    "console_scripts": [
        "loopai-obtainercli=loopai.skills.ObtainerCLI.cli:main",
        "loopai-judger=loopai.skills.Judger.cli:main",
        "loopai-analyzer=loopai.skills.Analyzer.cli:main",
    ],
}
```

### 2. Entry Function And Runtime Parameter Rules

One important rule for node development is that runtime parameters such as `task_id` and `DB_PATH` should be **read by the entry function directly from environment variables**. If a required value is missing, the entry function should **fail fast and exit explicitly** rather than continuing silently.

Recommended pattern:

1. Read environment variables such as `TASK_ID` and `DB_PATH` at the entry point.
2. If a required value is missing, raise an error immediately or return a structured failure.
3. When `task_id` is available, prefer loading runtime config from the database.
4. Keep validation, DB access, and missing-parameter handling behind one shared interface when possible.

### 3. State Inheritance And Runtime Injection

A sub-agent / node usually builds on the matching `LoopAIState` subtype, for example `JudgerState`.

Example runtime injection flow:

```python
import os

from loopai.skills.Configer import (
    get_configer_task_state_config,
    update_configer_task_state_config,
)

# The entry function should read required environment variables explicitly
DB_PATH = os.environ.get("DB_PATH")
TASK_ID = os.environ.get("TASK_ID")

if not DB_PATH:
    raise ValueError("missing required env: DB_PATH")
if not TASK_ID:
    raise ValueError("missing required env: TASK_ID")

# Read the current judger runtime config for the task
judger_cfg = get_configer_task_state_config(
    section_name="judger",
)

if not judger_cfg.get("ok"):
    raise ValueError(judger_cfg.get("message", "failed to load judger config"))

judger_config = judger_cfg["data"]["config"]
print(judger_config)

eval_api_key = judger_config.get("eval_api_key", {}).get("value")
eval_temperature = judger_config.get("eval_temperature", {}).get("value")

if not eval_api_key:
    raise ValueError("missing required config: judger.eval_api_key")
if eval_temperature is None:
    raise ValueError("missing required config: judger.eval_temperature")

# Update the current judger runtime config
update_result = update_configer_task_state_config(
    "judger",
    {
        "eval_api_key": {"value": "xxx", "type": "str"},
        "eval_temperature": 0.2,
    },
)

if not update_result.get("ok"):
    raise ValueError(update_result.get("message", "failed to update judger config"))

print(update_result["data"]["config"])
```

### 4. Success / Failure Return Format

Use a unified return shape whenever possible.

Success:

```json
{
  "ok": true,
  "status": "completed",
  "message": "Sub-agent completed.",
  "data": {},
  "error": null
}
```

Failure:

```json
{
  "ok": false,
  "status": "failed",
  "message": "Sub-agent crashed with an unhandled exception.",
  "data": null,
  "error": {
    "type": "RuntimeError",
    "code": "UNHANDLED_EXCEPTION",
    "detail": "vector index not found",
    "traceback": "...",
    "recoverable": true,
    "time": "2026-06-01T00:00:00Z"
  }
}
```

Prefer emitting them through `emit_error` / `emit_success`:

```python
from loopai.common.exception import emit_error, emit_success, ErrorCode

try:
    raise ValueError("missing codex_api_key")
except Exception as e:
    emit_error(
        e,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        message="Codex runtime config is incomplete.",
    )
```

### 5. Realtime Event Flow And Node Status

Realtime event flow example:

```python
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(name="judger", context_id="task_001")

writer(StreamEvent(
    current="judger",
    progress=0.2,
    message="loading dataset",
    data={"rows": 128},
))

writer(StreamEvent(
    current="judger",
    progress=1.0,
    message="finished",
))
```

It is recommended to trigger `writer` as soon as node execution starts so the runtime state becomes `running`. When the node ends or fails, the runtime status should be updated with `writer.set_failed` / `writer.set_completed`.

These are already wrapped by `emit_error` and `emit_success`, so in practice you only need to pass `stream_writer`:

```python
from loopai.common.exception import emit_error, emit_success, ErrorCode
from loopai.common.event_tool import StreamEvent, get_event_writer

writer = get_event_writer(name="judger", context_id="task_001")

try:
    raise ValueError("missing codex_api_key")
except Exception as e:
    emit_error(
        e,
        code=ErrorCode.CONFIG_ERROR,
        recoverable=True,
        stream_writer=writer,
        message="Codex runtime config is incomplete.",
    )

emit_success(data={...}, stream_writer=writer)
```

### 6. Development Notes

1. The old `python examples/scripts/run_judger.py` guidance has been removed and is no longer the recommended development path.
2. Put new work in `loopai/skills` first unless you are intentionally maintaining legacy modules in `loopai/agents`.
3. If documentation and the repository diverge, always treat the current codebase as the source of truth.

---

## LoopAI Sub-Agent Skill Supplementary Spec

### 1. Prerequisites / Input Contract

Define the minimum required input set for a sub-agent so that tasks remain schedulable and reproducible.

#### 1.1 Required

- `task_id`: unique task identifier for trace / retry / logging
- `input`: core input payload (string / JSON / structured object)
- `context`: context information (optional but recommended, such as history state / embeddings / external memory)
- `config`: runtime config such as model, temperature, top_k, timeout, etc.
- `callback`: callback or result sink, for example stream / webhook / queue

#### 1.2 Optional

- `trace_id`: distributed trace identifier
- `priority`: task priority for the scheduler
- `resource_limit`: resource limits for CPU / GPU / time / tokens

#### 1.3 Pre-check

- required-field validation
- schema validation through JSON schema / pydantic
- dependency availability checks such as index / model / db / cache
- permission checks for external tool usage

---

### 2. Error Model & Recovery Strategy

Use a unified sub-agent error format and define recovery guidance.

#### 2.1 Standard Error Shape

```json
{
  "ok": false,
  "status": "failed | partial_failed | timeout",
  "message": "Human readable error summary",
  "data": null,
  "error": {
    "type": "RuntimeError | ValidationError | ResourceError | ExternalServiceError | TimeoutError",
    "code": "MACHINE_READABLE_CODE",
    "detail": "Specific failure context",
    "traceback": "...",
    "recoverable": true,
    "retry_after": 3,
    "time": "ISO-8601"
  }
}
```

#### 2.2 Main Error Categories And Suggested Handling

##### (1) ValidationError

Typical causes:

- missing parameters
- schema mismatch
- type error

Suggested handling:

- return field-level errors
- let the caller fix the input
- do not auto-retry

##### (2) RuntimeError

Typical causes:

- null pointer / undefined state
- pipeline stage errors

Suggested handling:

- try fallback paths such as downgraded model or simplified flow
- retry no more than two times
- keep the full traceback

##### (3) ResourceError

Typical causes:

- vector index / model not loaded
- insufficient GPU / memory

Suggested handling:

- switch to backup resources
- wait in a queue and retry
- trigger autoscaling if available

##### (4) ExternalServiceError

Typical causes:

- embedding API failure
- DB / vector DB unavailable

Suggested handling:

- retry with exponential backoff
- use fallback cache
- degrade to offline mode

##### (5) TimeoutError

Typical causes:

- long reasoning chains
- stuck IO

Suggested handling:

- checkpoint resume
- reduce max_tokens / batch size
- split the task

---

### 3. Output Contract / Result Spec

Define the structure returned by a successful sub-agent so it can be consumed by trainer / orchestrator / analyzer layers.

#### 3.1 Standard Success Shape

```json
{
  "ok": true,
  "status": "success",
  "result": {},
  "metrics": {},
  "artifacts": [],
  "logs": [],
  "trace_id": "",
  "time_cost_ms": 0
}
```

#### 3.2 `result`

Different sub-agent types should define different result payloads.

**Trainer Agent / Node**

- `model`: model path / checkpoint
- `loss_curve`: training loss array
- `eval_metrics`: validation metrics
- Uses:
  - checkpoint selection
  - early stopping
  - model ranking

**Judger / Evaluator Agent / Node**

- `pass_at_k`
- `accuracy / f1 / reward_score`
- `ranking_scores`
- Uses:
  - model selection
  - RL reward shaping
  - benchmark reports

**Analyzer Agent / Node**

- `insights`: structured analysis output
- `clusters / topics`
- `error_patterns`
- Uses:
  - data cleaning
  - failure diagnosis
  - dataset iteration

**Tool / Executor Agent / Node**

- `execution_result`
- `side_effects`
- `output_files`
- Uses:
  - pipeline chaining
  - artifact storage

#### 3.3 `metrics`

Standard process metrics for monitoring and tuning:

- `latency_ms`
- `token_usage`
- `memory_peak`
- `gpu_utilization`
- `retry_count`

#### 3.4 `artifacts`

- model files
- index / embedding store
- jsonl / dataset dump
- reports / visualizations

---

## Codex Integration Notes

### 1. Model And Proxy Options

#### Qwen3-Plus (1M free quota)

- Alibaba Bailian model market:
  [Qwen3-Plus](https://bailian.console.aliyun.com/cn-beijing?spm=a2c4g.11186623.0.0.4876609d5KE5w1&tab=model#/model-market/detail/qwen3.6-plus?serviceSite=asia-pacific-china)

#### DeepSeek API (recommended)

**Option A: Rust proxy script**

Install Rust first:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

Then start the proxy:

```bash
LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY=<YOUR_API_KEY> ./scripts/start_codex_deepseek_proxy.sh
```

**Option B: model-pool-based native backend proxy**

The project already supports native backend forwarding through the model pool. Before startup, configure the model pool and proxy address in `starter.yaml`. If the database was initialized with older values already persisted, you will usually need to delete and reinitialize it, or update the corresponding values again in the WebUI.

```yaml
system:
  api_port: 8855

model:
  proxy_base_url: "http://127.0.0.1:8855/responseProxy/v1"
  proxy_api_key: "loopai-local-proxy"
  default_model: "default"
  codex_model: "default"
  looper_model: "default"
  default_tier: "medium"
  pool:
    - tier: "medium"
      name: "default"
      api_key: "<YOUR_DEEPSEEK_API_KEY>"
      base_url: "https://api.deepseek.com"
      model_name: "deepseek-v4-flash"
      maxworker: 1
      wire_api: "chat"
      response_format: ""
      enabled: true
```

Then configure the Codex / Starter request endpoint in the WebUI as:

```text
http://127.0.0.1:8855/responseProxy/v1
```

The real upstream provider address should live in `model.pool[*].base_url`, for example `https://api.deepseek.com`, rather than in the older standalone `codex_chat_proxy_url` field.

#### iKun forwarding

- Public endpoint: <https://api.ikuncode.cc/> with the `Codex-Mixed` or `Pro` group
- `base_url`: `https://api.ikuncode.cc/v1`

You can also use the team iKun setup:

```bash
export ikun=sk-UPvr8tNdwgr9otaeAnfC3udHkekPhWCJzNQQ2ryiFppFEWUA
codex -c 'model_provider=ikun'
```

### 2. Startup Order

After the DeepSeek proxy or another Codex path is ready, start the backend in a separate terminal:

```bash
python api/start.py
```

Then fill the corresponding request endpoint in the WebUI and click `Update`.

---
