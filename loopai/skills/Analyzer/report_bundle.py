"""Portable report filenames and a domain-independent downstream manifest."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


REPORT_FILENAMES = {
    "summary": "01_数据集背景与评测概览.txt",
    "report": "02_完整分析与审计报告.txt",
    "final_report": "03_最终报告.txt",
    "suggestions": "04_模型改进建议.txt",
    "obtainer": "05_数据爬取与构造建议.txt",
    "rollout": "06_Rollout五档能力分析.txt",
    "training": "07_SFT与RL训练阶段评估.txt",
    "training_plan": "08_training_plan.json",
}


def safe_dataset_names(names: list[str]) -> dict[str, str]:
    result, used = {}, set()
    for name in sorted(set(names)):
        slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
        slug = re.sub(r"\s+", "_", slug).strip(" ._")[:100] or "dataset"
        reserved = re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", slug.split(".")[0], re.I)
        if reserved or slug.casefold() in used or slug != name:
            slug += "_" + hashlib.sha256(name.encode()).hexdigest()[:10]
        used.add(slug.casefold())
        result[name] = slug
    return result


def write_report_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(text.replace("\r\n", "\n").replace("\r", "\n"))
    temp.replace(path)


def write_report_bundle(directory: Path, texts: dict, plan: dict) -> dict:
    expected = set(REPORT_FILENAMES) - {"training_plan"}
    if set(texts) != expected or any(not isinstance(t, str) or not t.strip() for t in texts.values()):
        raise ValueError("A report bundle requires seven nonempty text reports")
    if any(type(plan.get(key)) is not bool for key in ("sft_completed", "is_sft", "is_rl")):
        raise ValueError("Training decisions must be JSON booleans")
    paths = {key: str(directory / filename) for key, filename in REPORT_FILENAMES.items()}
    for key, value in texts.items():
        write_report_text(paths[key], value.rstrip() + "\n")
    target = Path(paths["training_plan"])
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(target)
    return paths


def register_report_bundle(cfg: dict, dataset: str, directory: Path, paths: dict) -> None:
    directory = directory.resolve()
    paths = {key: str(Path(path).resolve()) for key, path in paths.items()}
    cfg.setdefault("report_artifacts", {})[dataset] = {
        "schema_version": "1.0", "task_type": cfg.get("analyze_task_type", "math"),
        "dataset": dataset, "directory": str(directory), "files": paths,
    }
    cfg["report_artifact_format"] = "text_with_training_plan"
    cfg["report_bundle_dir"] = str(directory.parent)
    if len(cfg["report_artifacts"]) == 1:
        cfg["report_dataset_dir"] = str(directory)
        cfg["training_plan_path"] = paths["training_plan"]
        cfg["rollout_report_path"] = paths["rollout"]
        cfg["training_stage_report_path"] = paths["training"]
    else:
        for key in ("report_dataset_dir", "training_plan_path", "rollout_report_path", "training_stage_report_path"):
            cfg.pop(key, None)
