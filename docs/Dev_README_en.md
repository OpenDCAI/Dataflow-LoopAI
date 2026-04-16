# Dataflow-LoopAI Development Guide

[简体中文](./Dev_README.md) | English

Dataflow-LoopAI is an intelligent system with self-optimization capabilities. It detects and evaluates generation weaknesses in domain-specific LLM scenarios, then improves models through dialog-driven data retrieval and closed-loop optimization.

```text
User  <->  Starter / Manager  <->  LangGraph state machine
                      |
                      +-- Common question -> Direct response
                      +-- Complex task    -> Graph execution
                                           (Evaluation -> Data mining -> Training)
```

## Framework Overview

<p align="center">
  <img src="assets/workflow.svg" alt="Dataflow-LoopAI workflow" width="90%"/>
</p>

## Project Layout

```text
Dataflow-LoopAI/
├── api/                       # WebUI backend, FastAPI service, static dist hosting
│   ├── app/controllers/       # config / task / resource / starter routes
│   ├── app/utils/             # Starter runtime, resource preview, hardware monitor helpers
│   ├── db/                    # SQLite database directory
│   └── dist/                  # Published frontend dist served by FastAPI
│
├── examples/                  # Example scripts and runnable cases
│   └── scripts/               # Startup and test scripts
│
├── loopai/                    # Core framework
│   ├── agents/                # Agents, each implemented as a composable subgraph
│   │   ├── BaseAgent/         # Base agent definition
│   │   ├── Starter/           # Main supervisor agent
│   │   ├── Analyzer/          # Evaluation and analysis agent
│   │   ├── Obtainer/          # Data acquisition agent
│   │   └── ...                # Other agents
│   ├── common/                # Shared helpers, prompts, i18n
│   ├── memory/                # Persistent memory and checkpoint storage
│   ├── schema/                # State and event definitions
│   └── utils/                 # Common utilities
│
├── scripts/                   # Release and installation helpers
│   ├── download_ui_release.py # Download frontend dist release into api/dist
│   └── release_ui.sh          # Tag UI release and trigger GitHub Actions
│
├── ui/                        # Vue 3 + Vite frontend source
│   └── src/                   # Views, components, router, API client wrappers
│
└── docs/                      # Documentation and assets
    └── assets/                # Images and diagrams
```

## Implemented Agents

Each LoopAI agent is an independently runnable and composable subgraph.

### StarterAgent

The supervisor entry point. It talks to the user, parses intent, selects downstream agents, and manages the overall workflow.

### JudgerAgent

Runs model evaluation. It can generate evaluation cases, call external judging systems, and collect structured evaluation results.

### AnalyzerAgent

Analyzes Judger results, identifies failure patterns, and produces readable diagnostic reports.

### ConfigerAgent

Handles interactive configuration updates, missing-field feedback, and recovery from interrupted workflows.

### ObtainerAgent

Analyzes data requirements, searches for relevant datasets or web sources, and converts data into training-ready formats.

### TrainerAgent

Runs incremental training from new data and participates in the closed-loop optimization flow. It is integrated into LoopAI and is normally orchestrated by `StarterAgent`, not launched as a standalone API service.

## Installation

```bash
pip install -e .
```

## Quick Terminal Usage

Copy the starter config and edit system parameters:

```bash
cp examples/config/starter.yaml ./starter.yaml
```

Start LoopAI in terminal mode:

```bash
python examples/scripts/run_starter.py
```

## WebUI Frontend Development

For production or normal WebUI usage, prefer downloading the published frontend dist:

```bash
python scripts/download_ui_release.py
```

The steps below are only needed when modifying or debugging `ui/` source code.

### 1. Install NVM

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
```

### 2. Activate NVM

```bash
source ~/.bashrc  # or ~/.zshrc
```

### 3. Install Node.js

```bash
nvm install 20
nvm use 20
nvm alias default 20
```

### 4. Verify Installation

```bash
node -v
npm -v
```

### 5. Install Yarn

```bash
corepack enable
corepack prepare yarn@stable --activate
```

### 6. Install Dependencies

```bash
cd ui
yarn
```

### 7. Configure Backend Proxy

Edit `ui/vite.config.js` if the backend is not running on `127.0.0.1:8855`:

```javascript
server: {
  host: '0.0.0.0',
  proxy: {
    '/api': {
      target: 'http://<host>:8855/',
      changeOrigin: true,
      rewrite: path => path.replace(/^\/api/, '')
    }
  }
}
```

### 8. Start Frontend

```bash
yarn dev
```

The Vite development server proxies `/api/*` to the FastAPI backend. In production, `python api/start.py` serves static files directly from `api/dist`.

### 9. Publish Frontend Dist

After frontend changes are ready, run the release helper from the repository root:

```bash
bash scripts/release_ui.sh [optional-version]
```

If the version is omitted, the script prompts for one. It updates `ui/package.json`, creates a `ui-v<version>` tag, pushes the branch and tag, and lets GitHub Actions build and publish the frontend dist release asset.

## Quick Development Flow

### Start vLLM

```bash
conda activate vllm
bash examples/scripts/run_manager_vllm.sh
```

### Run A Judger Example

Update paths and model settings in the script, then run:

```bash
python examples/scripts/run_judger.py
```

### Run An Obtainer Example

Configuration requirements:

- Copy `examples/config/starter.yaml` to `./starter.yaml`.
- Configure model paths and Obtainer parameters.
- Add Kaggle credentials if Kaggle access is required.
- Add `examples/scripts/tavily_api_key.txt` for Tavily search.
- Add `rag_api_key.txt` in the repository root for API-based embedding models.

Run:

```bash
bash examples/scripts/run_obtainer.sh
```

## Defining A New Agent

Each custom Agent is a subgraph composed of node functions and edge logic. It can be integrated into `StarterAgent` for coordinated scheduling.

### Inherit From BaseAgent

Custom agents should inherit from `BaseAgent`, which provides:

- event recording through `agent_event`;
- optional LLM node construction through `create_llm_node`;
- the standard `init_graph` graph initialization entry point;
- a unified `__call__` protocol.

### Initialize The Graph

```python
def init_graph(self, **kwargs):
    builder = StateGraph(LoopAIState)
    ...
    self.graph = builder.compile(
        checkpointer=self.checkpointer,
        store=self.store,
        **kwargs
    )
```

### Agent Invocation

Subgraph mode:

```python
self.init_graph(**kwargs)
return self.graph
```

Streaming invocation inside `StarterAgent`:

```python
for res in self.graph.stream(
        Command(resume=input),
        subgraphs=True,
        stream_mode=["updates", "messages"],
        **invoke_args
    ):
    yield res
```

## Agent Conventions

Recommended naming and organization:

- Agent class names use PascalCase, for example `AnalyzerAgent`.
- Agent folders use PascalCase.
- Python files use lowercase with underscores, for example `eval_model.py`.
- Put complex node logic in `nodes/`.
- Put helper functions in `utils/`.
- Put LLM tools in `tools/`.
- Keep the agent class thin; put business logic in nodes and helpers.

## Prompt Management

LoopAI provides a shared prompt-template loading mechanism under:

```text
loopai/common/prompts/
```

`BaseAgent` initializes:

```python
self.prompt_loader = PromptLoader(prompt_template_dir)
```

Each `BaseAgent` subclass must define:

```python
@property
@abstractmethod
def system_prompt_type(self) -> str:
    return "system"

@property
@abstractmethod
def system_prompt_name(self) -> str:
    pass
```

`system_prompt_type` maps to files such as `system_prompt.json`, `user_prompt.json`, or `assistant_prompt.json`. `system_prompt_name` selects a key from that file.

## Tool Call Notes

LoopAI rewrites the ReAct node. Because sub-agents may still be affected by context from other tools, custom tools should return plain `dict` objects whenever possible. This helps `StarterAgent` and other sub-agents validate results consistently.

## Runtime Event Monitoring

`BaseAgent` includes `AgentEvent`, which records:

- event type, such as update, message, or custom;
- current node;
- state updates;
- streamed messages;
- node path;
- custom event payloads.

Although each agent could theoretically maintain its own `AgentEvent`, LoopAI currently centralizes event management in `StarterAgent`.

LangGraph events used by LoopAI:

- `update`: emitted after a node finishes and updates state;
- `message`: emitted by ChatOpenAI-based message streams;
- `custom`: emitted by user-defined stream writers.

For real-time node progress or temporary data that should not be stored in `LoopAIState`, use custom events with `get_stream_writer`.

```python
from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

writer = get_stream_writer()

writer(StreamEvent(
    current=state["current"],
    data={"configer_error": state["configer_error"]}
).json())
```

`StarterAgent` records custom events under:

```text
AgentEvent.custom_info[state["current"]]
```

## Exception Handling

Nodes can trigger a configuration recovery flow when required parameters are missing. The common pattern is to update state fields such as:

- `exception`: exception type, for example `ConfigerError`;
- `next_to`: the recovery node, for example `config_node`;
- `automated_query`: an automatic prompt used after configuration is completed;
- `configer.configer_error`: missing fields passed to `ConfigerAgent`;
- `goto_node`: the parent graph node to jump to for exception handling.

When a problem cannot be repaired by Configer but the workflow should continue, set `next_to` to `query_node` and provide an appropriate `automated_query`. `StarterAgent` then guides the user through the needed manual action.
