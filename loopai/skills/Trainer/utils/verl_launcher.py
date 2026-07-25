from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from loopai.common.tracking import assert_no_retired_tracking, strip_retired_tracking_environment


_ALLOWED_ENTRYPOINTS = {
    "verl.trainer.main_ppo",
    "verl.trainer.main_ppo_sync",
}
_OVERRIDE_KEY = re.compile(r"^\+?[A-Za-z0-9_.-]+$")
_DICT_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")


def load_verl_launch_config(config_path: str) -> Dict[str, Any]:
    """Load and validate LoopAI's user-approved Verl launch specification."""
    path = Path(config_path).expanduser().resolve()
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError(f"Verl launch config must be YAML: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Verl launch config does not exist: {path}")

    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"Verl launch config must contain a YAML mapping: {path}")
    assert_no_retired_tracking(config)
    if config.get("framework") != "verl" or config.get("stage") != "grpo":
        raise ValueError("Verl launch config must declare framework: verl and stage: grpo")

    entrypoint = str(config.get("entrypoint") or "")
    if entrypoint not in _ALLOWED_ENTRYPOINTS:
        raise ValueError(
            f"Unsupported Verl entrypoint: {entrypoint}; "
            f"allowed values: {', '.join(sorted(_ALLOWED_ENTRYPOINTS))}"
        )
    overrides = config.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError("Verl launch config must contain non-empty overrides")
    invalid_keys = [str(key) for key in overrides if not _OVERRIDE_KEY.fullmatch(str(key))]
    if invalid_keys:
        raise ValueError(f"Invalid Hydra override keys: {', '.join(invalid_keys)}")
    result_config = config.get("result")
    if isinstance(result_config, dict) and result_config.get("selection_mode") is not None:
        selection_mode = str(result_config["selection_mode"]).strip().lower()
        if selection_mode not in {"max", "min"}:
            raise ValueError(
                "Verl result.selection_mode must be max or min, "
                f"got: {result_config['selection_mode']}"
            )
        result_config["selection_mode"] = selection_mode
    return config


def _hydra_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(_hydra_value(item) for item in value) + "]"
    if isinstance(value, dict):
        invalid_keys = [str(key) for key in value if not _DICT_KEY.fullmatch(str(key))]
        if invalid_keys:
            raise ValueError(
                "Hydra mapping keys may contain only letters, numbers, dots, underscores, and hyphens: "
                + ", ".join(invalid_keys)
            )
        return "{" + ",".join(
            f"{key}:{_hydra_value(item)}" for key, item in value.items()
        ) + "}"
    return json.dumps(str(value), ensure_ascii=False)


def _resolve_python_command(env_path: str | None) -> Tuple[list[str], str | None]:
    if not env_path:
        return [sys.executable], None

    raw = str(env_path).strip()
    requested = Path(raw).expanduser()
    if requested.is_absolute() or "/" in raw:
        requested = requested.resolve()
        bin_dir = requested if requested.name == "bin" else requested / "bin"
        env_root = bin_dir.parent
        for name in ("python", "python3"):
            candidate = bin_dir / name
            if candidate.is_file():
                return [str(candidate)], str(env_root)
        raise FileNotFoundError(f"No python executable found under Verl environment: {bin_dir}")

    conda_from_path = shutil.which("conda")
    conda_candidates = (
        os.getenv("CONDA_EXE"),
        str(Path.home() / "miniconda3" / "bin" / "conda"),
        str(Path.home() / "anaconda3" / "bin" / "conda"),
    )
    conda = conda_from_path or next(
        (candidate for candidate in conda_candidates if candidate and Path(candidate).is_file()),
        None,
    )
    if not conda:
        raise FileNotFoundError(
            f"Conda executable was not found while resolving Verl environment '{raw}'"
        )
    return [conda, "run", "--no-capture-output", "-n", raw, "python"], None


def build_verl_launch(
    config_path: str,
    app_config: Dict[str, Any] | None = None,
) -> tuple[list[str], str, dict[str, str]]:
    """Translate an approved YAML spec into a shell-free Verl Hydra command."""
    config = load_verl_launch_config(config_path)
    app_config = app_config or {}
    environment = config.get("environment") or {}

    verl_dir = Path(str(
        environment.get("verl_dir") or app_config.get("verl_dir") or ""
    )).expanduser().resolve()
    if not (verl_dir / "verl" / "trainer").is_dir():
        raise FileNotFoundError(f"Invalid Verl repository directory: {verl_dir}")

    python_command, env_root = _resolve_python_command(
        str(environment.get("verl_env_path") or app_config.get("verl_env_path") or "") or None
    )
    cmd = [*python_command, "-m", str(config["entrypoint"])]
    cmd.extend(
        f"{key}={_hydra_value(value)}"
        for key, value in (config.get("overrides") or {}).items()
    )

    env = strip_retired_tracking_environment(os.environ)
    cuda_devices = environment.get("cuda_visible_devices") or app_config.get("CUDA_VISIBLE_DEVICES")
    if cuda_devices is not None and str(cuda_devices).strip():
        env["CUDA_VISIBLE_DEVICES"] = str(cuda_devices)
    env["PYTHONNOUSERSITE"] = "True"
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(verl_dir), env.get("PYTHONPATH", "")) if item
    )
    if env_root:
        env["PATH"] = os.pathsep.join(
            item for item in (str(Path(env_root) / "bin"), env.get("PATH", "")) if item
        )
    metrics_jsonl = str(environment.get("metrics_jsonl") or "").strip()
    if metrics_jsonl:
        metrics_path = Path(metrics_jsonl).expanduser().resolve()
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        env["VERL_FILE_LOGGER_PATH"] = str(metrics_path)

    return cmd, str(verl_dir), env
