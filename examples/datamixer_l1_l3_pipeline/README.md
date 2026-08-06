# DataMixer webpage L1 -> L2 -> L3 demo

This example runs through DataMixer's registered operator registry and YAML
pipeline runner. It does not overwrite L1 samples:

1. `webpage_to_pt` extracts readable text from raw HTML.
2. DataFlow's registered `WordNumberFilter` accepts PT-sized documents.
3. `domain_classify` classifies each cleaned PT record with the lake's persistent
   broad classes plus any domains already registered or observed in that lake.
4. `topic_quality_filter` admits only LLM evidence with focus relevance,
   confidence, and at least two grounded semantic signal categories; every
   rejection is retained in the streaming quality report. The focus keywords
   come from the campaign (`--focus-keywords`), so no vertical is hard-wired.
   No source whitelist is used.
5. The accepted record is materialized as an independent L2 sample with
   parent/root lineage; its L3 descendants inherit the domain labels.
6. `pt_to_sft_qa` uses the Qwen model-pool entry to create grounded QA.
7. `sft_validate` filters malformed output before independent L3 materialization.

Run it with an existing DataMixer warehouse that contains the selected model:

```bash
python3 examples/datamixer_l1_l3_pipeline/run_demo.py \
  --warehouse outputs/datamixer_l1_l3_pipeline/warehouse \
  --model-source-warehouse outputs/datamixer_dataflow_agent_verify/warehouse \
  --model qwen3-14b-fp8 \
  --mineru-gpu 0 \
  --report outputs/datamixer_l1_l3_pipeline/run_report.json
```

Use `--mineru-gpu` to select a GPU with enough free memory. The example uses
the Transformers backend by default because it does not reserve a full vLLM KV
cache alongside other jobs; `--mineru-backend vllm` is available on an idle GPU.

The ten raw HTML fixtures and their retrieval/license manifest are under
`source_pages/`. The pipeline creates `webpage_demo_l1`, `webpage_demo_l2_pt`,
and `webpage_demo_l3_sft` in the target warehouse.

The LLM classifier needs no hand-maintained label list. Inspect or extend its
lake-local vocabulary with:

```bash
python3 -m loopai.agents.Obtainer.datamixer --root outputs/datamixer_l1_l3_pipeline/warehouse \
  domain list
python3 -m loopai.agents.Obtainer.datamixer --root outputs/datamixer_l1_l3_pipeline/warehouse \
  domain add text2sql robotics
```

`domain list` also discovers non-empty `samples.domain` values from pre-existing
data, so an established lake's own labels become eligible on the next run.

## Code pipeline contract

`code_pipeline.yaml` uses the persistent MinerU-HTML service to preserve HTML
code blocks, then sends every record through six registered OpenDCAI DataFlow
Code filters before Qwen performs domain classification and SFT generation.
The native package name, version, operator name, and kind are retained in each
accepted row's `native_dataflow_operators` audit list.

Code is not required to be Qwen's first domain label. The target label is
accepted in positions 1 through 3 (`require_primary_label: false` and
`max_target_label_rank: 3`) when confidence and executable-code evidence also
pass. The classifier output does not have to reproduce the source page's
pre-existing `domain` field.

The Code SFT generator may combine, repair, complete, or otherwise rewrite the
extracted source blocks. It is not required to copy one source block verbatim.
The final answer must still use a language represented by the source blocks and
must pass the language-specific validator. Python output is parsed and rejected
when it has obvious undefined globals; SQL output must be a complete statement.

## Text2SQL pipeline contract

`text2sql_pipeline.yaml` keeps a stricter grounding contract because generated
queries are executed against a generated SQLite schema. Qwen must extract a
source DDL/query pair, `text2sql_sqlite_prepare` materializes an isolated
database, and the native `SQLExecutionFilter`, `Text2SQLPromptGenerator`, and
`SQLComponentClassifier` operators execute and annotate the result before L3
materialization. Unlike the Code generator, the Text2SQL generator intentionally
requires the DDL and query to be source snippets so it cannot invent tables or
columns.
