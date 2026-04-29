# Architecture

LoopAI models LLM improvement as a repeatable graph workflow rather than a loose collection of scripts.

## Core abstractions

The system revolves around three concepts:

- `Graph`: defines orchestration relationships and stage transitions.
- `Node`: encapsulates one processing action such as evaluation, analysis, sampling, or training.
- `State`: carries context, stage outputs, and shared information across agents.

That makes LoopAI feel like an execution system instead of a one-off automation bundle.

## A typical closed loop

A common task moves through stages like these:

1. A user proposes a goal such as improving code generation quality.
2. Starter interprets the intent and builds an execution plan.
3. Judger evaluates current outputs and identifies failed samples.
4. Analyzer summarizes defect patterns and likely improvement directions.
5. Collector gathers or generates better-fit data.
6. Trainer launches training or fine-tuning.
7. The updated model returns to evaluation for the next loop.

## Why the graph matters

Graph execution provides three practical benefits:

- Inputs and outputs between stages stay clear, which helps debugging and reuse.
- Human review can be inserted at key checkpoints instead of hard-coding every decision.
- Model services, training frameworks, and data sources are easier to swap.

## Where humans fit in

LoopAI is not black-box automation. In real projects, data quality, training budget, and business goals often need human judgment, so the system leaves room for review, confirmation, and interruption.

That is one reason it works better for team collaboration than a pure script pipeline.
