<div align="center">
  <img src="./docs/assets/LoopAI.svg" width="160" alt="LoopAI Logo" />
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

English | [简体中文](./README_zh.md)

LoopAI is an intelligent system designed for **self-optimization of LLMs in domain-specific scenarios**. It automatically detects and evaluates generation deficiencies, and continuously improves model performance through **dialog-driven data acquisition and closed-loop optimization**.

```text
User  ⇄  Starter (Supervisor)  ⇄  Sub-Agent
                  │
                  ├── Common Question → Direct Response
                  └── Complex Task → Graph Execution
                                 (Evaluation → Data Collection → Training)
````

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
pip install -e .
```

---

### 4.2 Start Services

LoopAI supports two modes:

#### ✅ Option A: API Mode (Recommended)

```bash
python api/start.py
```

API will be available at:

```
http://0.0.0.0:8855
```

---

<p align="center">
  <img src="./docs/assets/UI.png" alt="LoopAI UI" width="90%"/>
</p>

### 🖥️ Frontend Setup

#### 1. Install NVM

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
```

#### 2. Activate NVM

```bash
source ~/.bashrc  # or ~/.zshrc
```

#### 3. Install Node.js

```bash
nvm install 20
nvm use 20
nvm alias default 20
```

#### 4. Verify Installation

```bash
node -v
npm -v
```

#### 5. Install Yarn

```bash
corepack enable
corepack prepare yarn@stable --activate
```

#### 6. Install Dependencies

```bash
yarn
```

#### 7. Configure Backend Proxy

Edit `vite.config.js`:

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

#### 8. Start Frontend

```bash
yarn dev
```

---

#### ✅ Option A: Backend (Terminal Mode)

1. Copy configuration file:

```bash
cp examples/config/starter.yaml ./starter.yaml
```

2. Modify system settings in `starter.yaml`

3. Start LoopAI:

```bash
python examples/scripts/run_starter.py
```

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