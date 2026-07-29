"""LLM-backed operators (L4).

These bind to a model registered in the warehouse **model pool** (by name) and
call it through :mod:`datamixer.llm`, which supports both the ``openaichat`` and
``response`` wire formats -- the format is a per-model config switch, so the
operator code is format-agnostic.

Execution model: an LLM operator processes its input in **chunks of <= 10 items**
and fans the chunks out across a thread pool (LLM calls are I/O-bound), capped by
``max_concurrency`` (default 64). The Operator ``process`` contract is unchanged;
chunking + concurrency happen inside.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .. import llm, schema
from ..utils import extract_text
from .base import Batch, Operator, OperatorContext, OperatorSpec, register

MAX_CHUNK = 10
DEFAULT_CONCURRENCY = 64


class LLMOperator(Operator):
    """Base class: model-pool binding + chunked concurrent execution, with the
    production concerns large-scale LLM annotation needs:

    * **retries** with exponential backoff on transient errors (in ``llm.complete``);
    * **rate limiting** via ``rpm`` (requests/minute), so a 64-wide pool does not
      429 the provider;
    * **result cache / idempotency**: results are keyed by
      ``hash(model, messages)`` and persisted under ``<root>/llm_cache``, so a
      re-run does not re-pay for the same (model, prompt, item);
    * **usage / cost accounting**: calls, cache hits, errors, and estimated
      prompt/completion tokens are aggregated and reported.

    Subclasses implement :meth:`build_messages` and :meth:`apply`.
    """

    def __init__(self, model: str | None = None, chunk_size: int = MAX_CHUNK,
                 max_concurrency: int = DEFAULT_CONCURRENCY, cache: bool = True,
                 rpm: int = 0, max_retries: int = 3,
                 max_input_chars: int = 2000, cost_per_1k: float = 0.0,
                 max_tokens: int | None = None, **_):
        self.model_name = model
        self.chunk_size = max(1, min(int(chunk_size), MAX_CHUNK))
        self.max_concurrency = max(1, int(max_concurrency))
        self.cache_enabled = bool(cache)
        self.interval = 60.0 / float(rpm) if rpm else 0.0
        self.max_retries = int(max_retries)
        self.max_input_chars = int(max_input_chars)
        self.cost_per_1k = float(cost_per_1k)
        self.max_tokens = int(max_tokens or 0)
        self.model_spec = None
        self._cache_dir = None
        self._lock = None
        self._rl_lock = None
        self._next_allowed = 0.0
        self.usage = None

    def setup(self, ctx: OperatorContext) -> None:
        import threading
        from pathlib import Path
        from ..models import ModelPool
        if not self.model_name:
            raise ValueError(
                f"{self.spec.name} requires a 'model' arg naming a model in the "
                f"pool (register one with `datamixer model add`)")
        self.model_spec = ModelPool(ctx.root).get(self.model_name)
        self._lock = threading.Lock()
        self._rl_lock = threading.Lock()
        self.usage = {"calls": 0, "cache_hits": 0, "errors": 0,
                      "prompt_tokens": 0, "completion_tokens": 0}
        if self.cache_enabled and ctx.root:
            self._cache_dir = Path(ctx.root) / "llm_cache"
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # -- to override -------------------------------------------------------
    def build_messages(self, chunk: Batch) -> list[dict]:
        raise NotImplementedError

    def apply(self, chunk: Batch, data: dict) -> None:
        raise NotImplementedError

    # -- engine ------------------------------------------------------------
    def _cache_path(self, messages):
        import hashlib
        key = hashlib.blake2b(
            (self.model_spec.model + "\x00" +
             json.dumps(messages, ensure_ascii=False, sort_keys=True)).encode(),
            digest_size=16).hexdigest()
        return self._cache_dir / f"{key}.txt" if self._cache_dir else None

    def _rate_wait(self):
        if self.interval <= 0:
            return
        import time
        with self._rl_lock:
            now = time.monotonic()
            start = max(now, self._next_allowed)
            self._next_allowed = start + self.interval
        if start - now > 0:
            time.sleep(start - now)

    def _account(self, messages, text, cached):
        from ..utils import estimate_tokens
        pt = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        ct = estimate_tokens(text or "")
        with self._lock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += pt
            self.usage["completion_tokens"] += ct
            if cached:
                self.usage["cache_hits"] += 1

    def _complete(self, messages) -> str:
        path = self._cache_path(messages)
        if path is not None and path.exists():
            text = path.read_text(encoding="utf-8")
            self._account(messages, text, cached=True)
            return text
        self._rate_wait()
        spec = self.model_spec
        if self.max_tokens > 0:
            from dataclasses import replace
            spec = replace(spec, max_tokens=self.max_tokens)
        text = llm.complete(spec, messages, json_mode=True,
                            max_retries=self.max_retries)
        if path is not None:
            try:
                path.write_text(text, encoding="utf-8")
            except OSError:
                pass
        self._account(messages, text, cached=False)
        return text

    def _do_chunk(self, chunk: Batch) -> None:
        try:
            text = self._complete(self.build_messages(chunk))
            self.apply(chunk, llm.parse_json(text))
        except Exception as e:  # noqa: BLE001 - per-chunk isolation
            with self._lock:
                self.usage["errors"] += 1
            for row in chunk:
                row["llm_error"] = str(e)[:200]

    def usage_report(self) -> dict:
        u = dict(self.usage or {})
        total = u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)
        u["estimated_cost"] = round(total / 1000.0 * self.cost_per_1k, 4)
        u["token_basis"] = "estimated"
        return u

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        chunks = [batch[i:i + self.chunk_size]
                  for i in range(0, len(batch), self.chunk_size)]
        if not chunks:
            return batch
        workers = min(self.max_concurrency, len(chunks))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(self._do_chunk, chunks))
        return batch


_DEFAULT_INSTRUCTION = (
    "You are a precise data classifier for an LLM training-data platform. "
    "For each item, identify its primary subject from the allowed label list and "
    "optionally add directly supported secondary subjects. Judge only by the "
    "item's content; do not infer a label from the source URL or prior metadata."
)

# The output JSON schema is fixed and minimal: only index + label list.
_SCHEMA_HINT = (
    'Return ONLY a JSON object with this exact schema, no prose:\n'
    '{"results": [{"index": <int>, "labels": [<string>, ...]}]}\n'
    "- index: the item index exactly as given\n"
    "- labels: a non-empty subset of the allowed labels; put the primary domain first\n"
    "- include one results entry per item; use no labels outside the allowed list"
)


@register("domain_classify")
class DomainClassify(LLMOperator):
    """Lake-synchronised, per-item multi-label domain classifier via an LLM.

    Args (via ``--arg k=v``; values are JSON):
      * ``model``           model-pool name (required)
      * ``labels``          optional additional labels, e.g.
                            ``["text2sql","robotics"]``
      * ``field``           core column to receive the primary label (default ``domain``)
      * ``sync_lake``       include the current lake's registered/observed
                            domain classes (default ``true``)
      * ``prompt``          optional custom instruction (JSON schema is always appended)
      * ``chunk_size``      items per LLM call, <=10 (default 10)
      * ``max_concurrency`` thread cap (default 64)

    Every lake keeps a persistent registry seeded with several broad built-in
    domains.  On setup this operator synchronises any distinct ``domain`` values
    already found in the lake, then classifies against the union of that registry
    and optional labels.  Thus labels such as ``text2sql`` already used by a
    lake remain available without editing every pipeline YAML.

    Writes the full label list to ``tags.domain_labels`` (and the legacy
    ``tags.labels`` alias) and the first label to ``field`` so recipe filters
    such as ``domain='code'`` continue to work.
    """
    spec = OperatorSpec(
        "domain_classify", "1.1", "score", gpu_required=False,
        description="LLM domain classifier using persistent + lake-synchronised labels.")

    def __init__(self, model=None, labels=None, prompt=None, field="domain",
                 sync_lake=True, chunk_size=MAX_CHUNK,
                 max_concurrency=DEFAULT_CONCURRENCY, **kw):
        super().__init__(model, chunk_size, max_concurrency, **kw)
        if labels is None:
            labels = []
        if not isinstance(labels, (list, tuple)):
            raise ValueError("domain_classify labels must be a JSON list")
        self.extra_labels = _unique_labels(labels)
        self.labels: list[str] = []
        self.prompt = prompt
        self.field = field
        self.sync_lake = bool(sync_lake)

    def setup(self, ctx: OperatorContext) -> None:
        super().setup(ctx)
        lake_labels: list[str] = []
        if ctx.root:
            from pathlib import Path
            from ..catalog import Catalog

            catalog = Catalog(Path(ctx.root) / "catalog.db")
            try:
                # Explicit pipeline labels are durable too; subsequent runs and
                # other classifier invocations see the same vocabulary.
                if self.extra_labels:
                    catalog.register_domain_classes(self.extra_labels, source="user")
                if self.sync_lake:
                    lake_labels = [item["name"] for item in catalog.sync_domain_classes()]
                else:
                    lake_labels = [item["name"] for item in catalog.list_domain_classes()]
                catalog.commit()
            finally:
                catalog.close()
        self.labels = _unique_labels(
            [*schema.DEFAULT_DOMAIN_CLASSES, *lake_labels, *self.extra_labels]
        )
        if not self.labels:  # defensive; defaults above make this unreachable
            raise ValueError("domain_classify has no allowed labels")

    def build_messages(self, chunk: Batch) -> list[dict]:
        items = [{"index": i,
                  "text": extract_text(r.get("content", r))[:self.max_input_chars]}
                 for i, r in enumerate(chunk)]
        user = (
            f"Allowed labels: {self.labels}\n\n"
            f"Items (classify each by index):\n"
            f"{_json(items)}\n\n{_SCHEMA_HINT}"
        )
        return [
            {"role": "system", "content": self.prompt or _DEFAULT_INSTRUCTION},
            {"role": "user", "content": user},
        ]

    def apply(self, chunk: Batch, data: dict) -> None:
        allowed = set(self.labels)
        for entry in data.get("results", []):
            idx = entry.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(chunk)):
                continue
            labels = [str(label) for label in entry.get("labels", [])
                      if label in allowed]
            row = chunk[idx]
            row["domain_labels"] = labels     # full list -> tags.domain_labels
            row["labels"] = labels            # backward-compatible tag alias
            if labels:
                row[self.field] = labels[0]   # primary -> core column


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _unique_labels(values) -> list[str]:
    """Normalize labels while preserving the declared priority/order."""
    labels: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = str(value).strip()
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels
