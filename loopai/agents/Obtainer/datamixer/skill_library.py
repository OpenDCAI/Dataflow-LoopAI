"""DataFlow skill library: internalize delivered pipelines into reusable skills.

After a successful ``dataflow agent-run``, a post-run Codex agent (the
internalizer) turns the delivered pipeline into a skill under the DataFlow
agent's own Codex home (``$CODEX_HOME/skills``) following skill-creator
guidance. The library enforces a lifecycle:

- at most ``MAX_LIBRARY_SKILLS`` (20) user skills;
- when a new skill is functionally identical to an existing one (same
  normalized ``function_key``), keep the earlier skill and reset its lifecycle
  (bump ``last_used_at``, record the superseded run);
- when the limit is exceeded, evict the least-recently-used skills.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

MAX_LIBRARY_SKILLS = 20
LIBRARY_SCHEMA_VERSION = 1
DEFAULT_INTERNALIZE_TIMEOUT = 900
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
_NAME_RE = re.compile(r"^[a-z0-9-]+$")


def dataflow_skills_home() -> Path:
    """Skill library root for the DataFlow agent.

    Override with ``DATAFLOW_AGENT_SKILLS_HOME``; default is the DataFlow
    Codex home's ``skills/`` folder so Codex auto-discovers them.
    """
    explicit = os.environ.get("DATAFLOW_AGENT_SKILLS_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    from .dataflow_agent import dataflow_codex_home

    return dataflow_codex_home() / "skills"


def library_registry_path() -> Path:
    """Lifecycle registry, kept next to the skills folder (not inside it)."""
    return dataflow_skills_home().parent / "skill_library.json"


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def load_library() -> dict[str, Any]:
    path = library_registry_path()
    default = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "max_skills": MAX_LIBRARY_SKILLS,
        "skills": [],
    }
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(data, dict):
        return default
    data.setdefault("schema_version", LIBRARY_SCHEMA_VERSION)
    data.setdefault("max_skills", MAX_LIBRARY_SKILLS)
    data.setdefault("skills", [])
    return data


def save_library(data: dict[str, Any]) -> None:
    path = library_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_skill_dirs() -> list[Path]:
    home = dataflow_skills_home()
    if not home.is_dir():
        return []
    return sorted(
        (
            p
            for p in home.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and (p / "SKILL.md").is_file()
        ),
        key=lambda p: p.name,
    )


def normalize_function_key(key: str) -> str:
    return re.sub(r"\s+", " ", str(key or "").strip().lower())


# ---------------------------------------------------------------------------
# validation (mirrors skill-creator quick_validate.py)
# ---------------------------------------------------------------------------

def validate_skill_payload(payload: dict[str, Any]) -> list[str]:
    """Return validation errors (empty list == valid)."""
    errors: list[str] = []
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("skill.name is required")
    else:
        name = name.strip()
        if not _NAME_RE.match(name) or name.startswith("-") or name.endswith("-") or "--" in name:
            errors.append(
                f"skill.name {name!r} must be hyphen-case "
                "(lowercase letters, digits, hyphens only)"
            )
        if len(name) > MAX_SKILL_NAME_LENGTH:
            errors.append(f"skill.name too long ({len(name)} > {MAX_SKILL_NAME_LENGTH})")

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("skill.description is required")
    else:
        description = description.strip()
        if "\n" in description:
            errors.append("skill.description must be a single line")
        if "<" in description or ">" in description:
            errors.append("skill.description cannot contain angle brackets (< or >)")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            errors.append(
                f"skill.description too long ({len(description)} > {MAX_DESCRIPTION_LENGTH})"
            )

    body = payload.get("body")
    if not isinstance(body, str) or not body.strip():
        errors.append("skill.body is required")

    function_key = payload.get("function_key")
    if not isinstance(function_key, str) or not function_key.strip():
        errors.append("skill.function_key is required")

    files = payload.get("files") or []
    if not isinstance(files, list):
        errors.append("skill.files must be a list")
        files = []
    for item in files:
        rel = str(item.get("path") or "")
        parts = Path(rel).parts
        if not rel or Path(rel).is_absolute() or ".." in parts:
            errors.append(f"unsafe skill file path: {rel!r}")
        elif not (
            rel.startswith("references/")
            or rel.startswith("scripts/")
            or rel.startswith("assets/")
        ):
            errors.append(
                f"skill file must live under references/, scripts/, or assets/: {rel!r}"
            )
        if not isinstance(item.get("content"), str):
            errors.append(f"skill file {rel!r} content must be a string")
    return errors


# ---------------------------------------------------------------------------
# library mutations (dedupe + max-20 lifecycle)
# ---------------------------------------------------------------------------

def _write_skill(home: Path, payload: dict[str, Any]) -> None:
    name = payload["name"].strip()
    target = home / name
    if target.exists():
        raise ValueError(f"skill directory already exists: {target}")
    target.mkdir(parents=True)
    try:
        description = payload["description"].strip()
        body = payload["body"].strip()
        (target / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
            encoding="utf-8",
        )
        manifest = payload.get("manifest") or {}
        if isinstance(manifest, dict) and manifest:
            agents = target / "agents"
            agents.mkdir(exist_ok=True)
            lines = ["interface:"]
            for key in (
                "display_name",
                "short_description",
                "icon_small",
                "icon_large",
                "brand_color",
                "default_prompt",
            ):
                if manifest.get(key):
                    value = str(manifest[key]).replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'  {key}: "{value}"')
            (agents / "openai.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in payload.get("files") or []:
            rel = Path(str(item["path"]))
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(str(item["content"]), encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def enforce_max_skills(data: dict[str, Any], max_skills: int | None = None) -> list[str]:
    """Evict least-recently-used skills when the library exceeds the cap."""
    max_skills = max_skills or int(data.get("max_skills") or MAX_LIBRARY_SKILLS)
    entries = data.get("skills") or []
    if len(entries) <= max_skills:
        return []
    ordered = sorted(
        entries,
        key=lambda e: (e.get("last_used_at") or 0, e.get("created_at") or 0),
    )
    excess = ordered[: len(entries) - max_skills]
    evicted_names = {str(e["name"]) for e in excess}
    home = dataflow_skills_home()
    for entry in excess:
        target = home / str(entry["name"])
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
    data["skills"] = [e for e in entries if e["name"] not in evicted_names]
    save_library(data)
    return [str(e["name"]) for e in excess]


def add_or_refresh_skill(payload: dict[str, Any], *, run_dir: str) -> dict[str, Any]:
    """Add a skill or, on functional duplicate, keep the earlier one and reset
    its lifecycle. Returns an outcome dict describing the action taken."""
    errors = validate_skill_payload(payload)
    if errors:
        raise ValueError("; ".join(errors))

    home = dataflow_skills_home()
    home.mkdir(parents=True, exist_ok=True)
    name = payload["name"].strip()
    function_key = normalize_function_key(payload["function_key"])
    operator_chain = payload.get("operator_chain") or []
    now = _now()

    data = load_library()
    entries = data["skills"]
    dup = next(
        (
            e
            for e in entries
            if normalize_function_key(e.get("function_key")) == function_key
        ),
        None,
    )
    if dup is not None:
        # 功能完全一致：只保留更早的，并重置其生命周期（刷新 last_used_at，
        # 记录被替代的运行），新技能不落盘。
        dup["last_used_at"] = now
        dup["dedupe_count"] = int(dup.get("dedupe_count") or 0) + 1
        dup.setdefault("superseded_runs", []).append(
            {"run_dir": str(run_dir), "at": now}
        )
        if operator_chain:
            dup["operator_chain"] = operator_chain
        save_library(data)
        return {
            "action": "duplicate_kept_existing",
            "name": str(dup["name"]),
            "lifecycle_reset": True,
            "dedupe_count": int(dup.get("dedupe_count") or 0),
            "superseded_runs": len(dup.get("superseded_runs") or []),
        }

    if any(e.get("name") == name for e in entries):
        raise ValueError(
            f"skill name {name!r} already exists with a different function_key; rename it"
        )
    if (home / name).exists():
        raise ValueError(f"skill directory already exists: {home / name}")

    _write_skill(home, payload)
    entry = {
        "name": name,
        "function_key": function_key,
        "operator_chain": operator_chain,
        "description": payload["description"].strip(),
        "created_at": now,
        "last_used_at": now,
        "run_dir": str(run_dir),
        "dedupe_count": 0,
        "superseded_runs": [],
    }
    entries.append(entry)
    save_library(data)
    evicted = enforce_max_skills(data)
    return {"action": "added", "name": name, "evicted": evicted}


# ---------------------------------------------------------------------------
# skill-creator guidance (embedded like DataFlow-Skills)
# ---------------------------------------------------------------------------

def _codex_home_candidates() -> list[Path]:
    from .dataflow_agent import dataflow_codex_home
    from .codex import codex_home

    homes: list[Path] = []
    for home in (dataflow_codex_home(), codex_home()):
        if home is not None and home not in homes:
            homes.append(home)
    return homes


def _skill_creator_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("SKILL_CREATOR_ROOT")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for home in _codex_home_candidates():
        candidates.append(home / "skills" / ".system" / "skill-creator")
    candidates.append(Path.home() / ".codex" / "skills" / ".system" / "skill-creator")
    candidates.append(Path.home() / ".codex" / "skills" / "skill-creator")
    return candidates


def skill_creator_guidance() -> str:
    for root in _skill_creator_candidates():
        skill_md = root / "SKILL.md"
        if skill_md.is_file():
            return skill_md.read_text(encoding="utf-8")
    return (
        "# Skill Creator (embedded fallback)\n"
        "Create concise, self-contained skill folders. SKILL.md requires YAML "
        "frontmatter with exactly `name` (hyphen-case) and `description` (what "
        "it does + when to use, no angle brackets, <=1024 chars). Write the "
        "body in imperative form; keep it lean and split detail into "
        "references/ files. Name the folder exactly after the skill name. "
        "Validate with scripts/quick_validate.py when available.\n"
    )


def quick_validate_script() -> Path | None:
    for root in _skill_creator_candidates():
        script = root / "scripts" / "quick_validate.py"
        if script.is_file():
            return script
    return None


def _run_quick_validate(skill_dir: Path) -> dict[str, Any]:
    script = quick_validate_script()
    if script is None:
        return {
            "checker": "builtin",
            "ok": True,
            "message": "skill-creator quick_validate.py not found; builtin checks passed",
        }
    try:
        proc = subprocess.run(
            [sys.executable, str(script), str(skill_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "checker": str(script),
            "ok": proc.returncode == 0,
            "message": (proc.stdout or proc.stderr).strip(),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"checker": str(script), "ok": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# post-run internalizer agent
# ---------------------------------------------------------------------------

def build_internalize_prompt(
    *,
    run_context: dict[str, Any],
    library: list[dict[str, Any]],
    guidance: str,
) -> str:
    if library:
        lib_block = "\n".join(
            f"- {e.get('name')} | function_key: {e.get('function_key')} | "
            f"ops: {json.dumps(e.get('operator_chain') or [], ensure_ascii=False)} | "
            f"{e.get('description')}"
            for e in library
        )
    else:
        lib_block = "- (empty)"
    ctx = json.dumps(run_context, ensure_ascii=False, indent=2)
    return f"""你是 LoopAI DataFlow 技能内化 agent（skill internalizer）。

背景：DataFlow agent 刚刚完成一次运行并交付了一条经过试跑验证的 pipeline。
你的任务：按下面嵌入的 skill-creator 规范，把这次运行产物**内化为一个可复用
技能**，供未来 DataFlow agent 在相似 target 下复用。

## 本次运行上下文
```json
{ctx}
```

## 技能库现状（历史沉淀，供你对齐 function_key、避免重复内化）
{lib_block}

## 必需做法
1. 阅读本次产物：pipeline 文件（pipeline_path）、试跑输入/输出 JSONL、
   status.json（含 operator_decision 与统计）。字段、阈值、seed 从产物中读取。
2. 按 skill-creator 规范提炼：技能必须**可复用**——把 target 适配为
   "when to use" 触发条件，字段名、阈值、算子链写清楚；具体 pipeline 作为
   references/pipeline_example.py 附上；SKILL.md 正文用祈使句、简洁
   （<150 行），不得包含本次运行的绝对路径。
3. function_key 给出一句规范化功能标识（小写、去多余空白），用于生命周期去重；
   即使与库中某项功能完全一致，也照常提交新技能，由库管理器保留更早的并重置
   其生命周期。
4. 只返回一个 JSON 对象（不要 Markdown 包裹），结构如下：
{{
  "ok": true,
  "skill": {{
    "name": "动词开头-短横线-小写",
    "description": "做什么 + 何时用（单行，<=1024 字符，禁止 <> 尖括号）",
    "body": "SKILL.md 正文（不含 frontmatter，祈使句，简洁）",
    "files": [{{"path": "references/pipeline_example.py", "content": "..."}}],
    "manifest": {{
      "display_name": "人类可读名",
      "short_description": "25-64 字符 UI 简介",
      "default_prompt": "以 Use $<skill-name> 开头的默认提示"
    }},
    "function_key": "规范化功能标识",
    "operator_chain": ["..."],
    "summary": "一句话总结"
  }},
  "error": null
}}
若无法内化，返回 {{"ok": false, "error": "原因"}}。

--- skill-creator 规范（嵌入） ---
{guidance}
"""


def internalize_run_artifacts(
    *,
    run_dir: Path,
    target: str,
    dataset: str | None,
    where: str | None,
    field: str,
    expected_outputs: str | None,
    agent_result: dict[str, Any],
    prov: dict[str, str],
    cwd: str,
    timeout: int = DEFAULT_INTERNALIZE_TIMEOUT,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Internalize one delivered DataFlow pipeline into the skill library.

    Runs the post-run internalizer Codex agent (skill-creator guidance
    embedded), then writes/validates the skill and applies the library
    lifecycle. Never raises: returns a report dict.
    """
    if not agent_result.get("ok") or not agent_result.get("pipeline_path"):
        return {
            "ok": False,
            "skipped": True,
            "reason": "no delivered pipeline to internalize",
        }

    run_dir = Path(run_dir).resolve()
    library = load_library().get("skills") or []
    prompt = build_internalize_prompt(
        run_context={
            "target": target,
            "dataset": dataset,
            "where": where,
            "field": field,
            "expected_outputs": expected_outputs,
            "operator_decision": agent_result.get("operator_decision") or {},
            "mode": agent_result.get("mode"),
            "pipeline_path": str(agent_result.get("pipeline_path")),
            "trial_jsonl": agent_result.get("trial_jsonl"),
            "processed_jsonl": agent_result.get("processed_jsonl"),
            "trial_rows_in": agent_result.get("trial_rows_in"),
            "trial_rows_out": agent_result.get("trial_rows_out"),
            "summary": agent_result.get("summary") or "",
            "run_dir": str(run_dir),
            "status_json": str(run_dir / "status.json"),
        },
        library=library,
        guidance=skill_creator_guidance(),
    )

    from .dataflow_agent import dataflow_codex_home

    home = dataflow_codex_home()
    previous = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(home)
    try:
        if runner is None:
            from .codex import run_via_sdk
            runner = run_via_sdk
        raw = runner(prompt, prov, cwd=cwd, timeout=timeout)
    except Exception as exc:  # internalizer failures never fail the dataflow run
        report = {"ok": False, "error": str(exc)}
        _persist_report(run_dir, report)
        return report
    finally:
        if previous is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = previous

    payload = raw if isinstance(raw, dict) else {}
    skill = payload.get("skill")
    if not payload.get("ok") or not isinstance(skill, dict):
        report = {
            "ok": False,
            "error": payload.get("error") or "internalizer returned no valid skill",
            "runner_summary": payload.get("summary") or "",
        }
        _persist_report(run_dir, report)
        return report

    report: dict[str, Any] = {
        "ok": True,
        "skill": skill.get("name"),
        "function_key": skill.get("function_key"),
    }
    try:
        outcome = add_or_refresh_skill(skill, run_dir=str(run_dir))
        report.update(outcome)
        if outcome.get("name"):
            report["skill"] = outcome["name"]
        report["validated"] = _run_quick_validate(
            dataflow_skills_home() / str(skill["name"]).strip()
        )
    except Exception as exc:
        report.update({"ok": False, "error": str(exc)})
    _persist_report(run_dir, report)
    return report


def _persist_report(run_dir: Path, report: dict[str, Any]) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "skill_internalize.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
