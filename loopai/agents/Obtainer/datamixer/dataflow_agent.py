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
import shutil
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
        # Use the LAST <answer> block (innermost), which is the model's actual answer.
        # The API helper may wrap thinking/reasoning + answer tags around the model output
        # where content already has <answer> tags, creating nested wrapping.
        # Strip all <answer></answer> tags from the extracted text to get the raw score.
        text = re.sub(r'</?answer\s*>', '', answer_blocks[-1], flags=re.IGNORECASE).strip()
    else:
        # Strip any remaining <answer>/</answer> tags before parsing
        text = re.sub(r'</?answer\s*>', '', text, flags=re.IGNORECASE).strip()
        text = re.sub(r'answer\s*>', '', text, flags=re.IGNORECASE).strip()
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


def dataflow_pipeline_skill_asset() -> Path:
    """Return the repository-owned DataFlow pipeline skill asset."""
    return Path(__file__).resolve().parent / "assets" / "generating-dataflow-pipeline"


def dataflow_pipeline_skill_text() -> str:
    return (dataflow_pipeline_skill_asset() / "SKILL.md").read_text(encoding="utf-8")


def dataflow_agents_template() -> str:
    """Read the reviewed, repository-owned DataFlow agent instructions."""
    path = Path(__file__).resolve().parent / "assets" / "dataflow-agent-AGENTS.md"
    return path.read_text(encoding="utf-8")


def _project_root() -> Path | None:
    for path in Path(__file__).resolve().parents:
        if (path / "starter.yaml").exists():
            return path
    return None


def dataflow_codex_home() -> Path:
    """Dedicated Codex home for the DataFlow agent (its own AGENTS.md)."""
    root = _project_root() or Path.cwd()
    # Canonical location under outputs/obtainer/.codex/dataflow (obtainer-only).
    return Path(root) / "outputs" / "obtainer" / ".codex" / "dataflow"


DATAFLOW_AGENTS_MD = """# LoopAI DataMixer DataFlow Agent

## Task-specific benchmark and pipeline contract

Given a downstream task, first infer the capabilities measured by the target
benchmark, then design training-side content for instruction-following
post-training that develops those capabilities. The objective is not to
mechanically imitate the benchmark's evaluation prompt or completion shape.
Content style means what is written inside each post-training record: how a
standalone user-facing problem or instruction is phrased, what information and
constraints it includes, how context or examples are presented, how the
assistant answer responds, and how reasoning, code, derivations, formatting,
and level of detail are expressed. It does not mean only the schema, field
layout, empty-field pattern, benchmark interaction format, completion shape,
or the presence of `instruction` / `input` / `output`.

The benchmark's native evaluation format and the desired post-training format
are distinct. Unless the downstream request explicitly asks to preserve the
native evaluation format, construct self-contained instruction-following
records with a clear user task and a high-quality assistant response. When a
source instruction or answer does not fit the current post-training objective,
prefer generating an improved training field.

When the query names or implies a benchmark:

1. Search the DataMixer warehouse for the registered
   benchmark/contamination-guard set corresponding to the target benchmark.
2. Read that set's metadata and its associated DataMixer benchmark dataset
   records to determine its native
   evaluation format and measured capabilities. Infer the concrete problem
   types, required knowledge and reasoning, constraints, answer correctness
   criteria, and expected code/derivation behavior. Translate those requirements
   into an instruction-following post-training contract with separate standalone
   user question/instruction and assistant answer formats. Schema and
   evaluator-facing shape are supporting evidence, not the desired training
   content itself. When a source instruction or answer is evaluator-facing,
   semantically inadequate, or does not fit the current post-training objective,
   prefer generating an improved training field.
3. If the benchmark is not yet registered in any benchmark/contamination-guard
   set, download it from the network, register it in the current DataMixer
   warehouse, and then run the decontamination operator.

Keep this workflow generic across benchmark names, paths, schemas, and task
formats; do not hard-code any benchmark name or file layout.

Read the `generating-dataflow-pipeline` skill on demand when the task requires
pipeline generation: if it is available in the Codex skills directory, first
read its complete `SKILL.md` and any directly referenced template or example
needed for the task. Use it together with the built-in rules as a reference for
operator selection, field flow, pipeline structure, and trial validation, and
try to satisfy both where they apply. If it is unavailable, state that
limitation and continue with the built-in rules.

Required pipeline behavior:

1. Infer the capabilities measured by the target benchmark, then define an
   instruction-following post-training content contract that develops those
   capabilities: a standalone user problem/question format, assistant answer
   method and presentation, reasoning/code format, required information and
   constraints, examples, tone, formatting, and detail level. Separately record
   the benchmark's native evaluation format and supporting schema, and identify
   source instructions or answers that are evaluator-facing, semantically
   inadequate, or need improved training counterparts. Do not treat imitation of the
   benchmark prompt/completion shape as the training objective unless explicitly
   requested.
2. Treat the original L3 data as low-quality and untrusted by default. Before
   selecting operators, directly read and compare the full original content of
   several representative pending-data records and several records from the
   DataMixer benchmark dataset associated with the registered benchmark/
   contamination guard. Guard metadata,
   schemas, field names, non-empty rates, length statistics, and the mere
   presence of `instruction`, `input`, or `output` fields are not sufficient
   evidence of data quality or instruction-following readiness. Judge semantic
   quality, task completeness, field roles, style differences, and cross-field
   consistency from the record text itself. Build the pipeline around the
   concrete defects found in this comparison, using multiple stages of quality
   filtering and the necessary benchmark-aligned style rewriting or field
   generation. Do not decide that
   generation or rewriting is unnecessary until this direct record-level
   comparison has been completed and cited in the operator decision.
3. Use multiple filtering operators for an initial screening pass.
4. When source instructions or answers do not fit the current post-training
   objective, prefer field-generation operators to produce improved training
   fields.
5. Decide from the task requirements whether to use a native reasoning/COT
   generation operator to create a reasoning field.
6. Apply another quality-filtering pass after generation.
7. Keep `sample_id` as the join key, preserve input order, and never write
   directly to DataMixer catalog/blob files.
8. Generate a standard DataFlow pipeline, trial-run it on the given sample data,
   and report exact input/output row counts from the written JSONL files, along
   with artifact quality, supported data shape, vertical domain, and benchmark.

Deliver a complete pipeline `.py` file, the trial output JSONL, the trial input
JSONL, and a summary `.md` file.

你是 DataMixer 的 **DataFlow 后处理 agent**（dataflow agent），负责把 L1 -> L2
-> L3 链路上的数据通过 DataFlow operator 链处理成 **L4**（质量过滤、去重、
规范化、安全、SFT 有效性），默认情况下 L4 是生产出湖的数据源；若用户明确
指定 L3 出湖，则 L3 数据也可直接出湖，无需 L4 门。

## 运行环境清单

以下运行环境信息由系统在每次启动会话时自动探测并注入，直接按清单使用，
不要再去全仓探测运行环境：

<!-- runtime_environment_manifest -->

## 核心工作流：试跑成功 -> 交付 pipeline -> 上层跑 chunk 全量 -> L4

1. 先用样例 JSONL 检查代表性记录原文，规划 operator 链，生成标准 DataFlow FileStorage
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
   交付 summary 里说明桶规模与预期冗余即可。若用户明确指定 L3 出湖，则跳过
   L4 门，L3 数据可直接出湖（仍需满足湖内可用量/配比/质量门）。

## 硬约束

- 数据湖必须已 init/load，先 `dm lake load/unbind` 管理指针，不要新建湖，
  不要跨 task 复用旧绑定。
- **质量评估必须使用 DataFlow 的 LLM 评估算子**（如 `PromptedEvaluator` /
  `PromptedFilter` 等 LLM 打分/过滤算子），不得因耗时或成本而退化成纯启发式
  规则打分；只有任务本身没有 LLM 打分语义、或 LLM serving 不可用时才允许
  规则算子兜底，且必须在 summary 里说明具体原因。当源数据的指令或答案不符合
  当前后训练目标时，鼓励使用生成算子构造更合适的训练字段，再使用评估/过滤
  算子验证生成内容。

- `sample_id` 是唯一 join key，必须原样保留；输出顺序必须与输入一致。
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
    # A reviewed AGENTS.md is authoritative. Preserve it verbatim when present;
    # only bootstrap the file from the built-in template on a fresh workspace.
    if not agents_path.exists():
        agents_path.write_text(dataflow_agents_template(), encoding="utf-8")
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    source = dataflow_pipeline_skill_asset()
    target = skills_dir / "generating-dataflow-pipeline"
    if target.is_symlink():
        target.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True)
    system_skill = Path(__file__).resolve().parent / "assets" / "skill-creator"
    system_target = skills_dir / "skill-creator"
    if system_target.is_symlink() and not system_target.exists():
        system_target.unlink()
    if system_skill and (system_skill / "SKILL.md").exists() and not system_target.exists():
        try:
            system_target.symlink_to(system_skill, target_is_directory=True)
        except FileExistsError:
            pass
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

    The serving endpoint/model follow the Starter model-pool **default model**
    (read from the persisted system config, DB preferred), so DataFlow LLM
    operators run through the same response proxy as every other agent.
    """
    from loopai.schema.model_pool import load_starter_system_config_sync

    root = _project_root()
    db_path = (root / "api" / "db" / "db.sqlite3") if root else None
    # Use the project DB when it exists (production); otherwise fall back to the
    # project's starter.yaml (standalone / tests).
    system = load_starter_system_config_sync(
        workspace=root,
        prefer_db=bool(db_path and db_path.exists()),
    ) or {}
    model_value = system.get("model")
    has_explicit_pool = (
        isinstance(model_value, list)
        or (isinstance(model_value, dict) and isinstance(model_value.get("pool") or model_value.get("models"), list))
    )
    if has_explicit_pool:
        pool = StarterModelPool(system)
        entry = pool.default_entry()
        if entry is not None:
            provider = pool.resolve_proxy_provider(entry.name, tier=entry.tier)
            if provider is not None:
                return {
                    "api_url": chat_completions_url(provider.base_url),
                    "model_name": provider.model,
                    "api_key_env": "DF_API_KEY",
                    "api_key": provider.api_key,
                }
    # Legacy fallback (no explicit model pool): flat starter.yaml keys.
    root = _project_root()
    starter = root / "starter.yaml" if root else None
    doc = {}
    if starter and starter.exists():
        try:
            import yaml
            doc = yaml.safe_load(starter.read_text(encoding="utf-8")) or {}
        except Exception:
            doc = {}
    system = doc.get("system") or {}
    obtainer = (doc.get("default_states") or {}).get("obtainer") or {}
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
    op_llm = operator_llm or {}
    op_llm_block = (
        "For DataFlow LLM-based operators, instantiate APILLMServing_request with:\n"
        f'- api_url="{op_llm.get("api_url", "")}"\n'
        f'- model_name="{op_llm.get("model_name", "gpt-4o-mini")}"\n'
        f'- key_name_of_api_key="{op_llm.get("api_key_env", "DF_API_KEY")}"\n'
        "Do not use the Codex planning model as the DataFlow operator model.\n"
    )
    return f"""You are the DataMixer DataFlow planning agent.

Use the installed `generating-dataflow-pipeline` skill from the Codex skills
directory. Read it on demand and use it as a reference for this task.

Task:
- Downstream target: {target}
- Sample JSONL file: {trial_jsonl}
- Text/input field: {field}
- Work directory: {work_dir}
- Full input JSONL reserved for the upper layer: {full_jsonl}

{op_llm_block}

Return one final JSON object with:
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
        if missing_fields:
            raise CodexError(
                f"{label} row {row_number} ({sample_id}) dropped original "
                "input fields: " + ", ".join(missing_fields[:5])
            )
        # A DataFlow refinement pipeline is allowed to transform field values
        # (e.g. normalize text, compute scores); only losing a field is fatal.
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
        "ok", "mode", "pipeline_path",
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
                    current_prompt = (
                        f"{prompt}\nThe previous result failed validation: {exc}\n"
                        "Correct the result and return the required final JSON object."
                    )
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
