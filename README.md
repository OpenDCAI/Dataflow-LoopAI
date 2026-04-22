<div align="center">
  <img src="docs/assets/LoopAI.svg" width="160" alt="LoopAI Logo" />
  <h1>LoopAI: A Closed-loop Optimization Framework</h1>

  <p>
    <a href="https://www.python.org/">
      <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" />
    </a>
    <a href="./LICENSE">
      <img src="https://img.shields.io/badge/License-Apache_2.0-2F80ED?style=flat-square&logo=apache&logoColor=white" />
    </a>
  </p>

  <h4><i>✨ An Intelligent System with Self-Optimization Capabilities ✨</i></h4>
</div>

<br>

English | [简体中文](./docs/README_zh.md)

LoopAI is an intelligent system designed for **self-optimization of LLMs in domain-specific scenarios**. It automatically detects and evaluates generation deficiencies, and continuously improves model performance through **dialog-driven data acquisition and closed-loop optimization**.

```text
User  ⇄  Starter (Supervisor)  ⇄  Sub-Agent
                  │
                  ├── Common Question → Direct Response
                  └── Complex Task → Graph Execution
                                 (Evaluation → Data Collection → Training)
```

<p align="center">
  <img src="docs/assets/workflow.svg" alt="LoopAI Workflow" width="90%"/>
</p>

---

## 📰 1. News

* **[2026-03] 🎉 LoopAI (v0.1.0) is officially open-sourced!**
  We are excited to release the first version of LoopAI, enabling full automation from **natural language instructions to model optimization**.
  Say goodbye to tedious manual pipelines—LLM evaluation and optimization are now as simple as chatting.
  ⭐ Feel free to star the project and follow future updates!

---

## 💡 2. Why LoopAI?

Traditional LLM optimization workflows require users to manually:

* Evaluate model outputs
* Analyze failure cases
* Collect and curate training data

**LoopAI redefines this paradigm**:

> 🚀 *Everything that can be automated is handled by Agents.*

From evaluation to retraining, LoopAI provides a **seamless, interactive, and fully automated optimization experience**.

---

## 🔍 3. Overview

LoopAI reformulates the LLM optimization pipeline into a **graph-based execution framework (Graph / Node / State)**, enabling a new generation of interactive optimization systems:

* 🗣️ **NL2Optimize**
  Simply describe your goal in natural language (e.g., *“Improve my model's code generation ability”*), and LoopAI will automatically plan the optimization workflow.

* 🔄 **End-to-End Automation**
  Covers the full pipeline: evaluation → error analysis → data acquisition → retraining.

* 👨‍💻 **Human-in-the-Loop**
  Supports manual intervention at critical steps (e.g., reviewing evaluation results, selecting data), allowing flexible strategy adjustment.

* 📊 **Scalable Architecture**
  Built on LangGraph state management, easily integrates private datasets and custom evaluation metrics.

---

## 🚀 4. Quick Start

### 4.1 Installation

```bash
conda create -n loopai python=3.12
conda activate loopai

pip install uv
uv venv
source .venv/bin/activate

uv pip install -e .
```

---

### 4.2 Configure LoopAI

All run modes require a root-level `starter.yaml`.

1. Copy the starter configuration to the repository root:

```bash
cp examples/config/starter.yaml ./starter.yaml
```

2. Edit `starter.yaml` and fill at least the following `system` fields:

```yaml
system:
  starter_api_key: ""
  starter_model_path: ""
  starter_model_name: ""
  starter_base_url: ""
  tavily_api_key: ""
  kaggle_username: ""
  kaggle_key: ""
```

These values configure the Starter model provider and the external data-search credentials used by LoopAI.

For where to obtain `tavily_api_key`, `kaggle_username`, and `kaggle_key`, see [docs/API_KEYS.md](./docs/API_KEYS.md). Do not commit real credentials to the repository.

---

### 4.3 Start Services

LoopAI supports two modes:

#### ✅ Option A: WebUI API Mode (Recommended)

1. Install the published frontend dist.

For production or normal WebUI use, install the published frontend dist first. The backend serves `api/dist` directly, so you do not need to build or run the frontend dev server.

```bash
python scripts/download_ui_release.py
```

If the release asset cannot be downloaded automatically, download the frontend dist archive from the GitHub Release page manually, then extract it into `api/dist`.

2. Start the backend:

```bash
python api/start.py
```

The WebUI and API will be available at:

```
http://localhost:8855
```

API docs are available at:

```
http://localhost:8855/docs
```

---

<p align="center">
  <img src="./docs/assets/UI.png" alt="LoopAI UI" width="90%"/>
</p>

Frontend source setup, Vite proxy configuration, and UI release publishing are covered in [docs/Dev_README.md](./docs/Dev_README.md).

---

#### ✅ Option B: Terminal Mode

Start LoopAI:

```bash
python examples/scripts/run_starter.py
```

---

### 4.4 Optional Runtime Dependencies

`pip install -e .` installs the core LoopAI package, API service, graph orchestration, and common data-processing dependencies. Some Agents call heavy ML runtimes that are easier to keep in separate Conda environments because their CUDA, PyTorch, and serving requirements may conflict.

Recommended layout:

```bash
# Core LoopAI runtime
conda create -n loopai python=3.12

# Local OpenAI-compatible inference for Judger / Analyzer
conda create -n loopai-vllm python=3.10

# Local training with LlamaFactory
conda create -n loopai-llamafactory python=3.10

# Local training with verl
conda create -n loopai-verl python=3.10
```

Install `vllm`, `LLaMA-Factory`, and `verl` according to their upstream instructions and your CUDA/PyTorch version. They are not pinned in LoopAI because GPU environments are usually machine-specific.

Agent-specific notes:

* **JudgerAgent**: for local model evaluation, install `vllm` in a separate environment and set `judger.eval_vllm_env_path` to the Python executable, for example `/path/to/miniconda3/envs/loopai-vllm/bin/python`. When `judger.eval_base_url` is empty, Judger uses this interpreter to start a local vLLM OpenAI-compatible API server in a subprocess, with parameters such as `eval_vllm_port`, `eval_vllm_tensor_parallel_size`, `eval_vllm_gpu_memory_utilization`, and `eval_env_configs`. If you already run a compatible service yourself, set `judger.eval_base_url` and Judger will use that service instead.
* **AnalyzerAgent**: Analyzer calls an OpenAI-compatible chat endpoint through `analyzer.analyze_base_url`, `analyzer.analyze_model_path`, and `analyzer.analyze_api_key`. For local analysis, you can serve the analysis model with vLLM in the same vLLM environment and point `analyze_base_url` to it. Analyzer does not currently start vLLM by itself.
* **TrainerAgent**: local training normally requires `LLaMA-Factory` or `verl`. Set `trainer.train_framework` to `llamafactory` or `verl`. For LlamaFactory, set `trainer.llamafactory_dir` to the LLaMA-Factory repository and `trainer.llamafactory_env_path` to the environment root or `bin` directory, for example `/path/to/miniconda3/envs/loopai-llamafactory/bin`. For verl, provide `verl_dir` and `verl_env_path` in the trainer or system config. Trainer starts training asynchronously through an internal task manager, which launches the selected framework as a subprocess and streams logs back to LoopAI.

These fields can be provided through the WebUI Configer flow, in Agent state, or in `starter.yaml` under the corresponding `judger`, `analyzer`, `trainer`, or `system` sections.

---

## 🧠 5. Core Agents

Each Agent in LoopAI is implemented as an **independent and composable subgraph**.

### 🤖 StarterAgent (Supervisor)

* Handles user interaction and intent parsing
* Dynamically orchestrates downstream Agents
* Manages the overall execution workflow

### 🤖 JudgerAgent

* Automatically generates evaluation cases (LLM-based)
* Integrates external evaluation systems
* Collects structured results and logs

### 🤖 AnalyzerAgent

* Performs statistical analysis on evaluation results
* Identifies failure patterns and error types
* Generates interpretable diagnostic reports

### 🤖 ObtainerAgent & WebCrawlerAgent

* Derives data acquisition strategies
* Retrieves datasets and knowledge sources
* Cleans and structures data for training
* Supports extensible web data crawling

### 🤖 TrainerAgent

* Performs incremental training with new data
* Supports continual learning to prevent forgetting
* Enables closed-loop model improvement

### 🤖 ConfigerAgent

* Interacts with users for system configuration
* Supports dynamic parameter updates
* Handles missing information and workflow recovery

---

## 🚀 6. Future Work

We will continue improving LoopAI in the following directions:

* 💻 **Broader Domain Support**
* 🤖 **Stronger Agent Autonomy**
* 🌐 **Online Platform & Community**
* 📊 **Advanced Visualization Tools**

---

## 🙌 7. Contributing

We warmly welcome contributions!

* 📮 Submit issues via GitHub Issues
* 🔧 Contribute via Pull Requests

---

## 📄 8. License

This project is licensed under the **Apache 2.0 License**.
See the [LICENSE](./LICENSE) file for details.
