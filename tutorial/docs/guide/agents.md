# Agents

Each major LoopAI capability can be understood as a composable agent or subgraph module.

## StarterAgent

Starter is the coordinator. It mainly handles:

- user interaction
- intent detection
- choosing the right execution path
- chaining together downstream agent work

If LoopAI is treated like an operating system, Starter is closest to the task scheduler.

## JudgerAgent

Judger focuses on current model quality. It usually handles:

- running evaluations
- comparing results
- locating failed samples
- providing evidence for later analysis

When local or remote OpenAI-compatible services are configured, Judger can work across different inference backends.

## AnalyzerAgent

Analyzer turns observations into conclusions:

- grouping failure patterns
- analyzing likely causes
- generating actionable optimization suggestions

It connects evaluation results with data strategy and is a key part of the loop.

## TrainerAgent

Trainer carries out the actual optimization step, such as:

- invoking training frameworks
- launching asynchronous jobs
- collecting training logs
- sending results back into system state

Local training is typically integrated with `LLaMA-Factory` or `verl`.

## Why split the system into agents

This split gives the system three clear advantages:

- Each module has a narrower responsibility and is easier to replace or test.
- New capabilities can be added without rewriting the entire flow.
- Teams can choose which steps to automate and which steps to review manually.
