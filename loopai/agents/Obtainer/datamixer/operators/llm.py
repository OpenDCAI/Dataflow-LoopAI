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
from ..quality_validation import (
    validate_topic_records,
    validated_semantic_signals,
)
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
                 max_tokens: int | None = None, output_retries: int = 1,
                 strict_output: bool = False, strict_transport: bool = True,
                 **_):
        self.model_name = model
        self.chunk_size = max(1, min(int(chunk_size), MAX_CHUNK))
        self.max_concurrency = max(1, int(max_concurrency))
        self.cache_enabled = bool(cache)
        self.interval = 60.0 / float(rpm) if rpm else 0.0
        self.max_retries = int(max_retries)
        self.max_input_chars = int(max_input_chars)
        self.cost_per_1k = float(cost_per_1k)
        self.max_tokens = int(max_tokens or 0)
        self.output_retries = max(0, int(output_retries))
        self.strict_output = bool(strict_output)
        self.strict_transport = bool(strict_transport)
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
        pool = ModelPool(ctx.root)
        if not self.model_name:
            self.model_name = pool.default_name()
        if not self.model_name:
            raise ValueError(
                f"{self.spec.name} has no resolved default model; start the managed "
                "acquisition worker or explicitly pass a registered operator model"
            )
        self.model_spec = pool.get(self.model_name)
        self._check_local_openai_service()
        self._lock = threading.Lock()
        self._rl_lock = threading.Lock()
        self.usage = {"calls": 0, "cache_hits": 0, "errors": 0,
                      "prompt_tokens": 0, "completion_tokens": 0}
        if self.cache_enabled and ctx.root:
            self._cache_dir = Path(ctx.root) / "llm_cache"
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _check_local_openai_service(self) -> None:
        """Fail pipeline setup before crawling when a local vLLM is down."""
        import urllib.error
        import urllib.request
        from urllib.parse import urlsplit, urlunsplit

        spec = self.model_spec
        if spec is None or spec.response_format != "openaichat":
            return
        parsed = urlsplit(spec.api_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return
        if parsed.scheme not in {"http", "https"}:
            return
        marker = "/chat/completions"
        if marker not in parsed.path:
            return
        models_path = parsed.path.split(marker, 1)[0].rstrip("/") + "/models"
        models_url = urlunsplit(
            (parsed.scheme, parsed.netloc, models_path, "", "")
        )
        request = urllib.request.Request(
            models_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(5, spec.timeout)) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise RuntimeError(
                f"local model service for {self.model_name!r} is unavailable "
                f"at {models_url}: {exc}"
            ) from exc
        ids = {
            str(item.get("id") or "")
            for item in (payload.get("data") or [])
            if isinstance(item, dict)
        } if isinstance(payload, dict) else set()
        if ids and spec.model not in ids:
            raise RuntimeError(
                f"local model service at {models_url} does not expose "
                f"{spec.model!r}; available: {sorted(ids)}"
            )

    # -- to override -------------------------------------------------------
    def build_messages(self, chunk: Batch) -> list[dict]:
        raise NotImplementedError

    def apply(self, chunk: Batch, data: dict) -> None:
        raise NotImplementedError

    def validate_output(self, chunk: Batch, data: dict) -> None:  # noqa: ARG002
        """Raise when a syntactically valid JSON object is semantically unusable."""

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

    def _do_chunk(self, chunk: Batch) -> tuple[str, str] | None:
        messages = self.build_messages(chunk)
        last_error: Exception | None = None
        last_kind = "semantic"
        for attempt in range(self.output_retries + 1):
            text = ""
            try:
                text = self._complete(messages)
                data = llm.parse_json(text)
                self.apply(chunk, data)
                self.validate_output(chunk, data)
                for row in chunk:
                    row.pop("llm_error", None)
                return None
            except Exception as exc:  # noqa: BLE001 - bounded semantic repair
                last_error = exc
                last_kind = (
                    "transport"
                    if isinstance(exc, RuntimeError)
                    else "semantic"
                )
                # Never preserve a syntactically/semantically invalid response:
                # a durable pipeline retry must make a fresh model request.
                path = self._cache_path(messages)
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                if attempt < self.output_retries and text:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": (
                                "The previous JSON was incomplete or invalid: "
                                f"{str(exc)[:300]}. Return the complete required "
                                "JSON object now. Do not return an empty object."
                            ),
                        },
                    ]
                    continue
                break
        with self._lock:
            self.usage["errors"] += 1
        error = str(last_error or "invalid model output")[:200]
        for row in chunk:
            row["llm_error"] = error
        return error, last_kind

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
            errors = [error for error in ex.map(self._do_chunk, chunks) if error]
        if errors and self.strict_output:
            raise RuntimeError(
                f"{self.spec.name} received invalid model output: {errors[0][0]}"
            )
        transport_errors = [error for error in errors if error[1] == "transport"]
        if transport_errors and self.strict_transport:
            raise RuntimeError(
                f"{self.spec.name} model service failed: {transport_errors[0][0]}"
            )
        return batch


_DEFAULT_INSTRUCTION = (
    "You are a precise data classifier for an LLM training-data platform. "
    "For each item, identify its primary subject from the allowed label list and "
    "optionally add directly supported secondary subjects. Judge only by the "
    "item's content; do not infer a label from the source URL or prior metadata."
)

# The output JSON schema includes an auditable confidence.  Downstream quality
# gates must be able to distinguish a model classification from a caller simply
# assigning ``domain=finance`` during ingest.  The classifier emits a generic
# ``semantic_signals`` list (no vertical vocabulary), so the same judgement
# chain follows whichever topic the WebAgent explored.
_SCHEMA_HINT = (
    "Return ONLY a JSON object with this exact schema, no prose:\n"
    '{"results": [{"index": <int>, "labels": [<string>, ...], "confidence": <number 0..1>, '
    '"semantic_signals": [{"type": <string>, "evidence": <exact quote>, "confidence": <number 0..1>}]}]}\n'
    "- index: the item index exactly as given\n"
    "- labels: a non-empty subset of the allowed labels; put the primary domain first; "
    "labels=[] when the item is unrelated to the acquisition focus\n"
    "- confidence: confidence that the primary label is supported by the item content\n"
    "- semantic_signals: only for accepted labels, return at least two distinct, directly "
    "supported semantic categories; otherwise return []\n"
    "- every semantic signal evidence must be a short exact quote from the item; "
    "generic words alone are not evidence\n"
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
      * ``focus_keywords``  the WebAgent's exploration keywords; items that are
                            not directly related to this focus are marked
                            unrelated (labels=[]) instead of being admitted
      * ``prompt``          optional custom instruction (JSON schema is always appended)
      * ``chunk_size``      items per LLM call, <=10 (default 10)
      * ``max_concurrency`` thread cap (default 64)

    Every lake keeps a persistent registry seeded with several broad built-in
    domains.  On setup this operator synchronises any distinct ``domain`` values
    already found in the lake, then classifies against the union of that registry
    and optional labels.  Thus labels such as ``text2sql`` already used by a
    lake remain available without editing every pipeline YAML.

    Writes the full label list, primary-label confidence and classifier identity
    to tags.  The first label is also written to ``field`` so recipe filters such
    as ``domain='code'`` continue to work.  The generic ``semantic_signals``
    field carries the grounded evidence that downstream quality filters check.
    """
    spec = OperatorSpec(
        "domain_classify", "1.3", "score", gpu_required=False,
        description="LLM domain classifier using persistent + lake-synchronised labels.")

    def __init__(self, model=None, labels=None, prompt=None, field="domain",
                 sync_lake=True, focus_keywords=None, chunk_size=MAX_CHUNK,
                 max_concurrency=DEFAULT_CONCURRENCY, **kw):
        kw.setdefault("strict_output", False)
        kw.setdefault("strict_transport", True)
        super().__init__(model, chunk_size, max_concurrency, **kw)
        if labels is None:
            labels = []
        if not isinstance(labels, (list, tuple)):
            raise ValueError("domain_classify labels must be a JSON list")
        if focus_keywords is None:
            focus_keywords = []
        if not isinstance(focus_keywords, (list, tuple)):
            raise ValueError("domain_classify focus_keywords must be a JSON list")
        self.extra_labels = _unique_labels(labels)
        self.focus_keywords = _unique_labels(focus_keywords)
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
        focus = ""
        if self.focus_keywords:
            focus = (
                "\nAcquisition focus: " + ", ".join(self.focus_keywords) + ".\n"
                "Only items directly related to this focus are acceptable; "
                "unrelated items must return labels=[] and semantic_signals=[].\n"
            )
        user = (
            f"Allowed labels: {self.labels}\n\n"
            f"Items (classify each by index):\n"
            f"{_json(items)}\n\n{focus}{_SCHEMA_HINT}"
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
            row["topic_match"] = bool(labels)  # focus relevance decided by the LLM
            if not labels:
                continue
            try:
                confidence = float(entry.get("confidence"))
            except (TypeError, ValueError):
                confidence = 0.0
            row["domain_confidence"] = max(0.0, min(1.0, confidence))
            row["domain_classifier"] = f"{self.spec.name}@{self.spec.version}"
            row["domain_classifier_model"] = self.model_name
            row[self.field] = labels[0]   # primary -> core column
            semantic_signals, _ = validated_semantic_signals(
                entry.get("semantic_signals"),
                row.get("content", row),
            )
            row["semantic_signals"] = semantic_signals
            row["signal_generator"] = f"{self.spec.name}@{self.spec.version}"

    def validate_output(self, chunk: Batch, data: dict) -> None:  # noqa: ARG002
        # Every row must have a classification verdict; an empty label list is a
        # valid verdict (item unrelated to the focus) and is dropped downstream.
        missing = []
        for index, row in enumerate(chunk):
            if "domain_labels" not in row:
                missing.append(index)
        if missing:
            raise ValueError(
                f"missing valid domain classification for indexes {missing}"
            )


@register("topic_quality_filter")
class TopicQualityFilter(Operator):
    """Admit rows whose LLM domain judgement matches the WebAgent's focus.

    ``domain_classify`` already judged relevance against the campaign's
    ``focus_keywords`` and emitted grounded semantic signals.  This filter
    enforces a deterministic admission threshold on that evidence: a recognised
    classifier, a non-empty label list (focus relevance), a confidence floor,
    and at least ``min_semantic_signals`` grounded signal categories whose
    evidence appears verbatim in the content.  No vertical (e.g. finance) is
    hard-wired, so the same step follows whichever topic the WebAgent explored.
    """

    spec = OperatorSpec(
        "topic_quality_filter",
        "1.0",
        "filter",
        description=(
            "Filter LLM-classified rows by focus relevance, confidence and "
            "grounded semantic signal categories."
        ),
    )

    def __init__(
        self,
        min_semantic_signals: int = 2,
        min_classifier_confidence: float = 0.8,
        min_signal_confidence: float = 0.7,
        allowed_signal_types: list[str] | None = None,
        **_,
    ):
        self.min_semantic_signals = max(0, int(min_semantic_signals))
        self.min_classifier_confidence = float(min_classifier_confidence)
        self.min_signal_confidence = float(min_signal_confidence)
        self.allowed_signal_types = (
            _unique_labels(allowed_signal_types)
            if allowed_signal_types is not None else None
        )

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        candidates = []
        for row in batch:
            # Provenance fields live in tags while classifier outputs are
            # top-level during the in-flight pipeline.
            candidates.append({**dict(row.get("tags") or {}), **row})
        _, report = validate_topic_records(
            candidates,
            defaults={},
            content_key="content",
            allowed_signal_types=(
                frozenset(self.allowed_signal_types)
                if self.allowed_signal_types else None
            ),
            min_semantic_signals=self.min_semantic_signals,
            min_classifier_confidence=self.min_classifier_confidence,
            min_signal_confidence=self.min_signal_confidence,
        )
        accepted = []
        for row, decision in zip(batch, report["records"]):
            if row.get("llm_error"):
                decision.setdefault("rejection_reasons", []).append(
                    "topic_classifier_output_invalid"
                )
                decision["classification_error"] = str(row["llm_error"])[:200]
                decision["accepted"] = False
            row["topic_quality_passed"] = bool(decision["accepted"])
            row["topic_quality_findings"] = list(
                decision.get("rejection_reasons") or []
            )
            row["topic_quality_report"] = decision
            if decision["accepted"]:
                accepted.append(row)
        return accepted


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
