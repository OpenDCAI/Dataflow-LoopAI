"""Domain-aware L2 admission and L3 generation operators.

The generic webpage pipeline deliberately keeps extraction separate from domain
policy.  This module supplies the stricter contracts required when the target
is executable code or Text-to-SQL rather than ordinary explanatory prose.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import re
import sqlite3
import symtable
import threading
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..utils import extract_text
from .base import Batch, Operator, OperatorContext, OperatorSpec, register
from .llm import DEFAULT_CONCURRENCY, LLMOperator, MAX_CHUNK


_WS = re.compile(r"\s+")
_CODE_FENCE = re.compile(r"```([A-Za-z0-9_+.-]*)\s*\n(.*?)```", re.DOTALL)
_DDL = re.compile(r"\bCREATE\s+TABLE\b", re.IGNORECASE)
_READ_QUERY = re.compile(r"\b(?:SELECT|WITH)\b", re.IGNORECASE)
_CODE_HINT = re.compile(
    r"```|\b(?:def|class|function|import|from|SELECT|JOIN|CREATE\s+TABLE|"
    r"INSERT|UPDATE|DELETE|const|let|var|async|await|return|for|while)\b|"
    r"[{};]",
    re.IGNORECASE,
)
_ALLOWED_CODE_LANGUAGES = {
    "bash", "c", "cpp", "csharp", "css", "go", "html", "java", "javascript",
    "json", "jsx", "kotlin", "php", "python", "r", "ruby", "rust", "sql",
    "swift", "typescript", "tsx", "yaml",
}

_CODE_SUFFIX_LANGUAGE = {
    ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp",
    ".go": "go", ".java": "java", ".js": "javascript", ".jsx": "jsx",
    ".kt": "kotlin", ".php": "php", ".py": "python", ".r": "r",
    ".rb": "ruby", ".rs": "rust", ".sh": "bash", ".sql": "sql",
    ".swift": "swift", ".ts": "typescript", ".tsx": "tsx",
}

_LANGUAGE_ALIASES = {
    "js": "javascript", "py": "python", "python3": "python",
    "rs": "rust", "sh": "bash", "shell": "bash", "ts": "typescript",
}


def _tag(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if row.get(key) is not None:
        return row[key]
    tags = row.get("tags")
    return tags.get(key, default) if isinstance(tags, dict) else default


def _norm(value: Any) -> str:
    return _WS.sub(" ", str(value or "").strip()).casefold()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _source_text(row: dict[str, Any]) -> str:
    return extract_text(row.get("content", row))


def _source_url(row: dict[str, Any]) -> str:
    content = row.get("content")
    if isinstance(content, dict):
        return str(content.get("source_url") or content.get("url") or "")
    return str(row.get("source_uri") or "")


class _HTMLCodeBlockExtractor(HTMLParser):
    """Preserve exact whitespace inside HTML ``pre`` blocks and their language."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hints: list[str] = []
        self.current: list[str] | None = None
        self.current_language = ""
        self.blocks: list[tuple[str, str]] = []

    @staticmethod
    def _hint(attrs: list[tuple[str, str | None]]) -> str:
        classes = " ".join(
            value or "" for key, value in attrs if key in {"class", "data-lang"}
        ).casefold()
        for pattern in (
            r"(?:highlight|language|lang)-([a-z0-9_+.-]+)",
            r"\b(python3?|javascript|typescript|rust|golang|go|sql|bash|shell)\b",
        ):
            match = re.search(pattern, classes)
            if match:
                language = match.group(1)
                return _LANGUAGE_ALIASES.get(language, language)
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        hint = self._hint(attrs) or (self.hints[-1] if self.hints else "")
        self.hints.append(hint)
        if tag.casefold() == "pre" and self.current is None:
            self.current = []
            self.current_language = hint
        elif tag.casefold() == "br" and self.current is not None:
            self.current.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "pre" and self.current is not None:
            code = "".join(self.current).replace("\r\n", "\n").strip("\n")
            if code.strip():
                self.blocks.append((self.current_language, code))
            self.current = None
            self.current_language = ""
        if self.hints:
            self.hints.pop()

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)


def _normalized_language(language: str) -> str:
    value = str(language or "").strip().casefold()
    return _LANGUAGE_ALIASES.get(value, value)


def _valid_code_block(language: str, code: str) -> bool:
    if len(code.strip()) < 8:
        return False
    if language == "python":
        try:
            ast.parse(code)
        except SyntaxError:
            return False
        return True
    if language == "sql":
        return sqlite3.complete_statement(code.rstrip() + ";")
    return bool(_CODE_HINT.search(code))


def _undefined_python_globals(code: str) -> list[str]:
    """Find obvious unbound globals without executing generated code."""
    try:
        root = symtable.symtable(code, "<generated>", "exec")
    except SyntaxError:
        return []
    defined = {
        symbol.get_name()
        for symbol in root.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_parameter()
    }
    allowed = set(dir(builtins)) | defined | {"__name__", "__file__", "__package__"}
    unresolved: set[str] = set()

    def visit(table) -> None:
        for symbol in table.get_symbols():
            if not symbol.is_referenced():
                continue
            if table is root or symbol.is_global():
                name = symbol.get_name()
                if name not in allowed:
                    unresolved.add(name)
        for child in table.get_children():
            visit(child)

    visit(root)
    return sorted(unresolved)


def _is_read_only_sql(sql: str) -> bool:
    cleaned = re.sub(r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*", "", sql,
                     flags=re.DOTALL).strip().casefold()
    if not cleaned.startswith(("select", "with")):
        return False
    return not bool(re.search(r"\b(?:insert|update|delete|drop|alter|attach|pragma|"
                              r"create|replace|vacuum)\b", cleaned))


def _sqlite_pair_error(schema_sql: str, query_sql: str) -> str | None:
    """Validate a schema/query pair without touching persistent user data."""
    if not _DDL.search(schema_sql):
        return "schema_has_no_create_table"
    if not _is_read_only_sql(query_sql):
        return "assistant_sql_not_read_only_query"
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema_sql)
        conn.execute(query_sql)
    except sqlite3.Error as exc:
        return f"sqlite_validation_failed:{exc}"
    finally:
        conn.close()
    return None


@register("code_record_prepare")
class CodeRecordPrepare(Operator):
    """Project extracted technical PT into fields required by native Code filters."""

    spec = OperatorSpec(
        "code_record_prepare", "1.0", "map",
        description="Prepare native DataFlow Code filter input columns without scoring.",
    )

    def __init__(self, extract_code_blocks: bool = True, max_code_blocks: int = 24, **_):
        self.extract_code_blocks = bool(extract_code_blocks)
        self.max_code_blocks = max(1, int(max_code_blocks))

    @staticmethod
    def _language(text: str, source_url: str, row: dict[str, Any]) -> str:
        fenced = _CODE_FENCE.findall(text)
        if fenced:
            language = _normalized_language(fenced[0][0])
            if language in _ALLOWED_CODE_LANGUAGES:
                return language
        suffix = Path(urlparse(source_url).path).suffix.casefold()
        if suffix in _CODE_SUFFIX_LANGUAGE:
            return _CODE_SUFFIX_LANGUAGE[suffix]
        domain = str(_tag(row, "domain", "")).casefold()
        for token in ("python", "javascript", "typescript", "rust", "sql", "go"):
            if token in domain:
                return token
        return "text"

    def _blocks(
        self, row: dict[str, Any], text: str, language: str, source_url: str
    ) -> list[dict[str, str]]:
        candidates: list[tuple[str, str]] = []
        content = row.get("content")
        main_html = content.get("main_html") if isinstance(content, dict) else ""
        if self.extract_code_blocks and main_html:
            parser = _HTMLCodeBlockExtractor()
            parser.feed(str(main_html))
            candidates.extend(parser.blocks)
        for fenced_language, code in _CODE_FENCE.findall(text):
            candidates.append((_normalized_language(fenced_language), code.strip()))
        suffix = Path(urlparse(source_url).path).suffix.casefold()
        if not candidates and suffix in _CODE_SUFFIX_LANGUAGE:
            candidates.append((_CODE_SUFFIX_LANGUAGE[suffix], text))

        blocks = []
        seen: set[str] = set()
        for raw_language, code in candidates:
            block_language = _normalized_language(raw_language) or language
            if block_language not in _ALLOWED_CODE_LANGUAGES:
                block_language = language
            if block_language not in _ALLOWED_CODE_LANGUAGES:
                continue
            if not _valid_code_block(block_language, code):
                continue
            key = f"{block_language}\0{code}"
            if key in seen:
                continue
            seen.add(key)
            blocks.append({"language": block_language, "code": code})
            if len(blocks) >= self.max_code_blocks:
                break
        return blocks

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        for row in batch:
            text = _source_text(row)
            source_url = _source_url(row)
            language = self._language(text, source_url, row)
            blocks = self._blocks(row, text, language, source_url)
            if blocks:
                language = blocks[0]["language"]
            raw_name = Path(urlparse(source_url).path).name or "web_document.md"
            filename = raw_name if "." in raw_name else f"{raw_name}.md"
            lines = text.splitlines()
            record = {
                "text": text,
                "lines": lines,
                "language": language,
                "filename": filename,
                "filetype": "markdown",
                "line_count": len(lines),
                "visible_text_length": len(text),
                "total_code_length": max(1, len(text)),
            }
            row.update(record)
            row["code_sample"] = record
            row["code_blocks"] = blocks
            row["code_block_count"] = len(blocks)
            row["code_record_prepared"] = True
        return batch


@register("text2sql_sqlite_prepare")
class Text2SQLSQLitePrepare(Operator):
    """Materialize generated DDL as per-row SQLite DBs for native DataFlow checks."""

    spec = OperatorSpec(
        "text2sql_sqlite_prepare", "1.0", "filter",
        description="Create isolated SQLite validation DBs; no quality score is assigned.",
    )

    def __init__(self, database_root: str = "runtime/text2sql_databases", **_):
        self.database_root = database_root
        self.root: Path | None = None

    def setup(self, ctx: OperatorContext) -> None:
        root = Path(self.database_root)
        self.root = root if root.is_absolute() else Path(ctx.root) / root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _question(row: dict[str, Any]) -> str:
        content = row.get("content")
        messages = content.get("messages") if isinstance(content, dict) else None
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "user":
                    return str(message.get("content") or "").strip()
        return ""

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        if self.root is None:
            self.setup(ctx)
        accepted = []
        for row in batch:
            schema_sql = str(row.get("text2sql_schema") or "").strip()
            query_sql = str(row.get("text2sql_sql") or "").strip()
            digest = hashlib.sha256(
                f"{row.get('sample_id', '')}\0{schema_sql}".encode("utf-8")
            ).hexdigest()[:20]
            db_id = f"dm_{digest}"
            db_dir = self.root / db_id
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / f"{db_id}.sqlite"
            temp_path = db_dir / (
                f".{db_id}.{os.getpid()}.{threading.get_ident()}.tmp.sqlite"
            )
            error = None
            try:
                temp_path.unlink(missing_ok=True)
                conn = sqlite3.connect(str(temp_path))
                try:
                    conn.executescript(schema_sql)
                    conn.execute(query_sql).fetchmany(1)
                    conn.commit()
                finally:
                    conn.close()
                os.replace(temp_path, db_path)
            except (OSError, sqlite3.Error) as exc:
                temp_path.unlink(missing_ok=True)
                error = f"sqlite_prepare_failed:{exc}"
            row["text2sql_sqlite_prepared"] = error is None
            row["text2sql_sqlite_error"] = error
            if error is not None:
                continue
            row["SQL"] = query_sql
            row["question"] = self._question(row)
            row["db_id"] = db_id
            row["text2sql_database_path"] = str(db_path)
            accepted.append(row)
        return accepted


@register("domain_quality_filter")
class DomainQualityFilter(Operator):
    """Fail-closed source admission for an explicit Code or Text2SQL target.

    ``domain_classify`` remains the model-derived domain proof.  This filter
    adds deterministic evidence requirements that are appropriate to the final
    domain and records a per-row decision for the durable streaming queue.
    """

    spec = OperatorSpec(
        "domain_quality_filter", "1.0", "filter",
        description="Admit code/text2sql source text only with target-domain proof.",
    )

    def __init__(
        self,
        target_domain: str,
        min_classifier_confidence: float = 0.8,
        min_chars: int = 300,
        require_primary_label: bool = True,
        max_target_label_rank: int | None = None,
        **_,
    ):
        target = str(target_domain or "").strip().casefold()
        if target not in {"code", "text2sql"}:
            raise ValueError("domain_quality_filter target_domain must be code or text2sql")
        self.target_domain = target
        self.min_classifier_confidence = float(min_classifier_confidence)
        self.min_chars = max(1, int(min_chars))
        self.require_primary_label = bool(require_primary_label)
        self.max_target_label_rank = (
            max(1, int(max_target_label_rank))
            if max_target_label_rank is not None else None
        )

    def _evidence_reasons(self, row: dict[str, Any], text: str) -> list[str]:
        if self.target_domain == "code":
            reasons = [] if _CODE_HINT.search(text) else ["code_evidence_missing"]
            blocks = _tag(row, "code_blocks", [])
            if not isinstance(blocks, list) or not blocks:
                reasons.append("executable_code_block_missing")
            return reasons
        reasons = []
        if not _DDL.search(text):
            reasons.append("text2sql_schema_ddl_missing")
        if not _READ_QUERY.search(text):
            reasons.append("text2sql_read_query_missing")
        return reasons

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        accepted = []
        for row in batch:
            labels_raw = _tag(row, "domain_labels", _tag(row, "labels", []))
            labels = [str(x).strip().casefold() for x in labels_raw] \
                if isinstance(labels_raw, list) else []
            try:
                confidence = float(_tag(row, "domain_confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            text = _source_text(row)
            reasons: list[str] = []
            classifier = str(_tag(row, "domain_classifier", "")).strip()
            model = str(_tag(row, "domain_classifier_model", "")).strip()
            if row.get("llm_error"):
                reasons.append("domain_classifier_output_invalid")
            if not classifier.startswith("domain_classify@") or not model:
                reasons.append("domain_classifier_identity_missing")
            if self.target_domain not in labels:
                reasons.append("target_domain_label_missing")
                target_rank = None
            else:
                target_rank = labels.index(self.target_domain) + 1
                if self.require_primary_label and target_rank != 1:
                    reasons.append("target_domain_not_primary_label")
                elif (
                    self.max_target_label_rank is not None
                    and target_rank > self.max_target_label_rank
                ):
                    reasons.append("target_domain_rank_below_limit")
            if confidence < self.min_classifier_confidence:
                reasons.append("domain_classifier_confidence_below_threshold")
            if len(text) < self.min_chars:
                reasons.append("source_text_below_min_chars")
            reasons.extend(self._evidence_reasons(row, text))
            decision = {
                "target_domain": self.target_domain,
                "classifier": classifier,
                "classifier_model": model,
                "domain_labels": labels,
                "target_label_rank": target_rank,
                "max_target_label_rank": self.max_target_label_rank,
                "domain_confidence": round(max(0.0, min(1.0, confidence)), 6),
                "source_chars": len(text),
                "accepted": not reasons,
                "rejection_reasons": reasons,
            }
            row["domain_quality_passed"] = bool(decision["accepted"])
            row["domain_quality_findings"] = list(reasons)
            row["domain_quality_report"] = decision
            if decision["accepted"]:
                accepted.append(row)
        return accepted


class _DomainSFTBase(LLMOperator):
    """Shared strict-output mechanics for source-grounded SFT generators."""

    def __init__(self, model=None, chunk_size=MAX_CHUNK,
                 max_concurrency=DEFAULT_CONCURRENCY, max_input_chars=12000,
                 max_tokens=None, **kw):
        kw.setdefault("strict_output", True)
        super().__init__(model, chunk_size, max_concurrency,
                         max_input_chars=max_input_chars, max_tokens=max_tokens,
                         **kw)

    def _items(self, chunk: Batch) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "title": (
                    row.get("content", {}).get("title", "")
                    if isinstance(row.get("content"), dict) else ""
                ),
                "text": _source_text(row)[:self.max_input_chars],
            }
            for index, row in enumerate(chunk)
        ]


@register("pt_to_sft_code")
class PTToSFTCode(_DomainSFTBase):
    """Generate a valid code SFT pair grounded in source code evidence."""

    spec = OperatorSpec(
        "pt_to_sft_code", "1.1", "map",
        description="Generate Code SFT using source code blocks as editable evidence.",
    )

    def build_messages(self, chunk: Batch) -> list[dict]:
        system = (
            "Create one high-value Code SFT example from each supplied technical "
            "document. The answer must teach a concrete code or SQL concept and "
            "use the supplied code_blocks as its technical evidence. You may rewrite, "
            "combine, repair, or complete the source examples. Keep the APIs and behavior "
            "faithful to the source, and return a syntactically valid, self-contained "
            "example with every referenced variable or helper defined or imported."
        )
        user = (
            "Return only JSON: {\"results\":[{\"index\":0,\"question\":\"...\","
            "\"explanation\":\"...\",\"language\":\"python|sql|...\","
            "\"code\":\"generated code without markdown fences\"}]}. "
            "Provide one non-empty result per item. Code must be useful and at least "
            "one complete statement or block.\n\nItems:\n"
            + json.dumps([
                {
                    **item,
                    "code_blocks": _tag(chunk[item["index"]], "code_blocks", []),
                }
                for item in self._items(chunk)
            ], ensure_ascii=False)
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def apply(self, chunk: Batch, data: dict) -> None:
        seen: set[int] = set()
        for item in data.get("results", []):
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(chunk):
                continue
            question = str(item.get("question") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            language = str(item.get("language") or "").strip().casefold()
            code = str(item.get("code") or "").strip()
            if not question or not explanation or not code or language not in _ALLOWED_CODE_LANGUAGES:
                continue
            row = chunk[index]
            source = row.get("content")
            source_text = _source_text(row)[:self.max_input_chars]
            code_blocks = _tag(row, "code_blocks", [])
            title = source.get("title", "") if isinstance(source, dict) else ""
            source_url = source.get("source_url", "") if isinstance(source, dict) else ""
            row["_qa_source_text"] = source_text
            row["_qa_code_blocks"] = code_blocks if isinstance(code_blocks, list) else []
            row["code_language"] = language
            row["qa_code"] = code
            row["content"] = {
                "messages": [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": f"{explanation}\n\n```{language}\n{code}\n```"},
                ],
                "provenance": {"title": title, "source_url": source_url, "domain": "code"},
            }
            row["qa_generated"] = True
            row["qa_model"] = self.model_name
            row.pop("qa_error", None)
            seen.add(index)
        for index, row in enumerate(chunk):
            if index not in seen:
                row["qa_generated"] = False
                row["qa_error"] = "model returned no valid code SFT pair"

    def validate_output(self, chunk: Batch, data: dict) -> None:  # noqa: ARG002
        errors = []
        for index, row in enumerate(chunk):
            if not row.get("qa_generated"):
                errors.append(f"missing code SFT pair for index {index}")
                continue
            code = str(row.get("qa_code") or "")
            language = str(row.get("code_language") or "").casefold()
            blocks = row.get("_qa_code_blocks") or []
            source_language = any(
                isinstance(block, dict)
                and _normalized_language(block.get("language", "")) == language
                for block in blocks
            )
            if not source_language:
                errors.append(
                    f"generated language has no supplied source block for index {index}"
                )
            elif not _valid_code_block(language, code):
                errors.append(f"code block is not executable/valid for index {index}")
            elif language == "python" and _undefined_python_globals(code):
                errors.append(
                    "generated Python has undefined globals for index "
                    f"{index}: {_undefined_python_globals(code)}"
                )
        if errors:
            raise ValueError("; ".join(errors))


@register("code_sft_validate")
class CodeSFTValidate(Operator):
    """Validate Code SFT structure, source-language grounding, and basic syntax."""

    spec = OperatorSpec("code_sft_validate", "1.1", "filter",
                        description="Validate source-informed generated Code SFT.")

    @staticmethod
    def _error(row: dict[str, Any]) -> str | None:
        content = row.get("content")
        messages = content.get("messages") if isinstance(content, dict) else None
        if not isinstance(messages, list) or len(messages) != 2:
            return "code_sft_requires_two_messages"
        if [m.get("role") for m in messages if isinstance(m, dict)] != ["user", "assistant"]:
            return "code_sft_requires_user_assistant_roles"
        answer = str(messages[1].get("content") or "")
        blocks = _CODE_FENCE.findall(answer)
        if len(blocks) != 1:
            return "code_sft_requires_exactly_one_code_fence"
        language, code = blocks[0]
        language = language.strip().casefold()
        code = code.strip()
        if language not in _ALLOWED_CODE_LANGUAGES:
            return "unsupported_code_language"
        blocks = row.get("_qa_code_blocks") or []
        source_language = any(
            isinstance(block, dict)
            and _normalized_language(block.get("language", "")) == language
            for block in blocks
        )
        if not source_language:
            return "code_language_not_grounded_in_source"
        if language == "python":
            try:
                ast.parse(code)
            except SyntaxError as exc:
                return f"python_syntax_error:{exc.msg}"
            undefined = _undefined_python_globals(code)
            if undefined:
                return "python_undefined_globals:" + ",".join(undefined)
        elif language == "sql" and not sqlite3.complete_statement(code.rstrip() + ";"):
            return "sql_statement_incomplete"
        return None

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        accepted = []
        for row in batch:
            error = self._error(row)
            row["code_sft_valid"] = error is None
            row["code_sft_error"] = error
            row.pop("_qa_source_text", None)
            row.pop("_qa_code_blocks", None)
            row.pop("qa_code", None)
            if error is None:
                accepted.append(row)
        return accepted


@register("pt_to_sft_text2sql")
class PTToSFTText2SQL(_DomainSFTBase):
    """Generate an executable Text2SQL triple from source DDL and SQL snippets."""

    spec = OperatorSpec(
        "pt_to_sft_text2sql", "1.0", "map",
        description="Generate source-grounded Text2SQL SFT with DDL and read-only SQL.",
    )

    def build_messages(self, chunk: Batch) -> list[dict]:
        system = (
            "Create one executable Text-to-SQL SFT item per source document. "
            "Extract schema_sql and sql verbatim from the supplied text. schema_sql "
            "must contain CREATE TABLE statements and sql must be a single read-only "
            "SELECT or WITH query executable against that schema. Write a natural "
            "language question the copied query answers. Never invent tables, columns, "
            "literals, joins, or DDL."
        )
        user = (
            "Return only JSON: {\"results\":[{\"index\":0,\"question\":\"...\","
            "\"schema_sql\":\"exact source DDL\",\"sql\":\"exact source query\"}]}. "
            "One complete result for every item.\n\nItems:\n"
            + json.dumps(self._items(chunk), ensure_ascii=False)
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def apply(self, chunk: Batch, data: dict) -> None:
        seen: set[int] = set()
        for item in data.get("results", []):
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(chunk):
                continue
            question = str(item.get("question") or "").strip()
            schema_sql = str(item.get("schema_sql") or "").strip()
            sql = str(item.get("sql") or "").strip()
            if not question or not schema_sql or not sql:
                continue
            row = chunk[index]
            source = row.get("content")
            source_text = _source_text(row)[:self.max_input_chars]
            title = source.get("title", "") if isinstance(source, dict) else ""
            source_url = source.get("source_url", "") if isinstance(source, dict) else ""
            row["_qa_source_text"] = source_text
            row["text2sql_schema"] = schema_sql
            row["text2sql_sql"] = sql
            row["content"] = {
                "messages": [
                    {"role": "system", "content": schema_sql},
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": sql},
                ],
                "provenance": {"title": title, "source_url": source_url, "domain": "text2sql"},
            }
            row["qa_generated"] = True
            row["qa_model"] = self.model_name
            row.pop("qa_error", None)
            seen.add(index)
        for index, row in enumerate(chunk):
            if index not in seen:
                row["qa_generated"] = False
                row["qa_error"] = "model returned no valid text2sql SFT triple"

    def validate_output(self, chunk: Batch, data: dict) -> None:  # noqa: ARG002
        errors = []
        for index, row in enumerate(chunk):
            if not row.get("qa_generated"):
                errors.append(f"missing text2sql SFT triple for index {index}")
                continue
            source = str(row.get("_qa_source_text") or "")
            schema_sql = str(row.get("text2sql_schema") or "")
            sql = str(row.get("text2sql_sql") or "")
            if _compact(schema_sql) not in _compact(source):
                errors.append(f"schema is not an exact source snippet for index {index}")
            if _compact(sql) not in _compact(source):
                errors.append(f"SQL is not an exact source snippet for index {index}")
            pair_error = _sqlite_pair_error(schema_sql, sql)
            if pair_error:
                errors.append(f"invalid text2sql pair for index {index}:{pair_error}")
        if errors:
            raise ValueError("; ".join(errors))


@register("text2sql_sft_validate")
class Text2SQLSFTValidate(Operator):
    """Final fail-closed validation with actual in-memory SQLite execution."""

    spec = OperatorSpec("text2sql_sft_validate", "1.0", "filter",
                        description="Validate Text2SQL schema/query triples by SQLite execution.")

    @staticmethod
    def _error(row: dict[str, Any]) -> str | None:
        content = row.get("content")
        messages = content.get("messages") if isinstance(content, dict) else None
        if not isinstance(messages, list) or len(messages) != 3:
            return "text2sql_requires_system_user_assistant"
        if [m.get("role") for m in messages if isinstance(m, dict)] != ["system", "user", "assistant"]:
            return "text2sql_invalid_message_roles"
        schema_sql = str(messages[0].get("content") or "")
        question = str(messages[1].get("content") or "")
        sql = str(messages[2].get("content") or "")
        if len(question.strip()) < 8:
            return "text2sql_question_too_short"
        source = str(row.get("_qa_source_text") or "")
        if not source or _compact(schema_sql) not in _compact(source):
            return "text2sql_schema_not_grounded_in_source"
        if _compact(sql) not in _compact(source):
            return "text2sql_query_not_grounded_in_source"
        return _sqlite_pair_error(schema_sql, sql)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:  # noqa: ARG002
        accepted = []
        for row in batch:
            error = self._error(row)
            row["text2sql_valid"] = error is None
            row["text2sql_error"] = error
            row["sql_execution_valid"] = error is None
            row.pop("_qa_source_text", None)
            row.pop("text2sql_schema", None)
            row.pop("text2sql_sql", None)
            if error is None:
                accepted.append(row)
        return accepted
