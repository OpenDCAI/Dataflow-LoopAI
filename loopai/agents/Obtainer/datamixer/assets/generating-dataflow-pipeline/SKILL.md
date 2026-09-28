---
name: generating-dataflow-pipeline
description: Reasoning-guided planner for filtering, normalizing, and enriching existing DataFlow records
metadata:
  version: 1.0.0
---
# Existing-Data DataFlow Pipeline Code Generator

## Goal

This skill is used when users provide:

- **Target**: What the pipeline should achieve
- **Sample Data File**: Path to a JSONL file containing representative records from
  the existing dataset

The skill must:

1. **Read and analyze the JSONL file** at the provided path
2. Infer data structure, field types, and content characteristics
3. Determine the existing-record task (filtering, normalization, rewriting, enrichment,
   or multi-field composition)
4. Select appropriate operators from preferred primitives
5. Validate field dependencies
6. Output intermediate operator decision summary
7. Generate standard DataFlow pipeline code with `first_entry_file_name` set to the user-provided file path

## User Input Format

Users provide:

```
Target: [Clear task description]
Sample file: [Path to JSONL file, e.g., ./data/input.jsonl]
```

**Important**: The sample file is a JSONL file (one JSON object per line), not a JSON array.

## Preferred Operator Strategy

**Five Core Primitives** (high-coverage operators for existing-data processing):

1. `PromptedGenerator` - Single-field LLM generation
2. `FormatStrPromptedGenerator` - Multi-field template generation
3. `Text2MultiHopQAGenerator` - Multi-hop QA pair construction
4. `PromptedFilter` - LLM-based quality filtering
5. `GeneralFilter` - Rule-based filtering
These are **preferred primitives**, not fixed workflows. They can be used repeatedly and combined flexibly.
The default objective is to improve and select records that already exist in the input
JSONL. Treat the pipeline as an existing-data **filtering + shaping** workflow:
retain/reject records, normalize their representation, rewrite fields when the
training contract requires it, and add grounded derived fields without breaking the
source semantics.

## Operator Selection Priority Rule (MANDATORY)

When a specialized operator exists for the task, it MUST be used over generic operators. Do NOT use `PromptedGenerator` to replicate functionality that a dedicated operator already provides. The prompt-transparent training-target rule below takes precedence: a specialized operator that injects task, reasoning, style, or answer-format instructions cannot produce the final assistant target.

**Decision table** (check in order, use the first match):

| Task / Scenario                                 | Required Operator                                                                               | Do NOT use                                 |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------ |
| Generate QA pairs from text                     | `Text2MultiHopQAGenerator`                                                                    | `PromptedGenerator` with QA prompt       |
| Score / evaluate using multiple fields          | `FormatStrPromptedGenerator` + `GeneralFilter`                                              | `PromptedFilter` (single input_key only) |
| Filter by deterministic rule on existing fields | `GeneralFilter`                                                                               | `PromptedFilter`                         |
| Generate new content from a single field        | `PromptedGenerator`                                                                           | —                                         |
| Generate new content from multiple fields       | `FormatStrPromptedGenerator`                                                                  | Multiple `PromptedGenerator` steps       |

**Key principle**: `PromptedGenerator` is the fallback for generic single-field generation. If the target mentions "QA", "question-answer", "问答" — always reach for `Text2MultiHopQAGenerator` first.

## Field Flow Rules (MANDATORY)

1. **Inspect record content first**: Read representative records and identify the
   semantic role and fitness of their content; field names and presence alone do
   not establish that content is suitable for the target.
2. **Design from the target contract**: Select source and generated fields based
   on the content required by the target, including improved semantic
   counterparts when existing content does not fit that contract.
3. **Keep execution ordered**: Every field consumed by an operator must be
   available from the input or produced by an earlier operator.
4. **Never reference before creation**: Create a generated field before any
   downstream operator evaluates, filters, or transforms it.

```
✗ WRONG: Filter by "quality_score" before generating it
✓ CORRECT: Generate "quality_score" first, then filter by it
```

## Grounded Field Generation Pattern (RECOMMENDED)

When constructing improved training fields from records that already contain a
source instruction, answer, implementation, or other target-bearing content,
ground each generated stage in the source semantics. A useful reference flow is:

```text
source content + source answer
  -> generate an improved question/instruction

improved question/instruction + source answer
  -> generate or revise the answer

improved question/instruction + source answer + generated answer
  -> evaluate semantic consistency and target fitness
```

Adapt the field names and operators to the task. This is a reference pattern,
not a requirement to generate every role on every dataset. Its purpose is to
prevent an answer generator from seeing only a rewritten question and freely
inventing behavior that is unsupported by the source answer. The final quality
evaluation should reject generated answers that omit supported behavior or add
unsupported contracts, constraints, edge cases, exception handling, or other
observable semantics, even when the generated question and answer are mutually
consistent.

Before expensive LLM generation, prefer multiple applicable non-LLM filtering
operators when the source pool is large enough to support meaningful attrition.
Use deterministic checks for defects that can be established reliably, such as
parseability, required content shape, empty or truncated content, duplicates,
unsafe patterns, invalid values, and task-specific structural invariants. Keep
these filters content-aware and avoid treating familiar field names or mere
field presence as evidence of quality. The goal is to reduce generation load
while retaining enough valid and diverse source records for the downstream
target.

For vertical domains whose target capability inherently requires reasoning,
the pipeline MUST use operators to distinguish records that contain suitable
chain-of-thought (CoT) content from those that do not. For a record without
suitable CoT, assess the problem's difficulty and either retain it by routing
that branch through an appropriate reasoning-generation operator to construct
CoT, or filter it out when generating useful reasoning is unwarranted or
unreliable.

## Training-Target Generation, Judging, and DAG Routing (MANDATORY)

### Prompt-transparent strong-model targets

When an LLM completion will become the assistant-side training target, including
answer or reasoning regeneration, send the strong teacher only the canonical
user question/task and source context that is part of that task. Do not inject a
system prompt, answer hint, gold/reference answer, rubric, benchmark description,
reasoning instruction, style instruction, format instruction, or training-data
metacomment. Use an empty system/user prefix and no output schema when the
operator supports them, and preserve the teacher's natural response as the
candidate target. If an operator necessarily adds such instructions, it is not
eligible to generate the final training target; use a prompt-transparent serving
path instead.

This restriction applies to model text retained as training data, not to
question synthesis, routing labels, metadata extraction, or evaluator output.
Keep the gold answer hidden from the target generator and expose it only to
downstream verification. Deterministic cleanup may remove transport artifacts,
but must not rewrite the answer's substance. A recovery branch for a rejected
target may deterministically normalize it or rerun the prompt-transparent strong
teacher on the canonical task; it must not add a repair prompt whose response is
then silently used as the training target.

### Question-aware LLM judging

Every correctness or answer-quality LLM judge MUST see all three semantic
inputs in the same evaluation request:

1. the canonical question/task;
2. the candidate answer being judged;
3. the standard/gold answer.

Use a native question-aware evaluator when available. Otherwise compose an
explicit judge payload from those three existing fields before invoking the
evaluator. Never judge only the answer, or only answer plus gold. The judge may
receive a rubric because its output is evaluation metadata, not training text.
If a trustworthy standard answer is unavailable, do not claim that an LLM
correctness gate passed; route the record to a separately declared unsupported
or human-review path.

### Conditional DAG subgraphs

Design the pipeline as an auditable DAG, even when its main path is linear.
Declare nodes, edges, split conditions, join points, and per-branch input/output
fields. Operators inside a branch still execute in dependency order.

- Send failures caused only by correctable format, wrapper, or style issues to a
  separate recovery branch. Do not route correctness, grounding, safety, or
  semantic-quality failures through that branch.
- Keep the recovery branch dormant while the accepted main-path pool satisfies
  the configured dataset/bucket target. Activate it only when a measured quota
  or minimum output-size check reports a shortfall. If no scale target exists,
  do not invent one merely to activate recovery.
- Revalidate recovered records with the same downstream correctness and quality
  gates before joining them back into the main flow. Preserve `sample_id`, source
  provenance, original position, branch path, rejection reason, rewrite method,
  and pre/post values; deduplicate at the join and restore input order.
- Materialize branch counts and skipped/activated decisions so the trial review
  can verify the trigger rather than infer it from final output alone.

### Rollout-based difficulty gate

For tasks with verifiable answers, difficulty filtering SHOULD use the actual
target/candidate model rather than only a strong-model difficulty score:

1. Run at most four independent, prompt-transparent target-model rollouts per
   question. Compare each response with the gold answer using a deterministic
   task verifier when possible; otherwise use the question-aware LLM judge above.
2. Continue while all observed results have the same correctness value, up to
   four rollouts. Once both a correct and an incorrect result exist, the sample
   is already in the mixed group and further rollouts are optional.
3. Route the resulting groups explicitly. Remove `all_correct` samples from both
   SFT and GRPO pools. Retain `mixed` samples when they pass the other quality
   gates. Never make a direct keep/drop decision for `all_wrong` samples.
4. Every `all_wrong` sample MUST enter a separate strong-model rollout branch.
   Run a prompt-transparent strong teacher on the canonical task, then compare
   its response against the gold with the same verifier or a judge that sees
   question, strong-model answer, and gold. Retain only records established as
   hard-but-valid; quarantine or reject broken, ambiguous, unsolvable, or
   incorrect-gold records. An inconclusive diagnosis must not silently pass.

Persist model identities, rollout count, raw responses, parsed answers,
per-rollout correctness, verifier/judge evidence, group assignment, strong-model
diagnosis, and final routing decision. Rollout responses used only for difficulty
or diagnosis are audit evidence and must not automatically replace the selected
training target.

## Prompted Operator Usage Policy (MANDATORY)

- Don't mechanically create one prompted operator per tiny requirement. If one operator can handle multiple related transformations, prefer that over splitting.
- Multiple prompted operators are allowed when the task genuinely requires distinct semantic transformations. If using multiple, justify each step's role, input field, and output field.

## GeneralFilter Field Safety Rule (MANDATORY)

`GeneralFilter` lambda rules must ONLY reference fields that exist in sample data or are produced by upstream steps.

## Multi-Field Filtering Pattern (MANDATORY)

`PromptedFilter` only accepts a single `input_key`. For multi-field evaluation (e.g., scoring QA pairs), use `FormatStrPromptedGenerator` to score + `GeneralFilter` to filter.

**Important caveat for `Text2MultiHopQAGenerator` output**: The `QA_pairs` column is a nested list of dicts, not separate `question`/`answer` columns. You **cannot** directly pass `question` or `answer` as kwargs to `FormatStrPromptedGenerator` after `Text2MultiHopQAGenerator`. To score or filter individual QA pairs, use **post-processing** (explode the list into rows, then optionally score/filter in a second pipeline or in Python code).

## Output Contract (MANDATORY)

**Two-stage output required**:

### Stage 1: Intermediate Operator Decision (JSON)

Output this first:

```json
{
  "ops": ["OperatorA", "OperatorB", "OperatorC"],
  "dag": {
    "nodes": ["main_filter", "scale_gate", "recovery", "rollout_gate", "strong_diagnosis", "join"],
    "edges": ["main_filter -> scale_gate", "scale_gate(shortfall) -> recovery", "rollout_gate(all_wrong) -> strong_diagnosis"],
    "joins": ["recovery -> join"],
    "branch_triggers": {"recovery": "main_pass_count < required_count"}
  },
  "field_flow": "fields and branch-specific transitions through the DAG",
  "reason": "Why this DAG satisfies the target, how dependencies and conditional branches are enforced, and why prompted operators are or are not used."
}
```

### Stage 2: Complete Response (5 sections)

1. **Field Mapping**: Map sample fields to semantic roles, identify fields to generate
2. **DAG Operator Graph**: List nodes in topological order, plus edges, branch triggers, joins, and operator justification
3. **Reasoning Summary**: Explain operator selection, field flow, rollout routing, recovery activation, and why this design
4. **Complete Standard Pipeline Code**: Full executable Python following repository style
5. **Adjustable Parameters / Caveats**: Tunable parameters, fallback strategies, debugging tips

## Standard Code Generation Rule (MANDATORY)

**All generated Python code must follow the standard pipeline organization shown in the `examples/` folder of this skill package.**

**Input Data Format**:

- `first_entry_file_name` MUST be set to the **user-provided file path** (the JSONL sample file)
- File extension must be `.jsonl` (one JSON object per line, NOT an array)
- **DO NOT create new file paths** - use the exact path the user provided

**Required structure**: `__init__` (storage + llm_serving + operators) →
`forward` (topological execution of `operator.run(storage=..., ...)`, including
explicit conditional branches and joins where required) →
`if __name__ == "__main__"` entry point. A linear pipeline is a valid
single-path DAG, but it must not erase conditional recovery or rollout branches
required by the rules above.

**DO NOT**: generate custom runtime executors, `forward(plan)` style frameworks, or dynamic dispatch engines.

## Operator Parameter Signature Rule (MANDATORY)

Use repository-valid constructor/run signatures only. Never invent parameter names.

### Base Components

**`FileStorage`**

```python
FileStorage(
  first_entry_file_name="...jsonl",
  cache_path="./cache",
  file_name_prefix="dataflow_cache_step",
  cache_type="jsonl"
)
```

**`APILLMServing_request`**

```python
APILLMServing_request(
  api_url=os.environ["DF_API_URL"],
  key_name_of_api_key="DF_API_KEY",
  model_name=os.environ["DF_MODEL_NAME"],
  max_workers=10
)
```

### Five Core Operators: Signatures + Key Requirements

**1) `PromptedGenerator`**

- Constructor: `PromptedGenerator(llm_serving, system_prompt="You are a helpful agent.", user_prompt="", json_schema=None)`
- Run: `run(storage=self.storage.step(), input_key="raw_content", output_key="generated_content")`
- `input_key` column must exist. Generated rows written to `output_key`.

**2) `FormatStrPromptedGenerator`**

- Constructor: `FormatStrPromptedGenerator(llm_serving, system_prompt="You are a helpful agent.", prompt_template=FormatStrPrompt(...), json_schema=None)`
- Run: `run(storage=self.storage.step(), output_key="generated_content", **input_keys)`
- `**input_keys`: each kwarg maps a **template variable name** (key) to a **dataframe column name** (value). Internally does `row[input_keys[key]]` per row, then `prompt_template.build_prompt(need_fields, **key_dict)`.
- Kwarg keys must match `{placeholder}` names in `FormatStrPrompt.f_str_template`. Kwarg values must be existing dataframe columns.
- `prompt_template` cannot be `None` (raises `ValueError`). Must pass an instantiated `FormatStrPrompt(f_str_template="...")`.
- Import: `from dataflow.prompts.core_text import FormatStrPrompt`

**3) `Text2MultiHopQAGenerator`**

- Constructor: `Text2MultiHopQAGenerator(llm_serving=self.llm_serving, seed=0, lang="en", prompt_template=None, num_q=5)`
  - `llm_serving` — LLM serving instance (required)
  - `seed` (int, default `0`) — random seed for reproducibility
  - `lang` (str, default `"en"`) — language for generation prompt; controls sentence splitting (`"."` for `"en"`, `"。"` for `"zh"`)
  - `prompt_template` — custom `DIYPromptABC` instance; pass `None` to use default `Text2MultiHopQAGeneratorPrompt`
  - `num_q` (int, default `5`) — **maximum** number of QA pairs to **keep** per input row (truncates the generated list; actual generation count depends on sentence triples in the text)
- Run: `run(storage, input_key="cleaned_chunk", output_key="QA_pairs", output_meta_key="QA_metadata")`
  - `input_key` must exist (cleaned text chunk column)
  - `output_key` — column containing a **nested list** of QA dicts per row. Each dict has keys: `question` (str), `reasoning_steps` (list of `{step: str}`), `answer` (str), `supporting_facts` (list of str), `type` (str)
  - `output_meta_key` — column containing metadata dict per row with keys: `source`, `timestamp`, `complexity`
  - Output column named by `output_key` / `output_meta_key` must NOT pre-exist.
- Each input row produces **one row** with a nested list in the `output_key` column. The list items are dicts — `question`, `answer`, etc. are **NOT** separate dataframe columns. Downstream operators like `FormatStrPromptedGenerator` cannot directly reference `question` or `answer` as column names. To use individual QA pairs downstream, you must **post-process** (explode the list into separate rows) outside the operator chain.
- **Input text constraints** (texts failing these checks produce empty `qa_pairs: []`):
  - Length: 100–200,000 characters
  - Must contain at least 2 sentences (2+ `.` or 2+ `。`)
  - Special character ratio must be ≤ 30%

**4) `PromptedFilter`**

- Constructor: `PromptedFilter(llm_serving, system_prompt="...", min_score=1, max_score=5)`
- Run: `run(storage=self.storage.step(), input_key="raw_content", output_key="eval")`
- `input_key` must exist. `output_key` is numeric score column; rows outside `[min_score, max_score]` are filtered out.

**5) `GeneralFilter`**

- Constructor: `GeneralFilter([lambda df: df["score"] >= 4, ...])`
- Run: `run(storage=self.storage.step())`
- Each rule must return boolean `pd.Series`. Referenced fields must already exist.

### Correct Import Paths (MANDATORY)

```python
# Base components
from dataflow.utils.storage import FileStorage
from dataflow.serving import APILLMServing_request

# Existing-data operators
from dataflow.operators.core_text import PromptedGenerator, FormatStrPromptedGenerator, Text2MultiHopQAGenerator, PromptedFilter, GeneralFilter
```

## Extended Operator Reference: core_text Skill

The sibling skill **`core_text`** (located at `../core_text/`) provides detailed per-operator API documentation that supplements the summary signatures above.

**Each operator directory contains**:

- `SKILL.md` — Full English reference: constructor signature, `run()` signature, execution logic, mandatory rules, return value semantics
- `SKILL_zh.md` — Chinese translation of the reference
- `examples/good.md` — Best-practice pipeline example
- `examples/bad.md` — Common mistakes and failure cases

**When to consult `core_text`**:

- When generating pipeline code that uses an operator beyond the 5 core primitives (e.g., `BenchAnswerGenerator`, `ChunkedPromptedGenerator`, `EmbeddingGenerator`, `RetrievalGenerator`, `RandomDomainKnowledgeRowGenerator`)
- When you need to verify edge-case behavior, return value semantics, or error conditions for any operator
- When debugging generated pipeline code — the `bad.md` examples document the most frequent mistakes

**Note**: The 5 core primitives documented above in "Operator Parameter Signature Rule" remain the primary reference for standard pipeline generation. The `core_text` skill provides deeper detail and covers additional operators not in the core set.

---

### Generate Operators

**Path**: `../core_text/generate/`

**Available operator references** (8 operators):

| Operator                              | Subdirectory                               | Description                                                                                                         |
| ------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `PromptedGenerator`                 | `prompted-generator/`                    | Single-field LLM generation — full execution logic, skip-falsy rules                                               |
| `FormatStrPromptedGenerator`        | `format-str-prompted-generator/`         | Multi-field template generation — placeholder-to-column mapping details,`@prompt_restrict` validation            |
| `Text2MultiHopQAGenerator`          | `text2multihopqa-generator/`             | Multi-hop QA pair construction — text filtering thresholds (100–200k chars), output structure, row-count behavior |
| `BenchAnswerGenerator`              | `bench-answer-generator/`                | Benchmark answer generation —`eval_type` variants, conditional field requirements                                |
| `ChunkedPromptedGenerator`          | `chunked-prompted-generator/`            | Long document chunk-by-chunk processing — token-based splitting, file I/O conventions                              |
| `EmbeddingGenerator`                | `embedding-generator/`                   | Text vectorization — supported serving backends,`/v1/embeddings` endpoint usage                                  |
| `RandomDomainKnowledgeRowGenerator` | `random-domain-knowledge-row-generator/` | Domain-specific row generation — seed dataframe requirements,`generation_num` constraints                        |
| `RetrievalGenerator`                | `retrieval-generator/`                   | Async RAG generation —`LightRAGServing.create()` async initialization, `await run()` requirement               |

---

### Eval Operators

**Path**: `../core_text/eval/`

**Available operator references** (5 operators):

| Operator                          | Subdirectory                          | Description                                                                                                  |
| --------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `BenchDatasetEvaluator`         | `bench-dataset-evaluator/`          | Benchmark answer comparison —`match` (math verification) and `semantic` (LLM-based) modes               |
| `BenchDatasetEvaluatorQuestion` | `bench-dataset-evaluator-question/` | Extended benchmark evaluator — adds question context and subquestion support over `BenchDatasetEvaluator` |
| `PromptedEvaluator`             | `prompted-evaluator/`               | LLM-based row scoring  — writes score into new column without removing rows                                |
| `Text2QASampleEvaluator`        | `text2qa-sample-evaluator/`         | QA pair quality evaluation — 4 dimensions, 8 output columns (grades + feedbacks per dimension)              |
| `UnifiedBenchDatasetEvaluator`  | `unified-bench-dataset-evaluator/`  | Unified benchmark evaluation — 6 `eval_type` variants, writes 4 output columns                            |

---

### Filter Operators

**Path**: `../core_text/filter/`

**Available operator references** (3 operators):

| Operator                | Subdirectory              | Description                                                                                                             |
| ----------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `GeneralFilter`       | `general-filter/`       | Rule-based row filtering — lambda conditions combined with AND, removes rows only, adds no new columns                 |
| `KCenterGreedyFilter` | `kcentergreedy-filter/` | Diversity-based downsampling — K-Center Greedy algorithm, requires pre-computed embedding vectors                      |
| `PromptedFilter`      | `prompted-filter/`      | LLM semantic filtering — internally uses `PromptedEvaluator`, retains rows with scores in `[min_score, max_score]` |

---

### Refine Operators

**Path**: `../core_text/refine/`

**Available operator references** (2 operators):

| Operator            | Subdirectory          | Description                                                                                    |
| ------------------- | --------------------- | ---------------------------------------------------------------------------------------------- |
| `PandasOperator`  | `pandas-operator/`  | Custom DataFrame transformation — applies a sequential list of functions, no LLM calls        |
| `PromptedRefiner` | `prompted-refiner/` | LLM text refinement — rewrites text in-place, overwrites original column with refined results |

## Existing Record Content Analysis Rule (MANDATORY)

Analyze the actual record content to determine the filtering and enrichment task:

**Plain text fields** (e.g., `text`, `content`, `review_text`, `raw_content`):

- → inspect representative records first
- → use deterministic pre-filters for parseability, emptiness, duplication, safety,
  and structural invariants
- → use LLM evaluation/filtering and, where needed, grounded generation to
  normalize or enrich the existing record

**Multiple semantic fields** (e.g., `instruction`, `output`, `question`, `answer`):

- → use `FormatStrPromptedGenerator` for multi-field scoring or rewriting
- → use `GeneralFilter` for deterministic field-based rules
- → preserve the original semantic roles and construct improved derived fields
  when the source representation is not training-ready

**Path/URL-like fields**:

- → handle them according to the downstream target and the actual record semantics

## Examples

See `examples/` folder for complete workflows:

1. **`examples/basic_generate_and_filter.md`** — `PromptedGenerator` + `PromptedFilter` (simplest pattern)
2. **`examples/multifield_scoring.md`** — `FormatStrPromptedGenerator` with multi-field scoring
3. **`examples/multi_stage_pipeline.md`** — Multiple `PromptedGenerator` stages + `GeneralFilter`
4. **`examples/reasoning_math_pipeline.md`** — High-quality math reasoning workflow using native question screening/synthesis, difficulty and category evaluation, `ReasoningAnswerGenerator`, and format/length/ground-truth/ngram validation
5. **`examples/reasoning_general_pipeline.md`** — General or mixed-domain reasoning generation with reference-aware model judging and n-gram filtering
6. **`examples/reasoning_math_fusion_pipeline.md`** — Embedding-grounded sequential, parallel, and condition fusion for synthesizing harder math questions, followed by solvability evaluation
7. **`examples/reasoning_pretrain_pipeline.md`** — Math reasoning generation and filtering followed by explicit SFT-to-pretraining `text` conversion
8. **`examples/reasoning_diy_pipeline.md`** — Native reasoning operators with custom vertical-domain filter, synthesis, and answer prompt contracts
9. **`examples/reasoning_cpu_clean_pipeline.md`** — CPU-only format, mathematical ground-truth, and n-gram cleaning for existing reasoning answers
10. **`examples/agentic_rag_text_pipeline.md`** — Atomic and verified multi-hop QA over retrieved text, including grounding, shortcut, reasoning, and final-answer checks
11. **`examples/code_text_pipelines.md`** — Code-to-SFT and seed-to-code generation with quality scoring and sandbox execution, plus CPU code-text cleaning
12. **`examples/chemistry_smiles_text_pipeline.md`** — Chemistry-text SMILES extraction followed by molecular-equivalence evaluation
13. **`examples/function_call_text_pipeline.md`** — Scenario, task, function-schema, multi-turn tool-conversation synthesis and evaluation
14. **`examples/text2qa_pipeline.md`** — Diversity-aware text selection, QA generation, and multidimensional QA evaluation
15. **`examples/text2sql_text_pipelines.md`** — Executable Text-to-SQL generation, refinement, VectorSQL construction, CoT voting, and difficulty classification
16. **`examples/text_synthesis_and_quality_pipelines.md`** — Conversation, SFT, and PT text synthesis plus deterministic and learned quality-filtering chains
17. **`examples/text_benchmark_eval_pipelines.md`** — Direct and question-aware semantic or deterministic answer evaluation

These are strategy guidance, not templates to copy blindly. Generated code must follow standard pipeline structure.
