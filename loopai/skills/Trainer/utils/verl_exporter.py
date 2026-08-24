from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

from .verl_launcher import build_verl_launch


def _has_model_weights(path: Path) -> bool:
    return any(
        (path / name).is_file()
        for name in (
            "model.safetensors",
            "model.safetensors.index.json",
            "pytorch_model.bin",
            "pytorch_model.bin.index.json",
        )
    )


def export_verl_checkpoint(
    config_path: str,
    checkpoint: Dict[str, Any],
    log_path: str,
    app_config: Dict[str, Any] | None = None,
) -> str:
    """Merge the selected Verl FSDP actor into one Hugging Face model directory."""
    checkpoint_root = Path(str(checkpoint.get("checkpoint_path") or "")).expanduser().resolve()
    actor_dir = checkpoint_root / "actor"
    if not actor_dir.is_dir():
        raise FileNotFoundError(f"Selected Verl actor checkpoint does not exist: {actor_dir}")

    target_dir = checkpoint_root / "merged_huggingface"
    if target_dir.is_dir() and _has_model_weights(target_dir):
        return str(target_dir)

    training_cmd, cwd, env = build_verl_launch(config_path, app_config)
    try:
        module_index = training_cmd.index("-m")
    except ValueError as exc:
        raise RuntimeError("Unable to derive Verl Python command from launch config") from exc
    cmd = [
        *training_cmd[:module_index],
        "-m",
        "verl.model_merger",
        "merge",
        "--backend",
        "fsdp",
        "--local_dir",
        str(actor_dir),
        "--target_dir",
        str(target_dir),
    ]
    output_log = Path(log_path).expanduser().resolve()
    output_log.parent.mkdir(parents=True, exist_ok=True)
    with output_log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Verl checkpoint merge exited with code {completed.returncode}; see {output_log}"
        )
    if not _has_model_weights(target_dir):
        raise RuntimeError(f"Verl merger completed but no Hugging Face weights were found in {target_dir}")
    return str(target_dir)
