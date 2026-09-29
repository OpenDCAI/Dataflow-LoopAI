"""Run the bundled BIRD-style Text2SQL grader in a Docker container."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict


BIRD_EVAL_IMAGE = os.getenv("BIRD_EVAL_IMAGE", "loopai-bird-eval:dev")
BIRD_EVAL_CONTEXT = Path(__file__).resolve().parent.parent / "docker" / "bird_eval"


def _docker_executable() -> str:
    docker = shutil.which("docker")
    if docker:
        return docker
    # Docker Desktop can put its CLI in this user directory without adding it
    # to the PATH of an already-running terminal or application.
    desktop_cli = Path.home() / ".docker" / "bin" / "docker"
    if desktop_cli.is_file():
        return str(desktop_cli)
    raise FileNotFoundError("Docker CLI not found; install/start Docker Desktop or add docker to PATH")


def _docker_environment(docker: str) -> dict[str, str]:
    environment = os.environ.copy()
    docker_dir = str(Path(docker).parent)
    search_path = environment.get("PATH", "")
    if docker_dir not in search_path.split(os.pathsep):
        environment["PATH"] = docker_dir + os.pathsep + search_path
    return environment


def _ensure_bird_eval_image(docker: str, environment: dict[str, str], writer=None) -> None:
    probe = subprocess.run(
        [docker, "image", "inspect", BIRD_EVAL_IMAGE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, check=False,
    )
    if probe.returncode == 0:
        return
    if not (BIRD_EVAL_CONTEXT / "Dockerfile").is_file():
        raise FileNotFoundError(f"BIRD evaluator Dockerfile is missing: {BIRD_EVAL_CONTEXT}")
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(
            current="judger", progress=0.0,
            message="未找到 BIRD 评测镜像，正在自动构建",
            data={"image": BIRD_EVAL_IMAGE, "context": str(BIRD_EVAL_CONTEXT)},
        ))
    command = [docker, "build", "-t", BIRD_EVAL_IMAGE]
    base_image = os.getenv("BIRD_EVAL_BASE_IMAGE")
    if base_image:
        command.extend(["--build-arg", f"PYTHON_BASE_IMAGE={base_image}"])
    command.append(str(BIRD_EVAL_CONTEXT))
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL, env=environment)


def _task_ids(path: Path) -> list[str]:
    task_ids: list[str] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}, line {line_number}: {exc}") from exc
            if not isinstance(row, dict) or not isinstance(row.get("task_id"), (str, int)):
                raise ValueError(f"Missing task_id in {path}, line {line_number}")
            task_ids.append(str(row["task_id"]))
    return task_ids


def _check_sample_coverage(problems: Path, samples: Path, case_num: int) -> tuple[int, int]:
    if case_num < 1:
        raise ValueError("eval_case_num must be positive")
    problem_ids = _task_ids(problems)
    if not problem_ids or len(set(problem_ids)) != len(problem_ids):
        raise ValueError(f"Text2SQL problem file is empty or has duplicate task_id: {problems}")
    counts = Counter(_task_ids(samples))
    missing = sorted(set(problem_ids) - set(counts))
    extra = sorted(set(counts) - set(problem_ids))
    wrong_counts = {key: counts[key] for key in problem_ids if counts[key] != case_num}
    if missing or extra or wrong_counts:
        raise ValueError(
            f"Text2SQL samples do not match the problem set: missing={missing[:5]}, "
            f"extra={extra[:5]}, sample_counts={dict(list(wrong_counts.items())[:5])}, "
            f"expected_per_question={case_num}"
        )
    return len(problem_ids), sum(counts.values())


def _check_model_output_health(samples: Path) -> None:
    """Keep the previous Judger guard against an entirely empty model batch."""
    inspected = 0
    nonempty = 0
    with samples.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            inspected += 1
            nonempty += int(isinstance(row.get("completion"), str) and bool(row["completion"].strip()))
            if inspected >= 10:
                break
    if inspected and not nonempty:
        raise RuntimeError(
            f"All {inspected} sampled Text2SQL completions are empty; check model/vLLM generation"
        )


def run_evaluate_bird(state: Dict[str, Any], writer=None) -> Dict[str, Any]:
    """Grade generated Text2SQL JSONL; return paths and fraction-valued pass@k."""
    judger = state.get("judger") or {}
    problem_path = Path(str(judger.get("eval_problem_path") or "")).expanduser().resolve()
    if not problem_path.is_file():
        raise FileNotFoundError(f"Text2SQL problem file is missing: {problem_path}")
    database_setting = judger.get("eval_text2sql_dir")
    if not database_setting:
        raise ValueError("Text2SQL bench needs text2sql_dir")
    database_root = Path(str(database_setting)).expanduser().resolve()
    if not database_root.is_dir():
        raise FileNotFoundError(f"Text2SQL database directory is missing: {database_root}")

    task_id = str(state.get("task_id") or "task")
    version_id = str(getattr(writer, "version_id", None) or state.get("version_id") or "run")
    bench_name = str(judger.get("bench_name") or problem_path.stem)
    bench_dir = (
        Path(str(state.get("output_dir") or "./outputs")).expanduser().resolve()
        / task_id / "judger" / version_id / bench_name
    )
    raw_samples = judger.get("output_case_path")
    samples = (
        Path(str(raw_samples)).expanduser().resolve() if raw_samples
        else bench_dir / f"{bench_name}_sample.jsonl"
    )
    result_path = bench_dir / f"{bench_name}_result.jsonl"
    summary_path = bench_dir / f"{bench_name}_summary.json"
    bench_dir.mkdir(parents=True, exist_ok=True)
    # A failed rerun must never leave a previous score looking current.
    result_path.unlink(missing_ok=True)
    summary_path.unlink(missing_ok=True)

    if not samples.is_file():
        raise FileNotFoundError(f"Generated Text2SQL samples are missing: {samples}")
    expected_questions, expected_samples = _check_sample_coverage(
        problem_path, samples, int(judger.get("eval_case_num", 1))
    )
    _check_model_output_health(samples)

    docker = _docker_executable()
    environment = _docker_environment(docker)
    _ensure_bird_eval_image(docker, environment, writer)
    command = [docker, "run", "--rm", "--network", "none"]
    if hasattr(os, "getuid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    command.extend([
        "--mount", f"type=bind,source={samples},target=/input/samples.jsonl,readonly",
        "--mount", f"type=bind,source={database_root},target=/databases,readonly",
        "--mount", f"type=bind,source={bench_dir},target=/output",
        BIRD_EVAL_IMAGE,
        "--samples", "/input/samples.jsonl",
        "--databases", "/databases",
        "--results", f"/output/{result_path.name}",
        "--summary", f"/output/{summary_path.name}",
    ])
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(
            current=state.get("current", "judger"), progress=0.0,
            message="正在容器内判分 Text2SQL 样本",
            data={"image": BIRD_EVAL_IMAGE, "sample_path": str(samples)},
        ))
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL, env=environment)

    if not result_path.is_file() or not summary_path.is_file():
        raise RuntimeError("BIRD evaluator finished without result or summary file")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = summary.get("pass_at_k")
    if (not isinstance(metrics, dict) or "pass@1" not in metrics
            or summary.get("num_questions") != expected_questions
            or summary.get("num_samples") != expected_samples):
        raise ValueError(f"BIRD evaluator returned incomplete or mismatched summary: {summary_path}")
    return {
        "result_path": str(result_path),
        "summary_path": str(summary_path),
        "metrics": metrics,
        "summary": summary,
    }
