# ObtainerCLI First Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first usable `obtainercli` in a parallel package without deleting or replacing the existing LangGraph Obtainer.

**Architecture:** Add `loopai/obtainercli/` as a standalone local lakehouse CLI. The first version uses a file-backed catalog with JSONL tables and an Iceberg-ready table boundary so the command contract, schemas, idempotency, locking, tagging, lineage, quality findings, and sampling behavior can be tested before swapping the storage backend to PyIceberg. The repo keeps only `.loopai/lake.yaml` as a pointer to an external lake root.

**Tech Stack:** Python standard library, `argparse`, JSONL table files, pytest tests. No LangGraph dependency in the new package.

---

### File Structure

- Create `loopai/obtainercli/__init__.py`: package marker and version.
- Create `loopai/obtainercli/__main__.py`: enables `python -m loopai.obtainercli`.
- Create `loopai/obtainercli/cli.py`: argparse command surface and JSON output.
- Create `loopai/obtainercli/config.py`: lake root resolution, pointer config read/write.
- Create `loopai/obtainercli/errors.py`: typed CLI errors and exit codes.
- Create `loopai/obtainercli/lock.py`: local single-writer commit lock.
- Create `loopai/obtainercli/tables.py`: JSONL table append/read helpers.
- Create `loopai/obtainercli/models.py`: canonical record, IDs, tag parsing.
- Create `loopai/obtainercli/lake_init.py`: `lake init`.
- Create `loopai/obtainercli/ingest.py`: `ingest path`.
- Create `loopai/obtainercli/sample.py`: tag/core-column sampling and export.
- Modify `setup.py`: add `obtainercli` console entry point.
- Create `tests/test_obtainercli_lake.py`: integration-style CLI/core tests.

### Task 1: Baseline Tests

**Files:**
- Create: `tests/test_obtainercli_lake.py`

- [ ] **Step 1: Write failing tests**

Test lake init, pointer config, idempotent ingest, tags, dedup semantics, and allow-smaller sample warnings.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_obtainercli_lake.py`

Expected: import failure for `loopai.obtainercli`.

- [ ] **Step 3: Commit after green implementation**

Commit message: `feat: add obtainercli local lake core`

### Task 2: Local Lake Core

**Files:**
- Create: all `loopai/obtainercli/*.py` files listed above
- Modify: `setup.py`
- Test: `tests/test_obtainercli_lake.py`

- [ ] **Step 1: Implement minimal code to satisfy Task 1 tests**

Implement only JSONL input, local file-backed tables, deterministic IDs, file lock, and random sample with tag/core filters.

- [ ] **Step 2: Run focused tests**

Run: `pytest -q tests/test_obtainercli_lake.py`

Expected: all tests pass.

- [ ] **Step 3: Commit**

Commit message: `feat: add obtainercli local lake core`

### Task 3: Existing Sampling Regression

**Files:**
- Test: `tests/test_jsonl_dataset_sampling.py`

- [ ] **Step 1: Run existing related tests**

Run: `pytest -q tests/test_jsonl_dataset_sampling.py tests/test_obtainercli_lake.py`

Expected: all tests pass.

- [ ] **Step 2: Commit only if changes are needed**

Commit message if needed: `test: cover obtainercli sampling regressions`

### Task 4: Final Verification

**Files:**
- Check all new files and staged diff.

- [ ] **Step 1: Run final focused verification**

Run: `pytest -q tests/test_obtainercli_lake.py tests/test_jsonl_dataset_sampling.py`

Expected: all tests pass.

- [ ] **Step 2: Check git status**

Run: `git status --short`

Expected: clean after commits.
