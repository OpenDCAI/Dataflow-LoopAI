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
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

from loopai.schema.model_pool import StarterModelPool, chat_completions_url
from . import utils
from .codex import CodexError, provider_from_model, run_via_sdk
from .models import ModelPool
from .store import read_jsonl

RESERVED_FIELDS = {"content", "sample_id", "dataset_id", "cid", "created_at",
                   "version", "tags", "tags_json", "embedding"}

# Hard-coded per-turn budget for the DataFlow agent Codex session. One hour so
# the agent can finish the full (not just trial) processing on large datasets.
DATAFLOW_AGENT_TIMEOUT = 3600


def _write_dataflow_status(path: Path, **updates: Any) -> dict[str, Any]:
    current: dict[str, Any] = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    current.update(updates)
    current["updated_at"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return current


def _operator_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("operator") or value.get("type") or "")
    return str(value or "")


def parse_llm_scalar_score(value: Any, *, minimum: int = 1, maximum: int = 5) -> int:
    """Parse one final integer score without reading chain-of-thought numbers."""
    if value is None:
        raise ValueError("LLM score response is missing")
    text = str(value).strip()
    answer_blocks = re.findall(r"<answer>(.*?)</answer>", text, flags=re.DOTALL | re.IGNORECASE)
    if answer_blocks:
        if len(answer_blocks) != 1:
            raise ValueError("LLM score response has multiple <answer> blocks")
        text = answer_blocks[0].strip()
    else:
        text = re.sub(
            r"<think>.*?</think>", "", text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
    match = re.fullmatch(r"([+-]?\d+)", text)
    if not match:
        raise ValueError("LLM score response must end in one integer")
    score = int(match.group(1))
    if not minimum <= score <= maximum:
        raise ValueError(
            f"LLM score {score} is outside the allowed range {minimum}-{maximum}"
        )
    return score


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


def dataflow_codex_home() -> Path:
    """Dedicated Codex home for the DataFlow agent (its own AGENTS.md)."""
    root = _project_root() or Path.cwd()
    return Path(root) / "codex_home_dataflow"


DATAFLOW_AGENTS_MD = """# LoopAI DataMixer DataFlow Agent

你是 DataMixer 的 **DataFlow 后处理 agent**（dataflow agent），负责把 L1 -> L2
-> L3 链路上的数据通过 DataFlow operator 链处理成 **L4**（质量过滤、去重、
规范化、安全、SFT 有效性），L4 是生产导出的唯一数据源。

## 运行环境清单

以下运行环境信息由系统在每次启动会话时自动探测并注入，直接按清单使用，
不要再去全仓探测运行环境：

<!-- runtime_environment_manifest -->

## 核心工作流：试跑成功 -> 交付 pipeline -> 上层跑 chunk 全量 -> L4

1. 先用样例 JSONL 检查字段，规划 operator 链，生成标准 DataFlow FileStorage
   pipeline，并完成试跑。
2. **试跑成功即交付，全量由上层执行。** 你不需要（也不允许）在同一会话内自己
   启动全量处理或写 `full_processed.jsonl`：
   - 全量输入已经导出在 `full_input.jsonl`（与 `trial_input.jsonl` 同一目录），
     供**上层 starter** 使用。当任务带有 recipe / mix_plan 时，它是**按下游
     出湖桶目标 1.5 倍缓冲抽样**的（每桶 `ceil(bucket_target * 1.5)` 行，固定
     seed 可复现），**不是全湖导出**；
   - 交付物 = 试跑通过的 `pipeline.py` + 试跑输出 `trial_processed.jsonl`；
   - pipeline 必须遵循 `DATAFLOW_INPUT` / `DATAFLOW_CACHE_DIR` /
     `DATAFLOW_PREFIX` 环境变量约定，以便上层脚手架
     `loopai.agents.Obtainer.datamixer.dataflow_chunked_runner` 按 1 万行一个
     chunk 逐 chunk 启动同一 pipeline 并保序合并（`--chunk-size 10000`）；
   - 试跑与全量使用同一 pipeline，因此试跑通过后**不得再改阈值、prompt 或
     seed**；全量行数、sample_id 唯一性、原始字段保真由上层脚手架校验。
3. 返回最终 JSON 时带上 `pipeline_path`、`processed_jsonl`（试跑输出）、
   `trial_rows_in`、`trial_rows_out`；`mode` 必须是 `trial_run`（交付成功）或
   `planned_only`（依赖缺失等阻塞）。`full_*` 字段返回 null 即可，全量执行
   不在你的职责范围。
4. L4 规模必须达标（整体与每个 bucket 都 >= 配比目标的 1.5x）才允许外层启动
   `sft-export-agent` 出湖；规模不达标前禁止出湖。这是上层的出湖门，你在
   交付 summary 里说明桶规模与预期冗余即可。

## 硬约束

- 数据湖必须已 init/load，先 `dm lake load/unbind` 管理指针，不要新建湖，
  不要跨 task 复用旧绑定。
- **质量评估必须使用 DataFlow 的 LLM 评估算子**（如 `PromptedEvaluator` /
  `PromptedFilter` 等 LLM 打分/过滤算子），不得因耗时或成本而退化成纯启发式
  规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许
  规则算子兜底，且必须在 summary 里说明具体原因。**禁止 LLM 逐条重写或改写
  文本内容**，LLM 只做打分与筛选。

- `sample_id` 是唯一 join key，必须原样保留；每个保留行必须逐字保留所有
  原始字段与值，只允许新增算子字段；输出顺序必须与输入一致。
- DataFlow 的 LLM 算子必须使用清单里的 DataFlow serving 配置
  （`DF_API_KEY`），不得用 Codex 规划模型当算子模型。
- LLM 打分解析只取最终整数答案（去掉完整 `<think>` 块，或读取显式
  `<answer>` 块），禁止从 CoT 里取第一个数字、禁止 clamp、禁止静默默认
  失败为某个分数；优先 import `parse_llm_scalar_score` 并先用合成样例
  测通再接受输出。
- 最终返回必须是单个 JSON 对象，不要用 Markdown 包裹，不要缺字段。
- 全量执行不在你的职责内：不要自己后台起全量进程。你只需在交付 summary 里
  说明 full input 规模、算子链类型（尤其是否含 LLM 逐条打分）与预期运行时长，
  供上层判断；LLM 质量评估全量耗时数小时到十几小时属正常，不应据此改用规则
  算子。
"""


def ensure_dataflow_codex_home() -> Path:
    """Create the DataFlow agent Codex home with its dedicated AGENTS.md."""
    home = dataflow_codex_home()
    home.mkdir(parents=True, exist_ok=True)
    agents_path = home / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(DATAFLOW_AGENTS_MD, encoding="utf-8")
    return home


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


_DATASET_ID_EQ_RE = re.compile(r"dataset_id\s*=\s*(['\"])([^'\"]+)\1", re.I)
_DATASET_ID_IN_RE = re.compile(r"dataset_id\s+IN\s*\(([^)]*)\)", re.I)
_DOMAIN_EQ_RE = re.compile(r"domain\s*=\s*(['\"])([^'\"]+)\1", re.I)


def _simplify_bucket_filter(filter_expr: str) -> str | None:
    """Reduce a recipe bucket filter to the dataset/domain scope only.

    Recipe bucket filters reference L4 fields produced by the DataFlow
    post-processing itself (``quality_score``, ``json_extract(tags_json,
    '$.pii_flag')``, ...). Those columns/values do not exist yet at export
    time, so the full predicate would match nothing. For the 1.5x buffer
    export we keep only the dataset_id (and domain) scope.
    """
    ids: set[str] = set()
    for match in _DATASET_ID_EQ_RE.finditer(filter_expr or ""):
        ids.add(match.group(2))
    for match in _DATASET_ID_IN_RE.finditer(filter_expr or ""):
        for raw in match.group(1).split(","):
            token = raw.strip().strip("'\"").strip()
            if token:
                ids.add(token)
    if not ids:
        return None
    where = "dataset_id IN (" + ",".join(f"'{i}'" for i in sorted(ids)) + ")"
    domain_match = _DOMAIN_EQ_RE.search(filter_expr or "")
    if domain_match:
        where += f" AND domain='{domain_match.group(2)}'"
    return where


def _write_jsonl_row(fh, row: dict[str, Any], field: str) -> None:
    content = row.get("_content")
    rec = {"sample_id": row["sample_id"],
           field: utils.extract_text(content) if content else ""}
    for k, v in row.items():
        if k in RESERVED_FIELDS or k == field or k == "_content" or v is None:
            continue
        rec[k] = v
    for k, v in (row.get("tags") or {}).items():
        if k not in rec and v is not None:
            rec[k] = v
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def bucket_export_plan_from_recipe(
    recipe_path: str | Path, ratio: float = 1.5
) -> list[dict[str, Any]]:
    """Turn an export recipe's buckets into a 1.5x buffer export plan."""
    from .recipe import load_recipe
    recipe = load_recipe(str(recipe_path))
    total = recipe.total_samples or recipe.total_tokens or 0
    plan: list[dict[str, Any]] = []
    for bucket in recipe.buckets:
        target = int(round(bucket.weight * total))
        where = _simplify_bucket_filter(bucket.filter)
        if not where:
            raise ValueError(
                f"bucket {bucket.name!r} filter has no dataset_id scope; "
                "cannot derive the 1.5x export for it"
            )
        plan.append({
            "name": bucket.name,
            "target_records": target,
            "export_rows": int(math.ceil(target * ratio)),
            "where": where,
        })
    return plan


def bucket_export_plan_from_mix_plan(
    mix_plan_path: str | Path, ratio: float = 1.5
) -> list[dict[str, Any]]:
    """Turn an sft-export mix_plan.json's buckets into a 1.5x export plan."""
    doc = json.loads(Path(mix_plan_path).read_text(encoding="utf-8"))
    domain = str(doc.get("domain") or "").strip()
    plan: list[dict[str, Any]] = []
    for bucket in doc.get("buckets") or []:
        target = int(bucket.get("target_records") or 0)
        dataset_id = str(bucket.get("dataset") or "").strip()
        if not dataset_id:
            continue
        where = f"dataset_id='{dataset_id}'"
        if domain:
            where += f" AND domain='{domain}'"
        plan.append({
            "name": str(bucket.get("name") or dataset_id),
            "target_records": target,
            "export_rows": int(math.ceil(target * ratio)),
            "where": where,
        })
    return plan


def export_bucketed_jsonl(
    store,
    *,
    buckets: list[dict[str, Any]],
    field: str,
    out: str | Path,
    ratio: float = 1.5,
    seed: int = 42,
) -> dict[str, Any]:
    """Export a per-bucket 1.5x buffer JSONL (not the whole lake).

    Each bucket contributes up to ``ceil(target_records * ratio)`` rows,
    randomly sampled with a fixed seed (repeatable). If a bucket has fewer
    available rows than the buffer target, all of its rows are exported.
    sample_ids are unique across buckets (first bucket wins).
    """
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    exported: dict[str, int] = {}
    total = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for bucket in buckets:
            where = bucket.get("where") or ""
            name = str(bucket.get("name") or "bucket")
            limit = int(bucket.get("export_rows") or 0)
            rows = store.catalog.query(where=where) if where else []
            rng = random.Random(f"{seed}:{name}")
            rng.shuffle(rows)
            count = 0
            for row in rows:
                if limit and count >= limit:
                    break
                if row["sample_id"] in seen:
                    continue
                seen.add(row["sample_id"])
                try:
                    content = store.get_content(row["cid"]) if row.get("cid") else None
                except KeyError:
                    content = None
                row = dict(row)
                row["_content"] = content
                _write_jsonl_row(fh, row, field)
                count += 1
                total += 1
            exported[name] = count
    return {"total": total, "buckets": exported}


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
    full_jsonl: Path | None = None,
    bucket_plan: list[dict[str, Any]] | None = None,
) -> str:
    skill_text = dataflow_pipeline_skill_text()
    skill_root = dataflow_skills_root()
    skill_source = str(skill_root.resolve()) if skill_root else "embedded fallback"
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
    bucket_block = ""
    if bucket_plan:
        lines = "\n".join(
            f"  - {b.get('name')}: target={b.get('target_records')}, "
            f"exported={b.get('export_rows')}, scope={b.get('where')}"
            for b in bucket_plan
        )
        bucket_block = (
            "The full input is a **1.5x bucket buffer export**, NOT the whole "
            "lake: it was sampled per downstream export bucket to "
            "ceil(bucket_target * 1.5) rows so post-processing keeps enough "
            "redundancy for the final recipe mix. The upper-layer starter will "
            "run it with the chunked scaffold; you do not touch it.\n"
            f"Bucket export plan:\n{lines}\n"
        )
    deliver_block = (
        "After the trial succeeds you DELIVER the pipeline - you do NOT launch "
        "the full processing yourself:\n"
        f"- Full input JSONL (already exported for the upper layer): {full_jsonl}\n"
        f"- Your deliverables: {work_dir}/pipeline.py + trial processed JSONL.\n"
        f"{bucket_block}"
        "- The upper-layer starter will run the full input through the outer "
        "scaffold `python -m loopai.agents.Obtainer.datamixer."
        "dataflow_chunked_runner --input <full input> --pipeline <your "
        "pipeline.py> --output <full output> --chunk-size 10000`, which slices "
        "the input into 10k-row chunks, launches this same pipeline per chunk "
        "(DATAFLOW_INPUT / DATAFLOW_CACHE_DIR / DATAFLOW_PREFIX), merges "
        "outputs in input order, and validates byte-for-byte field "
        "preservation.\n"
        "- Your generated pipeline MUST follow that env-var convention and keep "
        "the operator chain/config identical to the trial. Never rewrite row "
        "text; score/filter only.\n"
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
- DataFlow-Skills root: {skill_source}

{op_llm_block}

Required behavior:
1. Inspect the JSONL sample before choosing operators.
2. Produce an intermediate operator decision JSON with `ops`, `field_flow`, and
   `reason`.
3. Generate a standard DataFlow FileStorage pipeline Python file in the work
   directory. Do not write DataMixer catalog/blob files directly.
4. Trial-run the generated pipeline on the sample JSONL when dependencies allow.
5. Locate or create the trial processed JSONL. Every retained row must preserve
   every original input field and value exactly, including timestamp strings and
   nested metadata; only new operator fields may be added. DataFrame type
   inference is not an excuse for rewriting input values. Preserve input order
   for retained rows and keep `sample_id` as the join key.
6. {deliver_block}
7. For LLM scalar scores, keep the raw model verdict auditable while developing
   the pipeline. Parse only the final answer after removing complete `<think>`
   blocks (or the content of an explicit `<answer>` block). Never parse the first
   number from chain-of-thought, clamp a value, or silently default a parse
   failure to a score. A missing, ambiguous, or out-of-range score must fail the
   trial. Prefer importing `parse_llm_scalar_score` from
   `loopai.agents.Obtainer.datamixer.dataflow_agent`, and test it against a
   synthetic response whose reasoning contains other 1-5 numbers before
   accepting the output.
8. Compute every count, range, and example mentioned in the final summary from
   the processed files after they are written. Do not rely on remembered model
   output.
9. Return only one final JSON object with:
   - ok: boolean
   - mode: "trial_run" | "planned_only"   (trial_run = pipeline delivered)
   - operator_decision: object
   - pipeline_path: string
   - processed_jsonl: string or null (trial output)
   - trial_rows_in: integer
   - trial_rows_out: integer or null
   - full_processed_jsonl: null
   - full_rows_in: null
   - full_rows_out: null
   - stdout_tail: string
   - errors: list[string]
   - summary: string

Do not claim success if the trial did not run. A successful final result means
the trial ran and the pipeline was delivered for the upper layer to execute on
the full input; you must NOT launch the full processing yourself. If DataFlow
dependencies are missing, return planned_only with ok=false and the exact
blocker in errors.

--- DataFlow-Skills generating-dataflow-pipeline/SKILL.md ---
{skill_text}
"""


def _resolve_agent_artifact(run_dir: Path, value: Any, *, name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise CodexError(f"dataflow agent result missing {name}")
    path = Path(raw)
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    if not path.is_file():
        raise CodexError(f"dataflow agent {name} not found: {path}")
    return path


def _audit_processed(
    processed_path: Path,
    input_path: Path,
    reported_out: int | None,
    *,
    label: str,
) -> dict[str, Any]:
    """Fail closed unless every processed row preserves its input row exactly."""
    input_rows: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(read_jsonl(input_path), 1):
        if not isinstance(row, dict) or not row.get("sample_id"):
            raise CodexError(f"{label} input row {row_number} is missing sample_id")
        sample_id = str(row["sample_id"])
        if sample_id in input_rows:
            raise CodexError(f"{label} input contains duplicate sample_id values")
        input_rows[sample_id] = row

    output_ids: list[str] = []
    added_fields: set[str] = set()
    for row_number, row in enumerate(read_jsonl(processed_path), 1):
        if not isinstance(row, dict) or not row.get("sample_id"):
            raise CodexError(
                f"{label} row {row_number} does not preserve sample_id"
            )
        sample_id = str(row["sample_id"])
        output_ids.append(sample_id)
        source = input_rows.get(sample_id)
        if source is None:
            continue
        missing_fields = sorted(set(source) - set(row))
        changed_fields = sorted(
            key for key, value in source.items()
            if key in row and row[key] != value
        )
        if missing_fields or changed_fields:
            details = []
            if missing_fields:
                details.append("missing " + ", ".join(missing_fields[:5]))
            if changed_fields:
                details.append("changed " + ", ".join(changed_fields[:5]))
            raise CodexError(
                f"{label} row {row_number} ({sample_id}) did not preserve "
                "original input fields exactly: " + "; ".join(details)
            )
        added_fields.update(set(row) - set(source))
    if reported_out is not None and len(output_ids) != reported_out:
        raise CodexError(
            f"{label} row count mismatch: found {len(output_ids)}, "
            f"reported {reported_out}"
        )
    if len(output_ids) != len(set(output_ids)):
        raise CodexError(f"{label} contains duplicate sample_id values")
    unknown = sorted(set(output_ids) - set(input_rows))
    if unknown:
        raise CodexError(
            f"{label} contains sample_id values outside the input: "
            + ", ".join(unknown[:5])
        )
    expected_order = [
        sample_id for sample_id in input_rows if sample_id in set(output_ids)
    ]
    if output_ids != expected_order:
        raise CodexError(f"{label} did not preserve input order for retained rows")
    return {
        "input_rows": len(input_rows),
        "output_rows": len(output_ids),
        "retained_input_rows": len(output_ids),
        "dropped_input_rows": len(input_rows) - len(output_ids),
        "unique_output_sample_ids": len(set(output_ids)),
        "original_fields_preserved": True,
        "added_fields": sorted(added_fields),
    }


def validate_dataflow_agent_result(
    result: Any,
    *,
    run_dir: Path,
    trial_jsonl: Path,
    trial_rows: int,
    full_jsonl: Path | None = None,
) -> dict[str, Any]:
    """Fail closed unless the agent returned verifiable pipeline artifacts."""
    if not isinstance(result, dict):
        raise CodexError("dataflow agent did not return a JSON object")

    required = {
        "ok", "mode", "operator_decision", "pipeline_path",
        "processed_jsonl", "trial_rows_in", "trial_rows_out",
        "full_processed_jsonl", "full_rows_in", "full_rows_out",
        "stdout_tail", "errors", "summary",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise CodexError(
            "dataflow agent returned an incomplete final result; missing: "
            + ", ".join(missing)
        )
    if not isinstance(result["ok"], bool):
        raise CodexError("dataflow agent result ok must be boolean")
    if result["mode"] not in {"full_run", "trial_run", "planned_only"}:
        raise CodexError(
            "dataflow agent result mode must be trial_run, planned_only, or full_run"
        )
    if not isinstance(result["errors"], list) or not all(
        isinstance(item, str) for item in result["errors"]
    ):
        raise CodexError("dataflow agent result errors must be a list of strings")
    if not isinstance(result["summary"], str) or not result["summary"].strip():
        raise CodexError("dataflow agent result summary must be non-empty")

    decision = result["operator_decision"]
    if not isinstance(decision, dict):
        raise CodexError("dataflow agent operator_decision must be an object")
    if not isinstance(decision.get("ops"), list) or not decision["ops"]:
        raise CodexError("dataflow agent operator_decision.ops must be non-empty")
    for key in ("field_flow", "reason"):
        if not isinstance(decision.get(key), str) or not decision[key].strip():
            raise CodexError(f"dataflow agent operator_decision.{key} must be non-empty")

    if result["trial_rows_in"] != trial_rows:
        raise CodexError(
            "dataflow agent trial_rows_in mismatch: "
            f"reported {result['trial_rows_in']!r}, exported {trial_rows}"
        )
    pipeline_path = _resolve_agent_artifact(
        run_dir, result["pipeline_path"], name="pipeline_path"
    )
    result["pipeline_path"] = str(pipeline_path)

    if result["mode"] == "planned_only":
        if result["ok"] or not result["errors"]:
            raise CodexError(
                "planned_only result must set ok=false and report exact blockers"
            )
        for key in (
            "processed_jsonl", "trial_rows_out",
            "full_processed_jsonl", "full_rows_in", "full_rows_out",
        ):
            if result.get(key) is not None:
                raise CodexError(f"planned_only result cannot claim {key}")
        return result

    if not result["ok"] or result["errors"]:
        raise CodexError("dataflow agent result must set ok=true with no errors")
    if not isinstance(result["trial_rows_out"], int) or result["trial_rows_out"] < 0:
        raise CodexError("dataflow agent trial_rows_out must be a non-negative integer")

    processed_path = _resolve_agent_artifact(
        run_dir, result["processed_jsonl"], name="processed_jsonl"
    )
    trial_audit = _audit_processed(
        processed_path,
        trial_jsonl,
        result["trial_rows_out"],
        label="trial processed_jsonl",
    )
    result["processed_jsonl"] = str(processed_path)
    result["output_audit"] = trial_audit

    if result["mode"] == "trial_run":
        # The DataFlow agent only delivers the trial-verified pipeline; the
        # upper-layer starter runs the full input through the chunked runner.
        result["deliverable"] = {
            "mode": "trial_run",
            "pipeline_path": result["pipeline_path"],
            "processed_jsonl": result["processed_jsonl"],
            "trial_rows_in": result["trial_rows_in"],
            "trial_rows_out": result["trial_rows_out"],
            "full_jsonl": str(full_jsonl) if full_jsonl else None,
        }
        return result

    if full_jsonl is None or not full_jsonl.is_file():
        raise CodexError(f"full_run mode requires a full input JSONL: {full_jsonl}")
    if not isinstance(result["full_rows_in"], int) or result["full_rows_in"] < 0:
        raise CodexError("dataflow agent full_rows_in must be a non-negative integer")
    if not isinstance(result["full_rows_out"], int) or result["full_rows_out"] < 0:
        raise CodexError("dataflow agent full_rows_out must be a non-negative integer")
    full_processed_path = _resolve_agent_artifact(
        run_dir, result["full_processed_jsonl"], name="full_processed_jsonl"
    )
    full_audit = _audit_processed(
        full_processed_path,
        full_jsonl,
        result["full_rows_out"],
        label="full processed_jsonl",
    )
    if result["full_rows_in"] != full_audit["input_rows"]:
        raise CodexError(
            "dataflow agent full_rows_in mismatch: "
            f"reported {result['full_rows_in']!r}, exported {full_audit['input_rows']}"
        )
    result["full_processed_jsonl"] = str(full_processed_path)
    result["full_output_audit"] = full_audit
    return result


def _continuation_prompt(validation_error: str, *, run_dir: Path) -> str:
    return f"""Your previous turn ended before returning a valid final result.

Validation error: {validation_error}
Work directory: {run_dir.resolve()}

Continue from the work already completed in this same thread. Do not search the
filesystem for skills, reread broad documentation, or repeat sample inspection.
Finish the operator decision, standard DataFlow pipeline, and trial run now.
Deliver the trial-verified pipeline (mode=trial_run); do NOT launch the full
processing yourself - the upper layer runs it through the chunked scaffold.
Then return only the final JSON object required by the original task. Verify
all artifact paths exist, trial row counts match the files, and every processed
row preserves a unique input sample_id plus every original input field and
value. For LLM scalar scoring, strip complete think blocks and parse the final
answer; never silently replace a parse failure with a score.
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
    apply: bool = False,
    recipe_path: str | Path | None = None,
    mix_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    if not model:
        raise CodexError("dataflow agent-run requires --model")
    if not target.strip():
        raise ValueError("dataflow agent-run requires --target")

    root = Path(store.root)
    run_dir = Path(work_dir) if work_dir else root / "runs" / "dataflow_agent"
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "status.json"
    base_status = {
        "state": "running",
        "phase": "exporting",
        "target": target,
        "dataset": dataset or "",
        "filter": where or "",
        "input_rows": 0,
        "selected_rows": 0,
        "output_rows": 0,
        "dropped_rows": 0,
        "failed_rows": 0,
        "applied_rows": 0,
        "full_input_rows": 0,
        "full_output_rows": 0,
        "current_operator": "",
        "attempt": 0,
        "feedback": "正在导出 DataFlowAgent 试运行输入",
        "error": "",
    }
    _write_dataflow_status(status_path, **base_status)
    trial_jsonl = run_dir / "trial_input.jsonl"
    try:
        exported = export_trial_jsonl(
            store, dataset=dataset, where=where, field=field,
            out=trial_jsonl, limit=trial_rows,
        )
        if exported <= 0:
            raise ValueError("no rows matched dataset/filter for DataFlow trial")
        _write_dataflow_status(
            status_path,
            phase="planning",
            input_rows=exported,
            selected_rows=exported,
            feedback="正在规划并试运行 DataFlow operator 链",
        )
        full_jsonl = run_dir / "full_input.jsonl"
        bucket_plan: list[dict[str, Any]] | None = None
        if recipe_path or mix_plan_path:
            if recipe_path:
                bucket_plan = bucket_export_plan_from_recipe(recipe_path)
            else:
                bucket_plan = bucket_export_plan_from_mix_plan(mix_plan_path)
            if not bucket_plan:
                raise ValueError("recipe/mix-plan defines no exportable buckets")
            full_exported = export_bucketed_jsonl(
                store, buckets=bucket_plan, field=field, out=full_jsonl,
            )["total"]
            scope = ", ".join(f"{b['name']}={b['export_rows']}" for b in bucket_plan)
        else:
            full_exported = export_trial_jsonl(
                store, dataset=dataset, where=where, field=field,
                out=full_jsonl, limit=0,
            )
            scope = "whole-lake export (no --recipe/--mix-plan)"
        if full_exported <= 0:
            raise ValueError("no rows matched dataset/filter for DataFlow full run")
        _write_dataflow_status(
            status_path,
            phase="planning",
            input_rows=exported,
            full_input_rows=full_exported,
            feedback=(
                "正在规划 DataFlow operator 链（试跑成功后自动全量处理）"
                if bucket_plan is None
                else f"按桶 1.5x 缓冲导出完成（{scope}），等待试跑后全量处理"
            ),
        )

        spec = ModelPool(store.root).get(model)
        prov = provider_from_model(spec)
        prompt = build_dataflow_agent_prompt(
            target=target,
            trial_jsonl=trial_jsonl.resolve(),
            work_dir=run_dir.resolve(),
            field=field,
            expected_outputs=expected_outputs,
            operator_llm=operator_llm_config_from_starter(),
            full_jsonl=full_jsonl.resolve() if full_jsonl else None,
            bucket_plan=bucket_plan,
        )
        operator_llm = operator_llm_config_from_starter()
        if operator_llm.get("api_key"):
            os.environ[operator_llm.get("api_key_env", "DF_API_KEY")] = operator_llm["api_key"]
        agent_result: dict[str, Any] | None = None
        validation_error: CodexError | None = None
        current_prompt = prompt
        thread_id: str | None = None
        df_home = ensure_dataflow_codex_home()
        previous_codex_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(df_home)
        try:
            for attempt in range(3):
                _write_dataflow_status(
                    status_path,
                    phase="running",
                    attempt=attempt + 1,
                    feedback=f"DataFlowAgent 第 {attempt + 1} 次规划/试运行（交付 pipeline，全量由上层执行）",
                )
                agent_result = run_via_sdk(
                    current_prompt,
                    prov,
                    cwd=str(root),
                    timeout=DATAFLOW_AGENT_TIMEOUT,
                    thread_id=thread_id,
                )
                try:
                    agent_result = validate_dataflow_agent_result(
                        agent_result,
                        run_dir=run_dir.resolve(),
                        trial_jsonl=trial_jsonl.resolve(),
                        trial_rows=exported,
                        full_jsonl=full_jsonl.resolve() if full_jsonl else None,
                    )
                    validation_error = None
                    break
                except CodexError as exc:
                    validation_error = exc
                    thread_id = str(agent_result.get("thread_id") or "").strip() or None
                    _write_dataflow_status(
                        status_path,
                        phase="validating",
                        error=str(exc),
                        feedback="结果校验失败，准备继续当前线程",
                    )
                    if attempt == 2 or not thread_id:
                        break
                    current_prompt = _continuation_prompt(str(exc), run_dir=run_dir)
        finally:
            if previous_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = previous_codex_home
        if validation_error is not None or agent_result is None:
            raise validation_error or CodexError("dataflow agent returned no result")

        audit = agent_result.get("output_audit") or {}
        full_audit = agent_result.get("full_output_audit") or {}
        operators = (agent_result.get("operator_decision") or {}).get("ops") or []
        current_operator = _operator_name(operators[-1]) if operators else ""
        output_rows = int(
            full_audit.get("output_rows")
            or audit.get("output_rows")
            or agent_result.get("full_rows_out")
            or agent_result.get("trial_rows_out")
            or 0
        )
        dropped_rows = int(full_audit.get("dropped_input_rows") or audit.get("dropped_input_rows") or 0)
        failed_rows = len(agent_result.get("errors") or [])
        delivering = agent_result.get("mode") == "trial_run"
        _write_dataflow_status(
            status_path,
            phase="delivering" if delivering else ("applying" if apply else "finalizing"),
            current_operator=current_operator,
            output_rows=output_rows,
            dropped_rows=dropped_rows,
            failed_rows=failed_rows,
            full_output_rows=output_rows,
            error="",
            feedback=(
                "试跑成功，已交付 pipeline；全量由上层用 chunk 脚手架执行"
                if delivering
                else ("全量处理完成，正在写回数据湖" if apply else "全量处理完成，正在生成结果")
            ),
        )

        processed = (
            agent_result.get("full_processed_jsonl")
            if agent_result.get("mode") == "full_run"
            else agent_result.get("processed_jsonl")
        )
        merge = None
        if apply:
            if agent_result.get("mode") == "trial_run":
                # Merge responsibility moved upstream: the upper layer runs the
                # chunked scaffold over full_input.jsonl, then applies.
                merge = None
            else:
                if not processed:
                    raise CodexError("agent did not return processed_jsonl; cannot apply")
                processed_path = Path(str(processed))
                if not processed_path.is_absolute():
                    processed_path = run_dir / processed_path
                if not processed_path.exists():
                    raise FileNotFoundError(f"processed_jsonl not found: {processed_path}")
                merge = apply_processed_jsonl(store, file=processed_path, field=field)

        python_bin = sys.executable
        chunked_cmd = (
            f"{python_bin} -m loopai.agents.Obtainer.datamixer.dataflow_chunked_runner "
            f"--input {full_jsonl.resolve()} --pipeline {agent_result.get('pipeline_path', '<pipeline.py>')} "
            f"--output {run_dir.resolve()}/full_processed.jsonl --chunk-size 10000"
        )
        apply_cmd = (
            f"{python_bin} -m loopai.skills.ObtainerCLI.cli dm --root {root} "
            f"apply-jsonl --file {run_dir.resolve()}/full_processed.jsonl --field {field} --json"
        )
        result = {
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
            "mode": agent_result.get("mode"),
            "full_jsonl": str(full_jsonl.resolve()) if full_jsonl else None,
            "full_rows_exported": int(full_audit.get("input_rows") or full_exported),
            "full_rows_out": int(full_audit.get("output_rows") or agent_result.get("full_rows_out") or 0),
            "trial_rows_out": int(audit.get("output_rows") or agent_result.get("trial_rows_out") or 0),
            "applied": bool(apply and agent_result.get("mode") != "trial_run"),
            "merge": merge,
            "agent_result": agent_result,
            "upstream": {
                "delivered_pipeline": bool(delivering),
                "full_input_jsonl": str(full_jsonl.resolve()) if full_jsonl else None,
                "chunked_run_command": chunked_cmd,
                "apply_command": apply_cmd,
            },
        }
        _write_dataflow_status(
            status_path,
            state="completed" if agent_result.get("ok") else "completed_with_errors",
            phase="completed",
            applied_rows=int((merge or {}).get("updated") or 0),
            feedback=str(agent_result.get("summary") or "DataFlowAgent 已完成"),
        )
        return result
    except Exception as exc:
        _write_dataflow_status(
            status_path,
            state="failed",
            phase="failed",
            error=str(exc),
            feedback="DataFlowAgent 运行失败",
        )
        raise
