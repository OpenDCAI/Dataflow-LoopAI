"""Docker adapter for math evaluation.

Math parsing, generation and grading intentionally remain in the evaluator
image.  This module only validates paths, maps Judger state to the existing
``evaluate_math.py`` CLI, and returns the generated result file.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict


MATH_EVAL_IMAGE = "math-eval-loopai"
MATH_EVAL_CONTEXT = Path(__file__).resolve().parent.parent / "docker" / "math_eval"


def _bool_arg(command: list[str], value: Any) -> None:
    if value is True:
        command.append("--enable_thinking")
    elif value is False:
        command.append("--no_thinking")


def _ensure_math_eval_image(writer=None) -> None:
    """Ensure the bundled math evaluator image exists on the Docker host."""
    inspect = subprocess.run(
        ["docker", "image", "inspect", MATH_EVAL_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if inspect.returncode == 0:
        return
    if not MATH_EVAL_CONTEXT.is_dir():
        raise FileNotFoundError(f"Math evaluator Docker context does not exist: {MATH_EVAL_CONTEXT}")
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(
            current="judger", progress=0.0,
            message="未找到数学评测镜像，正在自动构建",
            data={"image": MATH_EVAL_IMAGE, "context": str(MATH_EVAL_CONTEXT)},
        ))
    build_command = [
        "docker", "build", "--network", "host", "-t", MATH_EVAL_IMAGE,
    ]
    # Forward the host's package/proxy settings when present. This is useful on
    # hosts where Docker's default bridge network cannot reach the package index.
    for name in ("PIP_INDEX_URL", "PIP_TRUSTED_HOST", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        value = os.getenv(name)
        if value:
            build_command.extend(["--build-arg", f"{name}={value}"])
    build_command.append(str(MATH_EVAL_CONTEXT))
    subprocess.run(build_command, check=True)


def _assert_model_is_served(expected: str) -> None:
    """确认 vLLM 确实按这个名字上架，否则立刻给可读报错。

    模型名由 Judger 与 vLLM 共同约定：runtime_config 决定名字，vllm_starter 用
    同一个值传 ``--served-model-name``。两边一旦不一致，vLLM 会对*每一条*请求回
    404，而报错发生在容器内部、还要重试若干次才抛出，极难定位；这里提前拦住。
    """
    try:
        with urllib.request.urlopen("http://127.0.0.1:8911/v1/models", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return  # 查不到就不拦，让真正的调用去暴露问题
    served = [str(item.get("id")) for item in (payload.get("data") or []) if item.get("id")]
    if served and expected not in served:
        raise ValueError(
            f"eval_model_name={expected!r} 不在 vLLM 上架的名字里：{served}。"
            f"请检查 eval_model_path / eval_model_name 配置。"
        )


def run_evaluate_math(state: Dict[str, Any], writer=None) -> Dict[str, Any]:
    """Run the reusable math evaluator container for one math bench."""
    judger = state.get("judger") or {}
    dataset_path = Path(str(judger.get("eval_problem_path") or "")).expanduser().resolve()
    if not dataset_path.is_file():
        raise FileNotFoundError(f"Math dataset does not exist: {dataset_path}")

    image = MATH_EVAL_IMAGE
    # Math always talks to the vLLM instance started by the Judger pipeline.
    base_url = "http://127.0.0.1:8911/v1"
    # 名字由 resolve_judger_runtime_config 统一给出（未配置时取模型路径最后一段），
    # 与 vllm_starter 传给 vLLM 的 --served-model-name 是同一个值，不再靠猜。
    model_name = str(judger.get("eval_model_name") or "").strip()
    if not model_name:
        raise ValueError("eval_model_name is required for math evaluation")
    _assert_model_is_served(model_name)

    task_id = str(state.get("task_id") or "task")
    bench_name = str(judger.get("bench_name") or dataset_path.stem)
    version_id = str(getattr(writer, "version_id", None) or "run")
    output_dir = (Path(str(state.get("output_dir") or "./outputs")).expanduser().resolve()
                  / task_id / "judger" / version_id / bench_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    host_result = output_dir / f"{bench_name}_result.json"

    # Use a fixed container path so arbitrary host paths do not leak into the
    # evaluator arguments. The evaluator owns all extraction/grading logic.
    container_dataset = f"/data/math_dataset{dataset_path.suffix.lower()}"
    command = [
        "docker", "run", "--rm",
        "--network", str(judger.get("eval_docker_network") or "host"),
        "-v", f"{dataset_path}:{container_dataset}:ro",
        "-v", f"{output_dir}:/outputs",
        image,
        "--vllm_base_url", base_url,
        "--model", model_name,
        "--dataset_path", container_dataset,
        "--max_new_tokens", str(judger.get("eval_max_tokens", 38912)),
        "--temperature", str(judger.get("eval_temperature", 1.0)),
        "--top_p", str(judger.get("eval_top_p", 0.95)),
        "--top_k", str(judger.get("eval_top_k", -1)),
        "--min_p", str(judger.get("eval_min_p", 0.0)),
        "--presence_penalty", str(judger.get("eval_presence_penalty", 0.0)),
        "--val_n", str(judger.get("eval_case_num", 1)),
        "--output_file", "/outputs/result.json",
    ]
    if judger.get("eval_api_key"):
        command += ["--api_key", str(judger["eval_api_key"])]
    if judger.get("eval_request_timeout") is not None:
        command += ["--request_timeout", str(judger["eval_request_timeout"])]
    if judger.get("eval_max_retries") is not None:
        command += ["--max_retries", str(judger["eval_max_retries"])]
    _bool_arg(command, judger.get("eval_enable_thinking"))

    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current=state.get("current", "judger"), progress=0.0,
                           message="正在启动数学评测容器", data={"image": image, "command": command}))
    _ensure_math_eval_image(writer)
    subprocess.run(command, check=True)

    container_result = output_dir / "result.json"
    if not container_result.is_file():
        raise RuntimeError(f"Math evaluator completed without result file: {container_result}")
    container_result.replace(host_result)
    payload = json.loads(host_result.read_text(encoding="utf-8"))
    val_n = int(judger.get("eval_case_num", 1))
    metrics = {
        f"pass@{val_n}": payload.get("pass_at_n_pct", 0.0),
        f"average@{val_n}": payload.get("average_at_n_pct", 0.0),
        f"majority_vote@{val_n}": payload.get("majority_vote_at_n_pct", 0.0),
        "format_rate": payload.get("format_rate", 0.0),
    }
    if writer:
        writer(StreamEvent(current=state.get("current", "judger"), progress=1.0,
                           message="数学评测完成", data={"result_path": str(host_result), "metrics": metrics}))
    return {"result_path": str(host_result), "metrics": metrics, "summary": payload}
