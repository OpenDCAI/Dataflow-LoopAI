"""Agent-orchestrated DataFlow pipeline planning for DataMixer.

This module turns the low-level DataFlow bridge into an agentic workflow:

1. export representative DataMixer rows to JSONL;
2. ask the LoopAI Codex SDK runner to use DataFlow-Skills style planning;
3. have the agent generate and trial-run a DataFlow pipeline;
4. optionally merge the trial output back into the warehouse by ``sample_id``.

The existing ``op run dataflow`` bridge remains the manual single-operator
adapter. This module is for downstream-task driven operator-chain planning.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loopai.schema.model_pool import StarterModelPool, chat_completions_url
from . import utils
from .codex import CodexError, provider_from_model, run_via_sdk
from .models import ModelPool
from .store import read_jsonl

RESERVED_FIELDS = {"content", "sample_id", "dataset_id", "cid", "created_at",
                   "version", "tags", "tags_json", "embedding"}


def dataflow_skills_root() -> Path | None:
    candidates = [
        os.environ.get("DATAFLOW_SKILLS_ROOT"),
        Path.cwd() / ".cache_codex" / "external" / "DataFlow-Skills",
        Path(__file__).resolve().parents[4] / ".cache_codex" / "external" / "DataFlow-Skills",
    ]
    for cand in candidates:
        if cand and (Path(cand) / "generating-dataflow-pipeline" / "SKILL.md").exists():
            return Path(cand)
    return None


def dataflow_pipeline_skill_text() -> str:
    root = dataflow_skills_root()
    if root:
        skill = root / "generating-dataflow-pipeline" / "SKILL.md"
        return skill.read_text(encoding="utf-8")
    return (
        "# DataFlow Pipeline Code Generator\n"
        "Given a target and JSONL sample file, inspect fields, choose an ordered "
        "DataFlow operator chain, emit an operator decision JSON, generate a "
        "standard FileStorage-based Python pipeline, run it on the provided "
        "JSONL, and report the processed JSONL path. Prefer specialized "
        "operators over generic prompted operators. Validate field dependencies "
        "before every step. Use sample_id as the join key and do not overwrite "
        "DataMixer content."
    )


def _project_root() -> Path | None:
    for path in Path(__file__).resolve().parents:
        if (path / "starter.yaml").exists():
            return path
    return None


def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions" if base else ""


def operator_llm_config_from_starter() -> dict[str, str]:
    """Return non-secret DataFlow LLM serving config plus the resolved key.

    The key is returned so the Codex runner can receive it via ``DF_API_KEY``;
    callers must not include it in prompts or JSON output.
    """
    root = _project_root()
    starter = root / "starter.yaml" if root else None
    if not starter or not starter.exists():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(starter.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    system = doc.get("system") or {}
    obtainer = (doc.get("default_states") or {}).get("obtainer") or {}
    model_value = system.get("model")
    has_explicit_pool = (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )
    if has_explicit_pool:
        pool = StarterModelPool(system)
        provider = pool.resolve_proxy_provider(obtainer.get("model_path") or None, tier=pool.default_tier)
        if provider is not None:
            return {
                "api_url": chat_completions_url(provider.base_url),
                "model_name": provider.model,
                "api_key_env": "DF_API_KEY",
                "api_key": provider.api_key,
            }
    base_url = system.get("starter_base_url") or obtainer.get("base_url") or ""
    model = (
        system.get("starter_model_name")
        or system.get("starter_model_path")
        or obtainer.get("model_path")
        or "gpt-4o-mini"
    )
    api_key = system.get("starter_api_key") or obtainer.get("api_key") or ""
    return {
        "api_url": _chat_completions_url(str(base_url)),
        "model_name": str(model),
        "api_key_env": "DF_API_KEY",
        "api_key": str(api_key or ""),
    }


def export_trial_jsonl(store, *, dataset: str | None, where: str | None,
                       field: str, out: Path, limit: int,
                       batch_size: int = 512) -> int:
    ds_id = store.catalog.resolve_dataset(dataset) if dataset else None
    written = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for batch in store.catalog.iter_query(where=where, dataset_id=ds_id,
                                              batch_size=batch_size):
            for row in batch:
                try:
                    content = store.get_content(row["cid"])
                except KeyError:
                    content = None
                rec = {
                    "sample_id": row["sample_id"],
                    field: utils.extract_text(content) if content else "",
                }
                for k, v in row.items():
                    if k in RESERVED_FIELDS or k == field or v is None:
                        continue
                    rec[k] = v
                for k, v in (row.get("tags") or {}).items():
                    if k not in rec and v is not None:
                        rec[k] = v
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
                if limit and written >= limit:
                    return written
    return written


def apply_processed_jsonl(store, *, file: str | Path, key: str = "sample_id",
                          field: str = "raw_content") -> dict[str, int]:
    updated = missing = skipped = seen = 0
    for rec in read_jsonl(file):
        if not isinstance(rec, dict):
            skipped += 1
            continue
        seen += 1
        sid = rec.get(key)
        if not sid:
            skipped += 1
            continue
        if not store.catalog.get_sample(sid):
            missing += 1
            continue
        upd = {k: v for k, v in rec.items()
               if k not in RESERVED_FIELDS and k != key and k != field}
        if upd:
            store.catalog.update_fields(sid, upd)
            updated += 1
    store.catalog.commit()
    return {"seen": seen, "updated": updated, "missing": missing, "skipped": skipped}


def build_dataflow_agent_prompt(
    *,
    target: str,
    trial_jsonl: Path,
    work_dir: Path,
    field: str,
    expected_outputs: str | None = None,
    operator_llm: dict[str, str] | None = None,
) -> str:
    skill_text = dataflow_pipeline_skill_text()
    expected = expected_outputs or (
        "Fields needed to satisfy the target; preserve sample_id for merge-back."
    )
    op_llm = operator_llm or {}
    op_llm_block = (
        "For DataFlow LLM-based operators, instantiate APILLMServing_request with:\n"
        f'- api_url="{op_llm.get("api_url", "")}"\n'
        f'- model_name="{op_llm.get("model_name", "gpt-4o-mini")}"\n'
        f'- key_name_of_api_key="{op_llm.get("api_key_env", "DF_API_KEY")}"\n'
        "Do not use the Codex planning model as the DataFlow operator model.\n"
    )
    return f"""You are the DataMixer DataFlow planning agent.

Use the DataFlow-Skills `generating-dataflow-pipeline` rules below. You may
delegate planning/code-review/trial-run work to sub-agents if this Codex SDK
surface supports them. If sub-agents are unavailable, do the same steps yourself.

Task:
- Downstream target: {target}
- Sample JSONL file: {trial_jsonl}
- Text/input field: {field}
- Expected outputs: {expected}
- Work directory: {work_dir}

{op_llm_block}

Required behavior:
1. Inspect the JSONL sample before choosing operators.
2. Produce an intermediate operator decision JSON with `ops`, `field_flow`, and
   `reason`.
3. Generate a standard DataFlow FileStorage pipeline Python file in the work
   directory. Do not write DataMixer catalog/blob files directly.
4. Trial-run the generated pipeline on the sample JSONL when dependencies allow.
5. Locate or create a processed JSONL that preserves `sample_id` and includes
   the generated/scored/filtered fields.
6. Return only one final JSON object with:
   - ok: boolean
   - mode: "trial_run" | "planned_only"
   - operator_decision: object
   - pipeline_path: string
   - processed_jsonl: string or null
   - trial_rows_in: integer
   - trial_rows_out: integer or null
   - stdout_tail: string
   - errors: list[string]
   - summary: string

Do not claim success if the trial did not run. If DataFlow dependencies are
missing, return planned_only with the exact blocker in errors.

--- DataFlow-Skills generating-dataflow-pipeline/SKILL.md ---
{skill_text}
"""


def run_dataflow_agent(
    store,
    *,
    target: str,
    model: str,
    dataset: str | None = None,
    where: str | None = None,
    field: str = "raw_content",
    expected_outputs: str | None = None,
    work_dir: str | Path | None = None,
    trial_rows: int = 20,
    timeout: int = 600,
    apply: bool = False,
) -> dict[str, Any]:
    if not model:
        raise CodexError("dataflow agent-run requires --model")
    if not target.strip():
        raise ValueError("dataflow agent-run requires --target")

    root = Path(store.root)
    run_dir = Path(work_dir) if work_dir else root / "runs" / "dataflow_agent"
    run_dir.mkdir(parents=True, exist_ok=True)
    trial_jsonl = run_dir / "trial_input.jsonl"
    exported = export_trial_jsonl(
        store, dataset=dataset, where=where, field=field,
        out=trial_jsonl, limit=trial_rows,
    )
    if exported <= 0:
        raise ValueError("no rows matched dataset/filter for DataFlow trial")

    spec = ModelPool(store.root).get(model)
    prov = provider_from_model(spec)
    prompt = build_dataflow_agent_prompt(
        target=target,
        trial_jsonl=trial_jsonl.resolve(),
        work_dir=run_dir.resolve(),
        field=field,
        expected_outputs=expected_outputs,
        operator_llm=operator_llm_config_from_starter(),
    )
    operator_llm = operator_llm_config_from_starter()
    if operator_llm.get("api_key"):
        os.environ[operator_llm.get("api_key_env", "DF_API_KEY")] = operator_llm["api_key"]
    agent_result = run_via_sdk(prompt, prov, cwd=str(root), timeout=timeout)

    processed = agent_result.get("processed_jsonl")
    merge = None
    if apply:
        if not processed:
            raise CodexError("agent did not return processed_jsonl; cannot apply")
        processed_path = Path(str(processed))
        if not processed_path.is_absolute():
            processed_path = run_dir / processed_path
        if not processed_path.exists():
            raise FileNotFoundError(f"processed_jsonl not found: {processed_path}")
        merge = apply_processed_jsonl(store, file=processed_path, field=field)

    return {
        "engine": "codex",
        "task": "dataflow_agent_run",
        "model": model,
        "target": target,
        "dataset": dataset,
        "filter": where,
        "field": field,
        "work_dir": str(run_dir.resolve()),
        "trial_jsonl": str(trial_jsonl.resolve()),
        "trial_rows_exported": exported,
        "applied": bool(apply),
        "merge": merge,
        "agent_result": agent_result,
    }
