"""Registered operators for webpage L1 -> PT L2 -> initial SFT L3."""
from __future__ import annotations

import re
import json
import os
import select
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

from ..utils import extract_text
from .base import Batch, Operator, OperatorContext, OperatorSpec, register
from .llm import DEFAULT_CONCURRENCY, LLMOperator, MAX_CHUNK, _json

_SPACE = re.compile(r"[ \t\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
    "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ARG002
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        if self._in_title:
            self.title_parts.append(data.strip())
        self.parts.append(data)

    def result(self) -> tuple[str, str]:
        return " ".join(self.title_parts).strip(), _normalize_text("".join(self.parts))


def _normalize_text(text: str) -> str:
    lines = [_SPACE.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _extract_with_bs4(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for node in soup.find_all(tuple(_SKIP_TAGS | {"form", "iframe"})):
        node.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return title, _normalize_text(root.get_text("\n"))


def _extract_html(html: str) -> tuple[str, str, str]:
    try:
        title, text = _extract_with_bs4(html)
        return title, text, "beautifulsoup"
    except ImportError:
        parser = _TextExtractor()
        parser.feed(html)
        title, text = parser.result()
        return title, text, "html.parser"


def _tag(row: dict, key: str, default=None):
    if row.get(key) is not None:
        return row[key]
    tags = row.get("tags") or {}
    return tags.get(key, default)


@register("webpage_to_pt")
class WebpageToPT(Operator):
    """Extract readable pretraining text from an L1 raw HTML sample."""

    spec = OperatorSpec(
        "webpage_to_pt", "1.0", "map",
        description="Extract main readable text from raw webpage HTML.",
    )

    def __init__(self, html_key: str = "html", min_chars: int = 200,
                 max_chars: int = 120000, engine: str = "auto",
                 mineru_python: str | None = None,
                 mineru_model: str | None = None, mineru_gpu: str = "0",
                 mineru_url: str | None = None,
                 mineru_transport: str | None = None,
                 mineru_backend: str = "transformers",
                 mineru_gpu_memory_utilization: float = 0.25,
                 mineru_max_context_window: int = 70000,
                 mineru_max_tokens: int = 4096,
                 mineru_timeout: int = 900, mineru_batch_size: int = 1,
                 fallback: str = "legacy"):
        self.html_key = html_key
        self.min_chars = int(min_chars)
        self.max_chars = int(max_chars)
        if engine not in ("auto", "mineru", "legacy"):
            raise ValueError("webpage_to_pt engine must be auto, mineru, or legacy")
        self.engine = engine
        repo_root = Path(__file__).resolve().parents[5]
        self.mineru_python = mineru_python or os.environ.get(
            "MINERU_HTML_PYTHON", str(repo_root / "envs" / ".mineruhtml" / "bin" / "python")
        )
        self.mineru_model = mineru_model or os.environ.get(
            "MINERU_HTML_MODEL",
            str(repo_root / "model" / "mineru-html"),
        )
        self.mineru_gpu = str(mineru_gpu)
        self.mineru_url = (
            mineru_url
            if mineru_url is not None
            else os.environ.get("MINERU_HTML_URL", "http://127.0.0.1:7986")
        ).rstrip("/")
        self.mineru_transport = (
            mineru_transport
            or os.environ.get("MINERU_HTML_TRANSPORT", "auto")
        ).strip().lower()
        if self.mineru_transport not in ("auto", "http", "worker"):
            raise ValueError("mineru_transport must be auto, http, or worker")
        if mineru_backend not in ("vllm", "transformers"):
            raise ValueError("mineru_backend must be vllm or transformers")
        self.mineru_backend = mineru_backend
        self.mineru_gpu_memory_utilization = float(mineru_gpu_memory_utilization)
        self.mineru_max_context_window = int(mineru_max_context_window)
        self.mineru_max_tokens = int(mineru_max_tokens)
        self.mineru_timeout = int(mineru_timeout)
        self.mineru_batch_size = max(1, int(mineru_batch_size))
        if fallback not in ("legacy", "error"):
            raise ValueError("webpage_to_pt fallback must be legacy or error")
        self.fallback = fallback
        self._mineru = None

    def setup(self, ctx: OperatorContext) -> None:  # noqa: ARG002
        if self.engine == "legacy":
            return
        errors = []
        if self.mineru_transport in ("auto", "http") and self.mineru_url:
            self._mineru = _MinerUHTMLHTTPClient(
                base_url=self.mineru_url,
                timeout=self.mineru_timeout,
            )
            try:
                self._mineru.start()
                self.mineru_transport = "http"
                return
            except Exception as exc:
                errors.append(f"HTTP service unavailable at {self.mineru_url}: {exc}")
                self._mineru.close()
                self._mineru = None
                if self.mineru_transport == "http":
                    return self._handle_setup_failure(errors)

        if self.mineru_transport in ("auto", "worker"):
            python_ok = Path(self.mineru_python).exists()
            model_ok = Path(self.mineru_model).exists()
            if not python_ok:
                errors.append(f"MinerU-HTML Python not found: {self.mineru_python}")
            if not model_ok:
                errors.append(f"MinerU-HTML model not found: {self.mineru_model}")
            if python_ok and model_ok:
                worker = Path(__file__).with_name("mineru_html_worker.py")
                env = os.environ.copy()
                env["CUDA_VISIBLE_DEVICES"] = self.mineru_gpu
                env.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
                self._mineru = _MinerUHTMLClient(
                    python=self.mineru_python,
                    worker=worker,
                    model=self.mineru_model,
                    backend=self.mineru_backend,
                    gpu_memory_utilization=self.mineru_gpu_memory_utilization,
                    max_context_window=self.mineru_max_context_window,
                    max_tokens=self.mineru_max_tokens,
                    timeout=self.mineru_timeout,
                    env=env,
                )
                try:
                    self._mineru.start()
                    self.mineru_transport = "worker"
                    return
                except Exception as exc:
                    errors.append(f"isolated MinerU-HTML worker failed: {exc}")
                    self._mineru.close()
                    self._mineru = None
        self._handle_setup_failure(errors)

    def _handle_setup_failure(self, errors: list[str]) -> None:
        if self.engine == "auto" and self.fallback == "legacy":
            self.engine = "legacy"
            return
        raise RuntimeError("; ".join(errors) or "MinerU-HTML is unavailable")

    def teardown(self, ctx: OperatorContext) -> None:  # noqa: ARG002
        if self._mineru is not None:
            self._mineru.close()
            self._mineru = None

    def _legacy_extract(self, html: str):
        title, text, method = _extract_html(html)
        return title, text, method

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        inputs = []
        for row in batch:
            original = row.get("content")
            if isinstance(original, dict):
                html = str(original.get(self.html_key, ""))
                supplied_title = str(original.get("title", "")).strip()
                supplied_url = str(original.get("url", "")).strip()
            else:
                html = str(original or "")
                supplied_title = ""
                supplied_url = ""
            inputs.append((row, html, supplied_title, supplied_url))

        mineru_results = None
        if self.engine == "mineru" or self._mineru is not None:
            try:
                mineru_results = []
                htmls = [item[1] for item in inputs]
                for start in range(0, len(htmls), self.mineru_batch_size):
                    mineru_results.extend(
                        self._mineru.process(
                            htmls[start:start + self.mineru_batch_size]
                        )
                    )
            except Exception as exc:
                if self.fallback == "error":
                    raise
                for row, _, _, _ in inputs:
                    row["pt_error"] = f"mineru_html: {type(exc).__name__}: {exc}"[:500]

        for index, (row, html, supplied_title, supplied_url) in enumerate(inputs):
            title = ""
            text = ""
            method = "legacy"
            main_html = None
            mineru_error = None
            if mineru_results is not None and index < len(mineru_results):
                result = mineru_results[index]
                mineru_error = result.get("error")
                main_html = result.get("main_html")
                text = str(result.get("main_content") or "").strip()
                method = "mineru_html"
                if mineru_error:
                    row["pt_error"] = f"mineru_html: {mineru_error}"[:500]
                if not text and main_html:
                    _, text, _ = self._legacy_extract(main_html)
                    method = "mineru_html+main_html_fallback"
            if not text:
                if self.fallback == "error" and mineru_results is not None:
                    raise RuntimeError(
                        f"MinerU-HTML returned empty content for row {index}: "
                        f"{mineru_error or 'unknown error'}"
                    )
                title, text, method = self._legacy_extract(html)
                if mineru_results is not None:
                    method = "mineru_html+legacy_fallback"
            if not title:
                title = supplied_title
            if self.max_chars > 0 and len(text) > self.max_chars:
                text = text[:self.max_chars].rsplit("\n", 1)[0].rstrip()
                row["pt_truncated"] = True
            row["raw_html_chars"] = len(html)
            row["pt_text_chars"] = len(text)
            row["pt_extractor"] = method
            row["pt_valid"] = len(text) >= self.min_chars
            content = {
                "text": text,
                "title": supplied_title or title,
                "source_url": supplied_url or _tag(row, "source_uri", ""),
                "document_type": "webpage_pt",
            }
            if main_html:
                content["main_html"] = main_html
            row["content"] = content
        return batch


class _MinerUHTMLClient:
    def __init__(self, *, python: str, worker: Path, model: str,
                 backend: str,
                 gpu_memory_utilization: float, max_context_window: int,
                 max_tokens: int, timeout: int, env: dict):
        self.python = python
        self.worker = worker
        self.model = model
        self.backend = backend
        self.gpu_memory_utilization = gpu_memory_utilization
        self.max_context_window = max_context_window
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.env = env
        self.proc = None

    def start(self) -> None:
        if self.proc is not None:
            return
        self.proc = subprocess.Popen(
            [
                self.python, str(self.worker),
                "--model-path", self.model,
                "--backend", self.backend,
                "--gpu-memory-utilization", str(self.gpu_memory_utilization),
                "--max-context-window", str(self.max_context_window),
                "--max-tokens", str(self.max_tokens),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=self.env,
        )

    def process(self, htmls: list[str]) -> list[dict]:
        if self.proc is None:
            self.start()
        if self.proc.poll() is not None:
            raise RuntimeError(f"MinerU-HTML worker exited with {self.proc.returncode}")
        request_id = uuid.uuid4().hex
        request = json.dumps({"id": request_id, "htmls": htmls}, ensure_ascii=False)
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.proc.stdin.write(request + "\n")
        self.proc.stdin.flush()
        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        if not ready:
            raise TimeoutError(f"MinerU-HTML worker timed out after {self.timeout}s")
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MinerU-HTML worker closed its output")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise RuntimeError("MinerU-HTML worker response id mismatch")
        if response.get("error"):
            raise RuntimeError(response["error"])
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(htmls):
            raise RuntimeError(
                f"MinerU-HTML worker returned {len(results or [])} results for "
                f"{len(htmls)} inputs"
            )
        return results

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        finally:
            self.proc = None


class _MinerUHTMLHTTPClient:
    """Small client for the persistent MinerU-HTML REST service."""

    def __init__(self, *, base_url: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 200))
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(str(exc.reason)) from exc
        if not 200 <= status < 300:
            raise RuntimeError(f"HTTP {status}: {body[:500]}")
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MinerU-HTML service returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("MinerU-HTML service returned a non-object response")
        return result

    def start(self) -> None:
        result = self._request("/health")
        if result.get("status") != "ok":
            raise RuntimeError(f"unhealthy response: {result}")

    def process(self, htmls: list[str]) -> list[dict]:
        results = []
        for html in htmls:
            response = self._request("/extract", {"html": html})
            main_html = response.get("main_html")
            if not isinstance(main_html, str) or not main_html.strip():
                raise RuntimeError("MinerU-HTML service returned empty main_html")
            results.append({
                "main_html": main_html,
                "main_content": None,
                "error": None,
            })
        return results

    def close(self) -> None:
        return None


@register("pt_to_sft_qa")
class PTToSFTQA(LLMOperator):
    """Generate one grounded instruction/answer pair per L2 PT document."""

    spec = OperatorSpec(
        "pt_to_sft_qa", "1.0", "map",
        description="Generate grounded initial SFT QA from pretraining text.",
    )

    def __init__(self, model=None, chunk_size=MAX_CHUNK,
                 max_concurrency=DEFAULT_CONCURRENCY,
                 max_input_chars: int = 12000,
                 max_tokens: int | None = None, **kw):
        kw.setdefault("strict_output", True)
        super().__init__(
            model=model,
            chunk_size=chunk_size,
            max_concurrency=max_concurrency,
            max_input_chars=max_input_chars,
            max_tokens=max_tokens,
            **kw,
        )

    def build_messages(self, chunk: Batch) -> list[dict]:
        items = []
        for index, row in enumerate(chunk):
            content = row.get("content")
            title = content.get("title", "") if isinstance(content, dict) else ""
            items.append({
                "index": index,
                "domain": row.get("domain") or _tag(row, "domain", "general"),
                "title": title,
                "text": extract_text(content)[:self.max_input_chars],
            })
        system = (
            "You create grounded initial SFT training examples from source "
            "documents. Produce exactly one useful question and one self-contained "
            "answer per item. Use only facts present in the supplied text. Preserve "
            "technical notation and code accurately. For medical and financial "
            "content, stay educational and avoid personalized diagnosis, treatment, "
            "investment recommendations, or guarantees."
        )
        user = (
            "Return only a JSON object with this exact schema:\n"
            '{"results":[{"index":0,"question":"...","answer":"..."}]}\n'
            "Include one result for every input index. Questions must be specific "
            "and answers should usually be 2-6 sentences.\n\nItems:\n"
            + _json(items)
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def apply(self, chunk: Batch, data: dict) -> None:
        seen = set()
        for item in data.get("results", []):
            index = item.get("index")
            if not isinstance(index, int) or not (0 <= index < len(chunk)):
                continue
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if not question or not answer:
                continue
            row = chunk[index]
            source = row.get("content")
            title = source.get("title", "") if isinstance(source, dict) else ""
            source_url = (
                source.get("source_url", "") if isinstance(source, dict) else ""
            )
            row["content"] = {
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
                "provenance": {
                    "title": title,
                    "source_url": source_url,
                    "domain": row.get("domain"),
                },
            }
            row["qa_generated"] = True
            row["qa_model"] = self.model_name
            row.pop("qa_error", None)
            seen.add(index)
        for index, row in enumerate(chunk):
            if index not in seen:
                row["qa_generated"] = False
                row["qa_error"] = "model returned no valid QA pair"

    def validate_output(self, chunk: Batch, data: dict) -> None:  # noqa: ARG002
        missing = [
            index for index, row in enumerate(chunk)
            if not row.get("qa_generated")
        ]
        if missing:
            raise ValueError(f"missing valid QA pair for indexes {missing}")
