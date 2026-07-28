# DataMixer webpage L1 -> L2 -> L3 demo

This example runs through DataMixer's registered operator registry and YAML
pipeline runner. It does not overwrite L1 samples:

1. `webpage_to_pt` extracts readable text from raw HTML.
2. DataFlow's registered `WordNumberFilter` accepts PT-sized documents.
3. `domain_classify` classifies the cleaned PT content using the lake's persistent
   broad classes plus any domains already registered or observed in that lake.
4. The classified record is materialized as an independent L2 sample with
   parent/root lineage; its L3 descendants inherit the domain labels.
5. `pt_to_sft_qa` uses a DataMixer model-pool entry to create grounded QA.
6. `sft_validate` filters malformed output before independent L3 materialization.

Run it with an existing DataMixer warehouse that contains the selected model:

```bash
python3 examples/datamixer_l1_l3_pipeline/run_demo.py \
  --warehouse outputs/datamixer_l1_l3_pipeline/warehouse \
  --model-source-warehouse outputs/datamixer_dataflow_agent_verify/warehouse \
  --model deepseek-proxy \
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
