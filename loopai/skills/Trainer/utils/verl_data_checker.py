from __future__ import annotations

import ast
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

from loopai.common.tracking import strip_retired_tracking_environment
from loopai.skills.Trainer.rewards import (
    is_verl_builtin_data_source,
    normalize_reward_preset,
)


_REQUIRED_COLUMNS = {"prompt", "data_source", "reward_model"}
_REWARD_ROUTER_PATH = Path(__file__).resolve().parents[1] / "rewards" / "router.py"
_SEMANTIC_SAMPLE_LIMIT = 100
_REWARD_SMOKE_SCRIPT = r"""
import importlib.util
import json
import math
import sys

payload = json.load(sys.stdin)
sys.path.insert(0, payload["verl_dir"])
spec = importlib.util.spec_from_file_location("loopai_reward_smoke", payload["path"])
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load reward module")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
function = getattr(module, payload["function_name"])
result = function(
    data_source=payload["data_source"],
    solution_str=payload["solution_str"],
    ground_truth=payload["ground_truth"],
    extra_info=payload.get("extra_info") or {},
    **(payload.get("reward_kwargs") or {}),
)
if isinstance(result, dict):
    result = result["score"]
elif isinstance(result, (list, tuple)):
    result = result[0]
score = float(result)
if not math.isfinite(score):
    raise ValueError("reward score is not finite")
print(json.dumps({"score": score}))
"""


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _validate_sample(row: Dict[str, Any], label: str, index: int) -> list[str]:
    errors: list[str] = []
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or not prompt:
        errors.append(f"{label} row {index} prompt must be a non-empty chat message list")
    else:
        for message_index, message in enumerate(prompt):
            if not isinstance(message, dict):
                errors.append(
                    f"{label} row {index} prompt[{message_index}] must be a mapping"
                )
                break
            if _is_missing(message.get("role")) or _is_missing(message.get("content")):
                errors.append(
                    f"{label} row {index} prompt[{message_index}] requires non-empty role/content"
                )
                break

    if _is_missing(row.get("data_source")):
        errors.append(f"{label} row {index} data_source must be non-empty")

    reward_model = row.get("reward_model")
    if not isinstance(reward_model, dict):
        errors.append(f"{label} row {index} reward_model must be a mapping")
    elif _is_missing(reward_model.get("ground_truth")):
        errors.append(f"{label} row {index} reward_model.ground_truth must be non-empty")
    return errors


def _first_reward_sample(path_value: str) -> Dict[str, Any] | None:
    try:
        import pyarrow.parquet as parquet

        parquet_file = parquet.ParquetFile(Path(path_value).expanduser().resolve())
        columns = ["data_source", "reward_model"]
        if "extra_info" in parquet_file.schema_arrow.names:
            columns.append("extra_info")
        for batch in parquet_file.iter_batches(batch_size=1, columns=columns):
            rows = batch.to_pylist()
            return rows[0] if rows else None
    except Exception:
        return None
    return None


def _smoke_solution(data_source: str, ground_truth: Any, preset: str) -> str:
    value = ground_truth
    if isinstance(value, dict) and "target" in value:
        value = value["target"]
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    answer = str(value)
    if preset == "gsm8k_exact" or data_source == "openai/gsm8k":
        return f"#### {answer}"
    if preset == "qa_exact_match" or data_source.startswith("searchR1_"):
        return f"<answer>{answer}</answer>"
    return f"\\boxed{{{answer}}}"


def _smoke_test_preset(
    train_path: str,
    reward: Dict[str, Any],
    reward_kwargs: Dict[str, Any] | None,
    verl_dir: str | None,
    verl_env_path: str | None,
) -> Dict[str, Any]:
    if not verl_dir or not verl_env_path:
        return {
            "status": "skipped",
            "reason": "verl_dir and verl_env_path are required for preset smoke testing",
        }
    sample = _first_reward_sample(train_path)
    if not sample:
        return {"status": "skipped", "reason": "no readable reward sample"}
    reward_model = sample.get("reward_model") or {}
    ground_truth = reward_model.get("ground_truth")
    data_source = str(sample.get("data_source") or "")
    kwargs = dict(reward_kwargs or {})
    kwargs["preset"] = reward.get("preset") or "auto"
    payload = {
        "verl_dir": str(Path(verl_dir).expanduser().resolve()),
        "path": reward["path"],
        "function_name": reward["function_name"],
        "data_source": data_source,
        "solution_str": _smoke_solution(data_source, ground_truth, kwargs["preset"]),
        "ground_truth": ground_truth,
        "extra_info": sample.get("extra_info") or {},
        "reward_kwargs": kwargs,
    }
    try:
        from loopai.skills.Trainer.utils.verl_launcher import _resolve_python_command

        python_command, env_root = _resolve_python_command(verl_env_path)
        environment = strip_retired_tracking_environment(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (payload["verl_dir"], environment.get("PYTHONPATH", ""))
            if item
        )
        if env_root:
            environment["PATH"] = os.pathsep.join(
                item
                for item in (str(Path(env_root) / "bin"), environment.get("PATH", ""))
                if item
            )
        completed = subprocess.run(
            [*python_command, "-c", _REWARD_SMOKE_SCRIPT],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=payload["verl_dir"],
            env=environment,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            return {"status": "failed", "error": detail[-2000:]}
        output = json.loads((completed.stdout or "{}").strip().splitlines()[-1])
        score = float(output["score"])
        if not math.isfinite(score):
            raise ValueError("reward score is not finite")
        return {"status": "passed", "score": score, "data_source": data_source}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _check_parquet(path_value: str, label: str) -> Dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    result: Dict[str, Any] = {
        "label": label,
        "path": str(path),
        "is_valid": True,
        "columns": [],
        "rows": None,
        "data_sources": [],
        "semantic_sample_size": 0,
        "errors": [],
        "warnings": [],
    }
    if not path.is_file():
        result["errors"].append(f"{label} dataset does not exist: {path}")
    elif path.suffix.lower() != ".parquet":
        result["errors"].append(f"{label} dataset must be a .parquet file: {path}")
    else:
        try:
            with path.open("rb") as stream:
                header = stream.read(4)
                stream.seek(-4, 2)
                footer = stream.read(4)
            if header != b"PAR1" or footer != b"PAR1":
                result["errors"].append(f"{label} file does not have valid Parquet magic bytes: {path}")
        except (OSError, ValueError) as exc:
            result["errors"].append(f"Unable to read {label} dataset: {exc}")

    if not result["errors"]:
        try:
            import pyarrow.parquet as parquet

            parquet_file = parquet.ParquetFile(path)
            columns = set(parquet_file.schema_arrow.names)
            result["columns"] = sorted(columns)
            result["rows"] = parquet_file.metadata.num_rows
            if result["rows"] == 0:
                result["errors"].append(f"{label} Parquet dataset is empty: {path}")
            missing = sorted(_REQUIRED_COLUMNS - columns)
            if missing:
                result["errors"].append(
                    f"{label} Parquet is missing required Verl columns: {', '.join(missing)}"
                )
            else:
                data_sources = set()
                for batch in parquet_file.iter_batches(
                    batch_size=4096,
                    columns=["data_source"],
                ):
                    for row in batch.to_pylist():
                        value = row.get("data_source")
                        if not _is_missing(value):
                            data_sources.add(str(value).strip())
                result["data_sources"] = sorted(data_sources)

                sampled = []
                for batch in parquet_file.iter_batches(
                    batch_size=_SEMANTIC_SAMPLE_LIMIT,
                    columns=sorted(_REQUIRED_COLUMNS),
                ):
                    sampled = batch.to_pylist()[:_SEMANTIC_SAMPLE_LIMIT]
                    break
                result["semantic_sample_size"] = len(sampled)
                semantic_errors: list[str] = []
                for index, row in enumerate(sampled):
                    semantic_errors.extend(_validate_sample(row, label, index))
                    if len(semantic_errors) >= 10:
                        result["warnings"].append(
                            f"{label} semantic validation stopped after 10 errors"
                        )
                        break
                result["errors"].extend(semantic_errors[:10])
        except ImportError:
            result["warnings"].append(
                "pyarrow is not installed in the LoopAI environment; schema columns will be "
                "validated by Verl when training starts."
            )
        except Exception as exc:
            result["errors"].append(f"Unable to inspect {label} Parquet schema: {exc}")

    result["is_valid"] = not result["errors"]
    return result


def _check_reward(
    path_value: str | None,
    function_name: str,
    reward_mode: str | None = None,
    reward_preset: str | None = None,
    reward_kwargs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    mode = str(reward_mode or ("custom" if path_value else "auto")).strip().lower()
    result: Dict[str, Any] = {
        "path": None,
        "function_name": function_name,
        "mode": mode,
        "preset": None,
        "is_valid": True,
        "errors": [],
        "warnings": [],
    }
    if mode not in {"auto", "preset", "custom"}:
        result["errors"].append("reward mode must be auto, preset, or custom")
        result["is_valid"] = False
        return result
    if reward_kwargs is not None and not isinstance(reward_kwargs, dict):
        result["errors"].append("reward kwargs must be a mapping")

    if mode == "custom":
        if not path_value:
            result["errors"].append("custom reward mode requires a Python file path")
            result["is_valid"] = False
            return result
        resolved_path = path_value
    else:
        try:
            preset = normalize_reward_preset("auto" if mode == "auto" else reward_preset)
            result["preset"] = preset
        except ValueError as exc:
            result["errors"].append(str(exc))
            result["is_valid"] = False
            return result
        resolved_path = str(_REWARD_ROUTER_PATH)
        function_name = "compute_score"
        result["function_name"] = function_name

    path = Path(resolved_path).expanduser().resolve()
    result["path"] = str(path)
    if not path.is_file():
        result["errors"].append(f"Custom reward Python file does not exist: {path}")
    elif path.suffix.lower() != ".py":
        result["errors"].append(f"Custom reward must be a Python file: {path}")
    else:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            defined = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if function_name not in defined:
                result["errors"].append(
                    f"Reward function '{function_name}' was not found in {path}"
                )
        except (OSError, SyntaxError) as exc:
            result["errors"].append(f"Unable to validate reward function: {exc}")
    result["is_valid"] = not result["errors"]
    return result


def check_verl_grpo_inputs(
    train_path: str,
    eval_path: str,
    reward_path: str | None = None,
    reward_function_name: str = "compute_score",
    reward_mode: str | None = None,
    reward_preset: str | None = None,
    reward_kwargs: Dict[str, Any] | None = None,
    verl_dir: str | None = None,
    verl_env_path: str | None = None,
) -> Dict[str, Any]:
    """Validate the files required by the initial LoopAI Verl GRPO adapter."""
    train = _check_parquet(train_path, "train")
    validation = _check_parquet(eval_path, "validation")
    reward = _check_reward(
        reward_path,
        reward_function_name,
        reward_mode,
        reward_preset,
        reward_kwargs,
    )
    if reward.get("mode") in {"auto", "preset"} and reward.get("preset") in {
        "auto",
        "verl_builtin",
    }:
        data_sources = set(train.get("data_sources") or []) | set(
            validation.get("data_sources") or []
        )
        unknown = sorted(
            source for source in data_sources if not is_verl_builtin_data_source(source)
        )
        if unknown:
            reward["errors"].append(
                "Verl's built-in reward router does not support data_source values: "
                + ", ".join(unknown)
                + "; select a named preset or custom reward"
            )
            reward["is_valid"] = False
    if reward.get("mode") in {"auto", "preset"} and reward.get("is_valid"):
        reward["smoke_test"] = _smoke_test_preset(
            train_path,
            reward,
            reward_kwargs,
            verl_dir,
            verl_env_path,
        )
        if reward["smoke_test"].get("status") == "failed":
            reward["errors"].append(
                "Reward preset smoke test failed: "
                + str(reward["smoke_test"].get("error") or "unknown error")
            )
            reward["is_valid"] = False
    errors = train["errors"] + validation["errors"] + reward["errors"]
    warnings = train["warnings"] + validation["warnings"] + reward["warnings"]
    return {
        "framework": "verl",
        "stage": "grpo",
        "is_valid": not errors,
        "train": train,
        "validation": validation,
        "reward": reward,
        "errors": errors,
        "warnings": warnings,
        "total_samples": train.get("rows"),
    }


def generate_verl_data_report(result: Dict[str, Any]) -> str:
    status = "PASS" if result.get("is_valid") else "FAIL"
    lines = [f"Verl GRPO input check: {status}"]
    for key in ("train", "validation"):
        item = result.get(key) or {}
        lines.append(f"- {key}: {item.get('path')}")
        if item.get("rows") is not None:
            lines.append(f"  rows: {item.get('rows')}")
        if item.get("columns"):
            lines.append(f"  columns: {', '.join(item['columns'])}")
        if item.get("data_sources"):
            lines.append(f"  data_sources: {', '.join(item['data_sources'])}")
        if item.get("semantic_sample_size") is not None:
            lines.append(f"  semantic rows checked: {item.get('semantic_sample_size')}")
    reward = result.get("reward") or {}
    lines.append(
        f"- reward: mode={reward.get('mode')}, preset={reward.get('preset') or 'custom'}, "
        f"path={reward.get('path')} ({reward.get('function_name')})"
    )
    smoke_test = reward.get("smoke_test") or {}
    if smoke_test:
        lines.append(f"  smoke test: {smoke_test.get('status')}")
    for error in result.get("errors") or []:
        lines.append(f"ERROR: {error}")
    for warning in result.get("warnings") or []:
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines) + "\n"
