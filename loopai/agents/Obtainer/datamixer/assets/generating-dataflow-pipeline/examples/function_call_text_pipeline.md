# Example: Function-Calling Conversation Synthesis

## Use Case

Convert text chats or scenarios into multi-turn function-calling training data
with composed tasks, tool schemas, calls, and evaluation metadata.

## Operator Decision

```text
chat -> ScenarioExtractGenerator -> scenario
  -> ScenarioExpandGenerator
  -> AtomTaskGenerator
  -> SequentialTaskGenerator / ParaSeqTaskGenerator
  -> CompositionTaskFilter
  -> FunctionGenerator -> functions
  -> MultiTurnConversationGenerator -> conversations
  -> FuncCallConversationSampleEvaluator
```

## Field Contract

- Source: `chat`
- Intermediate: `scenario`, `atom_task`, optional `subsequent_task`,
  `composition_task`, `functions`
- Output: `conversations` plus evaluator fields

## Key Notes

- Tool schemas must be executable and sufficient for the composed task.
- Preserve subtask dependencies; parallel tasks must not be serialized as if dependent.
- Require evaluator success for tool selection, argument correctness, turn order,
  and final response consistency.
