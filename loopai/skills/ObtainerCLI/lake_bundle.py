"""Lake consolidation, bundle export/import and data import.

Canonical layout (the only two places obtainer data lives):

    <project>/.datamixer/            data lake
      lake.yaml                      lake pointer
      warehouse/                     datamixer warehouse (catalog/blobs/index/
                                     exports/snapshots/lineage/dataset_cards/audit)
    <project>/outputs/obtainer/      obtainer assets
      runs/<run_id>/                 orchestrator/acquisition/dataflow/sft-export runs
      dataflow_work/                 dataflow chunked runner work
      events/<task_id>/obtainercli/  obtainercli event stream
      .codex/{worker,dataflow,orchestrator,sft}/  obtainer agent Codex homes

``codex_home`` (starter) stays at the repository root and is never bundled.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

from .errors import ObtainerCliError

CANONICAL_LAKE_DIR = ".datamixer"
CANONICAL_OBTAINER_DIR = "outputs/obtainer"
BUNDLE_SCHEMA_VERSION = 1

# warehouse sub-paths that are runtime state (excluded from bundles)
WAREHOUSE_RUNTIME_SUBPATHS = {
    ".loopai", "llm_cache", "locks", "runs", "webagent_campaigns",
    "webcrawler_dm_runs", "pipeline_queue.sqlite", "webagent_queue.sqlite",
}
# run-dir file names that are runtime state (excluded from bundles)
RUN_RUNTIME_NAMES = {
    "thread.json", "status.json", "logs", "worker_prompt.md",
    "resume_prompt.md", "worker_prompt", "worker_stdout.ndjson", "worker_stderr.log",
}
TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".md", ".txt", ".log", ".toml", ".cfg", ".ini"}


def canonical_lake_dir(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / CANONICAL_LAKE_DIR


def canonical_obtainer_dir(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / CANONICAL_OBTAINER_DIR


def resolve_active_lake(
    project_root: str | Path,
    link: str | Path | None = None,
) -> tuple[Path, Path]:
    """Resolve (pointer_path, warehouse_path) for the active lake.

    The canonical pointer is ``<project>/.datamixer/lake.yaml``.
    """
    project = Path(project_root).expanduser().resolve()
    if link:
        pointer = Path(link).expanduser()
        if not pointer.is_absolute():
            pointer = project / pointer
        pointer = pointer.resolve()
    else:
        pointer = canonical_lake_dir(project) / "lake.yaml"
    if not pointer.is_file():
        raise ObtainerCliError(
            "DATAMIXER_LAKE_POINTER_NOT_FOUND",
            f"No lake pointer found: {pointer}",
            hint="Initialize or consolidate the lake first (`dm lake init` / `dm lake consolidate`).",
            exit_code=2,
        )
    from .config import read_lake_config

    config = read_lake_config(pointer)
    warehouse = Path(str(config.get("warehouse") or "")).expanduser()
    if not warehouse.is_absolute():
        warehouse = (project / warehouse).resolve()
    warehouse = warehouse.resolve()
    if not (warehouse / "datamixer.toml").is_file():
        raise ObtainerCliError(
            "DATAMIXER_WAREHOUSE_NOT_FOUND",
            f"Lake pointer {pointer} resolves to a warehouse without datamixer.toml: {warehouse}",
            hint="Run `dm lake load --warehouse <warehouse>` first.",
            exit_code=2,
        )
    return pointer, warehouse


def _counts(warehouse: Path) -> dict[str, Any]:
    from loopai.agents.Obtainer.datamixer.store import DataStore

    store = DataStore.open(warehouse)
    try:
        datasets = store.catalog.list_datasets()
        return {
            "datasets": len(datasets),
            "records": store.catalog.count(),
            "by_dataset": {ds["name"]: int(ds["n_samples"]) for ds in datasets},
        }
    finally:
        store.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# path rewriting (absolute paths embedded in metadata)
# ---------------------------------------------------------------------------

def _is_rewritable(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in TEXT_SUFFIXES


def _rewrite_text(text: str, mapping: list[tuple[str, str]]) -> str:
    for old, new in mapping:
        if old and old != new:
            text = text.replace(old, new)
    return text


def rewrite_tree(root: Path, mapping: list[tuple[str, str]]) -> int:
    """Rewrite old->new absolute paths inside text metadata files under root."""
    count = 0
    for path in sorted(root.rglob("*")):
        if not _is_rewritable(path):
            continue
        try:
            original = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rewritten = _rewrite_text(original, mapping)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8")
            count += 1
    return count


# ---------------------------------------------------------------------------
# consolidate: scattered -> canonical
# ---------------------------------------------------------------------------

def _move(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    src.rename(dst)
    return True


def _write_canonical_pointer(project: Path, warehouse: Path) -> Path:
    pointer = canonical_lake_dir(project) / "lake.yaml"
    canonical_lake_dir(project).mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        "# LoopAI ObtainerCLI lake pointer\n"
        f"root: {canonical_lake_dir(project)}\n"
        f"warehouse: {warehouse}\n"
        "catalog: datamixer\n"
        "backend: datamixer\n"
        "namespace: loopai\n"
        "auto_embed: false\n",
        encoding="utf-8",
    )
    return pointer


def consolidate(project_root: str | Path) -> dict[str, Any]:
    """Move scattered lake/obtainer assets into .datamixer/ + outputs/obtainer/."""
    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise ObtainerCliError("PROJECT_NOT_FOUND", f"Project root not found: {project}", exit_code=2)

    lake_dir = canonical_lake_dir(project)
    obtainer_dir = canonical_obtainer_dir(project)
    mapping: list[tuple[str, str]] = []
    moved: list[str] = []

    # 1. lake: legacy pointer (.loopai/lake.yaml) + warehouse (lake/ or root)
    legacy_pointer = project / ".loopai" / "lake.yaml"
    warehouse = None
    if (lake_dir / "warehouse" / "datamixer.toml").is_file():
        warehouse = lake_dir / "warehouse"
    else:
        for candidate in (
            project / "lake" / "warehouse",
            project / "lake",
            project / "warehouse",
        ):
            if (candidate / "datamixer.toml").is_file():
                warehouse = candidate
                break
        if warehouse is None and legacy_pointer.is_file():
            from .config import read_lake_config

            cfg = read_lake_config(legacy_pointer)
            w = Path(str(cfg.get("warehouse") or "")).expanduser()
            if not w.is_absolute():
                w = project / w
            if (w / "datamixer.toml").is_file():
                warehouse = w.resolve()

    if warehouse is not None and warehouse != (lake_dir / "warehouse"):
        _move(warehouse, lake_dir / "warehouse")
        mapping.append((str(warehouse), str(lake_dir / "warehouse")))
        # legacy lake root -> .datamixer
        legacy_lake_root = warehouse.parent
        if legacy_lake_root.name == "lake":
            mapping.append((str(legacy_lake_root), str(lake_dir)))
        moved.append("lake")

    # duplicate pointer at legacy lake root
    dup_pointer = project / "lake" / "lake.yaml"
    if dup_pointer.is_file():
        dup_pointer.unlink()

    # write canonical pointer
    if warehouse is None and (lake_dir / "warehouse" / "datamixer.toml").is_file():
        warehouse = lake_dir / "warehouse"
    if warehouse is not None:
        pointer = _write_canonical_pointer(project, warehouse)
        moved.append(str(pointer.relative_to(project)))
    elif legacy_pointer.is_file():
        pointer = _write_canonical_pointer(project, legacy_pointer.parent / "warehouse")
        moved.append(str(pointer.relative_to(project)))
    # remove legacy .loopai once its pointer is gone
    if legacy_pointer.is_file():
        legacy_pointer.unlink()
    if (project / ".loopai").exists() and not list((project / ".loopai").iterdir()):
        (project / ".loopai").rmdir()

    # 2. obtainer run dirs -> outputs/obtainer/runs/<name>
    outputs = project / "outputs"
    if outputs.is_dir():
        run_patterns = ("obtainer_run", "obtainer_", "sft_coding_obtainer_run", "code_eval_acquisition_run")
        for child in sorted(outputs.iterdir()):
            if not child.is_dir():
                continue
            if not any(child.name.startswith(p) for p in run_patterns):
                continue
            target = obtainer_dir / "runs" / child.name
            _move(child, target)
            mapping.append((str(child), str(target)))
            moved.append(f"runs/{child.name}")

        # 3. obtainercli events under outputs/<task_id>/obtainercli -> outputs/obtainer/events/<task_id>/obtainercli
        for child in sorted(outputs.iterdir()):
            if not child.is_dir() or not (child / "obtainercli").is_dir():
                continue
            src = child / "obtainercli"
            dst = obtainer_dir / "events" / child.name / "obtainercli"
            _move(src, dst)
            mapping.append((str(src), str(dst)))
            moved.append(f"events/{child.name}")

    # 4. dataflow_work -> outputs/obtainer/dataflow_work
    dfw = project / "dataflow_work"
    if dfw.is_dir():
        target = obtainer_dir / "dataflow_work"
        _move(dfw, target)
        mapping.append((str(dfw), str(target)))
        moved.append("dataflow_work")

    # 5. obtainer codex homes -> outputs/obtainer/.codex/{worker,dataflow}
    for name, sub in (("codex_home_worker", "worker"), ("codex_home_dataflow", "dataflow")):
        src = project / name
        if src.is_dir():
            dst = obtainer_dir / ".codex" / sub
            _move(src, dst)
            mapping.append((str(src), str(dst)))
            moved.append(f".codex/{sub}")

    # 6. rewrite absolute paths in moved metadata (project root unchanged, but
    #    the lake/run locations changed)
    rewritten = 0
    for root in (lake_dir, obtainer_dir):
        if root.is_dir():
            rewritten += rewrite_tree(root, mapping)

    return {
        "ok": True,
        "command": "dm.lake.consolidate",
        "project_root": str(project),
        "moved": moved,
        "rewritten_files": rewritten,
        "canonical_lake": str(lake_dir),
        "canonical_obtainer": str(obtainer_dir),
    }


# ---------------------------------------------------------------------------
# bundle export / import (tar.gz, digital assets only)
# ---------------------------------------------------------------------------

def _bundle_filter(path: Path, *, scope: str, include_runtime: bool) -> bool:
    rel = Path(path)
    parts = rel.parts
    if rel.name in {"__pycache__", ".git"}:
        return False
    if include_runtime:
        # even a runtime bundle skips agent codex homes, event logs and caches
        return not (".codex" in parts or parts[:1] == ("events",))
    if scope == "warehouse":
        if any(part in WAREHOUSE_RUNTIME_SUBPATHS for part in parts):
            return False
    else:  # obtainer scope
        if parts[:1] in {("events",), ("logs",), (".codex",), ("dataflow_work",)}:
            return False
        if "downloads" in parts:
            return False
        if rel.name in RUN_RUNTIME_NAMES or rel.name in {"worker_prompt.md", "resume_prompt.md", "policy.md"}:
            return False
        if rel.name == "monitor_state.json" or rel.name == "campaign_logs":
            return False
    return True


def export_bundle(
    project_root: str | Path,
    out_file: str | Path,
    *,
    link: str | Path | None = None,
    include_runtime: bool = False,
) -> dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    pointer, warehouse = resolve_active_lake(project, link)
    counts = _counts(warehouse)
    out = Path(out_file).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(out, "w:gz") as tf:
        # manifest first (small, easy to validate on import)
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "created_at": time.time(),
            "source_root": str(project),
            "datasets": counts["datasets"],
            "records": counts["records"],
            "by_dataset": counts["by_dataset"],
            "catalog_sha256": _sha256(warehouse / "catalog.db"),
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        _tar_add_bytes(tf, "bundle/manifest.json", manifest_bytes)

        # .datamixer/lake.yaml (pointer) + warehouse digital assets
        _tar_add_file(tf, pointer, "bundle/.datamixer/lake.yaml")
        _tar_add_tree(tf, warehouse, "bundle/.datamixer/warehouse", _bundle_filter, scope="warehouse", include_runtime=include_runtime)

        # outputs/obtainer digital assets
        obtainer = canonical_obtainer_dir(project)
        if obtainer.is_dir():
            _tar_add_tree(tf, obtainer, "bundle/outputs/obtainer", _bundle_filter, scope="obtainer", include_runtime=include_runtime)

    return {
        "ok": True,
        "command": "dm.lake.export-bundle",
        "out": str(out),
        "bytes": out.stat().st_size,
        "datasets": counts["datasets"],
        "records": counts["records"],
        "include_runtime": include_runtime,
    }


def _tar_add_bytes(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    tf.addfile(info, io.BytesIO(data))


def _tar_add_file(tf: tarfile.TarFile, path: Path, name: str) -> None:
    info = tf.gettarinfo(str(path), arcname=name)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    with path.open("rb") as handle:
        tf.addfile(info, handle)


def _tar_add_tree(tf: tarfile.TarFile, root: Path, prefix: str, filt, *, scope: str, include_runtime: bool) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if not filt(rel, scope=scope, include_runtime=include_runtime):
            continue
        _tar_add_file(tf, path, f"{prefix}/{rel.as_posix()}")


def import_bundle(bundle_file: str | Path, target_root: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_file).expanduser()
    target = Path(target_root).expanduser().resolve()
    if not bundle.is_file():
        raise ObtainerCliError("BUNDLE_NOT_FOUND", f"Bundle file not found: {bundle}", exit_code=2)
    target.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {}
    with tarfile.open(bundle, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        names = {m.name for m in members}
        if "bundle/manifest.json" not in names:
            raise ObtainerCliError("BUNDLE_MANIFEST_MISSING", "Bundle has no manifest.json", exit_code=2)
        manifest = json.loads(tf.extractfile("bundle/manifest.json").read().decode("utf-8"))  # type: ignore[union-attr]
        if int(manifest.get("schema_version") or 0) != BUNDLE_SCHEMA_VERSION:
            raise ObtainerCliError(
                "BUNDLE_SCHEMA_MISMATCH",
                f"Bundle schema {manifest.get('schema_version')} != {BUNDLE_SCHEMA_VERSION}",
                exit_code=2,
            )
        tf.extractall(target, filter="data")  # type: ignore[call-arg]

    # move extracted bundle dirs into the canonical locations
    extracted = target / "bundle"
    src_lake = extracted / ".datamixer"
    src_obtainer = extracted / "outputs" / "obtainer"
    dst_lake = canonical_lake_dir(target)
    dst_obtainer = canonical_obtainer_dir(target)
    dst_lake.parent.mkdir(parents=True, exist_ok=True)
    dst_obtainer.parent.mkdir(parents=True, exist_ok=True)
    if src_lake.is_dir():
        if dst_lake.exists():
            shutil.rmtree(dst_lake, ignore_errors=True)
        src_lake.rename(dst_lake)
    if src_obtainer.is_dir():
        if dst_obtainer.exists():
            shutil.rmtree(dst_obtainer, ignore_errors=True)
        src_obtainer.rename(dst_obtainer)

    # rewrite absolute paths: old source_root -> new target root
    source_root = str(manifest.get("source_root") or "")
    mapping = [(source_root, str(target))] if source_root else []
    rewritten = 0
    for root in (dst_lake, dst_obtainer):
        if root.is_dir():
            rewritten += rewrite_tree(root, mapping)

    warehouse = dst_lake / "warehouse"
    if not (warehouse / "datamixer.toml").is_file():
        raise ObtainerCliError("BUNDLE_WAREHOUSE_INVALID", f"Bundle did not restore a valid warehouse at {warehouse}", exit_code=2)
    pointer = _write_canonical_pointer(target, warehouse)
    # pointer paths already canonical; ensure warehouse line is exact
    counts = _counts(warehouse)
    expected = {
        "datasets": int(manifest.get("datasets") or -1),
        "records": int(manifest.get("records") or -1),
    }
    if expected["datasets"] >= 0 and counts["datasets"] != expected["datasets"]:
        raise ObtainerCliError(
            "BUNDLE_COUNT_MISMATCH",
            f"Restored dataset count {counts['datasets']} != manifest {expected['datasets']}",
            exit_code=2,
        )
    catalog_sha = _sha256(warehouse / "catalog.db")
    if manifest.get("catalog_sha256") and manifest["catalog_sha256"] != catalog_sha:
        raise ObtainerCliError(
            "BUNDLE_CATALOG_CHECKSUM_MISMATCH",
            "Restored catalog.db checksum differs from the bundle manifest",
            exit_code=2,
        )
    if (extracted / "outputs").exists() and not list((extracted / "outputs").iterdir()):
        shutil.rmtree(extracted / "outputs", ignore_errors=True)
    shutil.rmtree(extracted, ignore_errors=True)

    return {
        "ok": True,
        "command": "dm.lake.import-bundle",
        "target_root": str(target),
        "pointer": str(pointer),
        "warehouse": str(warehouse),
        "datasets": counts["datasets"],
        "records": counts["records"],
        "rewritten_files": rewritten,
    }


# ---------------------------------------------------------------------------
# data import (incremental / batch)
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    import json as _json

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = _json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path.name} line is not a JSON object")
            rows.append(row)
    return rows


def _register_dataset_card(warehouse: Path, dataset: str, card: Path | None) -> str | None:
    if card is None or not card.is_file():
        return None
    cards_dir = warehouse / "dataset_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    target = cards_dir / f"{dataset}.md"
    target.write_text(card.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target)


def import_data(
    project_root: str | Path,
    package: str | Path,
    *,
    link: str | Path | None = None,
) -> dict[str, Any]:
    """Import a data package into the active lake (merge / batch).

    Package layout:
      manifest.json   {schema_version, datasets: [{file, name, quality_level,
                       domain, stage, lang, source, license, task_type,
                       dataset_card, split}]}
      <file>.jsonl    records with a `content` object + metadata
      dataset_cards/*.md (optional)

    Ingest is idempotent per sample (add_sample keys on dataset+cid+core), so
    re-importing the same package into an existing lake adds 0 new records and
    merging into a populated lake appends only genuinely new samples.
    """
    project = Path(project_root).expanduser().resolve()
    pointer, warehouse = resolve_active_lake(project, link)
    pkg = Path(package).expanduser()
    if not pkg.is_dir():
        raise ObtainerCliError("DATA_PACKAGE_NOT_FOUND", f"Data package directory not found: {pkg}", exit_code=2)
    manifest_path = pkg / "manifest.json"
    if not manifest_path.is_file():
        raise ObtainerCliError("DATA_PACKAGE_MANIFEST_MISSING", f"No manifest.json in package: {pkg}", exit_code=2)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    datasets = manifest.get("datasets") or []
    if not isinstance(datasets, list) or not datasets:
        raise ObtainerCliError("DATA_PACKAGE_EMPTY", "manifest.json declares no datasets", exit_code=2)

    from loopai.agents.Obtainer.datamixer.store import DataStore

    imported = 0
    merged = 0
    per_dataset: dict[str, dict[str, int]] = {}
    store = DataStore.open(warehouse)
    try:
        for entry in datasets:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            file = str(entry.get("file") or "").strip()
            if not name or not file:
                raise ObtainerCliError("DATA_PACKAGE_INVALID_ENTRY", f"Invalid dataset entry: {entry}", exit_code=2)
            data_file = pkg / file
            if not data_file.is_file():
                raise ObtainerCliError("DATA_PACKAGE_FILE_MISSING", f"Missing data file: {data_file}", exit_code=2)
            records = _read_jsonl(data_file)
            defaults: dict[str, Any] = {}
            for key in ("stage", "domain", "lang", "source", "license",
                        "modality", "task_type", "processing_level", "source_kind", "split"):
                if entry.get(key):
                    defaults[key] = entry[key]
            if entry.get("quality_level"):
                defaults["quality_level"] = entry["quality_level"]
            card = pkg / str(entry["dataset_card"]) if entry.get("dataset_card") else None
            card_path = _register_dataset_card(warehouse, name, card)
            ds_id = store.catalog.resolve_dataset(name)
            if ds_id is None:
                ds_id = store.catalog.add_dataset(
                    name=name, source=entry.get("source") or "", license=entry.get("license") or ""
                )
            result = store.ingest_records(
                ds_id, records, defaults=defaults, content_key="content",
                allow_duplicates=False, decontaminate=False,
            )
            imported += int(result.written)
            merged += int(result.merged)
            per_dataset[name] = {
                "written": int(result.written),
                "merged": int(result.merged),
                "dataset_id": ds_id,
            }
            if card_path:
                per_dataset[name]["dataset_card"] = card_path
    finally:
        store.close()

    return {
        "ok": True,
        "command": "dm.lake.import-data",
        "warehouse": str(warehouse),
        "imported": imported,
        "merged": merged,
        "per_dataset": per_dataset,
    }
