# -*- coding: utf-8 -*-
"""Math LLMaJ: label failed math cases with short critique + overall error tag.

Inserts after metric_score so analyze_metric_report / bucket_strategy can
consume Code-like ``judge.tags`` / ``judge.reason`` fields.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI

from loopai.common.event_tool import StreamEvent
from loopai.logger import get_logger
from loopai.schema.states import LoopAIState
from loopai.skills.Analyzer.math_llmaj_quality import (
    DIAGNOSIS_STATUS,
    MATH_TAG_DESCRIPTIONS,
    PROCESS_STATUS,
    aggregate_obtainer_actions,
    assess_math_label_quality,
    evidence_supported,
    infer_math_tag_from_critique,
    infer_process_status,
    validate_obtainer_seed,
)
from loopai.skills.Analyzer.utils.stream import get_safe_stream_writer

logger = get_logger()

# Bump when prompt / schema / whitelist / confidence policy / clip limits change.
LABEL_SCHEMA_VERSION = "math_llmaj_v4.3_complete_resolution"
CONFIDENCE_THRESHOLD = 0.6
DEFAULT_MAX_RETRIES_PER_ITEM = 2
DEFAULT_MAX_BATCH_RETRIES = 1
DEFAULT_MAX_BATCH_SIZE = 4
DEFAULT_MAX_INPUT_TOKENS = 5500
DEFAULT_MAX_OUTPUT_TOKENS_PER_CASE = 140
QUESTION_CLIP = 500
TARGET_CLIP = 120
PREDICTION_CLIP = 900
REASON_CLIP = 80
SHORT_CRITIQUE_CLIP = 80
FIRST_ERROR_CLIP = 80
EVIDENCE_CLIP = 160
REPAIR_CLIP = 80
PROMPT_OVERHEAD_TOKENS = 320

# Keep tags aligned with bucket_strategy._math_label_bucket mappings.
MATH_ERROR_TAG_WHITELIST = (
    "评测异常",
    "输出格式错误",
    "计算错误",
    "化简错误",
    "题意理解错误",
    "公式使用错误或遗漏",
    "答案与过程不符",
    "答题步骤不完整",
)

_TAG_ALIASES = {
    "评测异常": "评测异常",
    "评测误判": "评测异常",
    "指标误判": "评测异常",
    "等价判定": "评测异常",
    "metric error": "评测异常",
    "evaluation error": "评测异常",
    "输出格式": "输出格式错误",
    "格式错误": "输出格式错误",
    "无法提取": "输出格式错误",
    "提取失败": "输出格式错误",
    "空答案": "输出格式错误",
    "answer format": "输出格式错误",
    "output format": "输出格式错误",
    "extraction": "输出格式错误",
    "repetition": "输出格式错误",
    "计算": "计算错误",
    "算术错误": "计算错误",
    "数值错误": "计算错误",
    "arithmetic": "计算错误",
    "calculation": "计算错误",
    "化简": "化简错误",
    "代数": "化简错误",
    "符号变换": "化简错误",
    "algebra": "化简错误",
    "symbolic": "化简错误",
    "题意理解": "题意理解错误",
    "建模": "题意理解错误",
    "modeling": "题意理解错误",
    "公式错误": "公式使用错误或遗漏",
    "公式使用错误": "公式使用错误或遗漏",
    "定理": "公式使用错误或遗漏",
    "策略错误": "公式使用错误或遗漏",
    "wrong formula": "公式使用错误或遗漏",
    "答案与过程不符": "答案与过程不符",
    "推理错误": "答案与过程不符",
    "逻辑错误": "答案与过程不符",
    "前后矛盾": "答案与过程不符",
    "reasoning error": "答案与过程不符",
    "步骤不完整": "答题步骤不完整",
    "遗漏步骤": "答题步骤不完整",
    "incomplete": "答题步骤不完整",
}

_BUCKET_FOR_TAG = {
    "评测异常": "math_metric_anomaly",
    "输出格式错误": "math_output_contract",
    "计算错误": "math_arithmetic_calculation",
    "化简错误": "math_algebra_symbolic",
    "题意理解错误": "math_modeling",
    "公式使用错误或遗漏": "math_strategy_theorem",
    "答案与过程不符": "math_reasoning_consistency",
    "答题步骤不完整": "math_verification_completeness",
}

_CONSTRUCT_HINT = {
    "math_output_contract": "生成带 boxed/明确最终答案锚点的格式约束样本",
    "math_arithmetic_calculation": "生成短步骤计算纠错与显式验算样本",
    "math_algebra_symbolic": "生成逐步等价变换与符号验证样本",
    "math_modeling": "生成自然语言条件到数学表达的建模对比样本",
    "math_strategy_theorem": "生成同题多策略与错误路线修正样本",
    "math_reasoning_consistency": "生成步骤级验证与过程一致性样本",
    "math_verification_completeness": "生成约束检查、回代验证与完整解答样本",
}


@dataclass
class LlmCallStats:
    """True API-request accounting (not logical batch count)."""

    requests: int = 0
    retries: int = 0
    errors: int = 0
    elapsed_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    batch_attempts: int = 0
    single_attempts: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "llm_requests": self.requests,
            "llm_retries": self.retries,
            "llm_errors": self.errors,
            "llm_elapsed_ms": round(self.elapsed_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "batch_attempts": self.batch_attempts,
            "single_attempts": self.single_attempts,
        }


def _analyzer(state: LoopAIState) -> dict:
    state.setdefault("analyzer", {})
    return state["analyzer"]


def _runtime_api_key(cfg: dict) -> str:
    return (
        cfg.get("analyze_api_key")
        or os.getenv("_LOOPAI_ANALYZER_RUNTIME_API_KEY")
        or os.getenv("ANALYZER_API_KEY")
        or os.getenv("analyzer_api_key")
        or os.getenv("DEEPSEEK_API_KEY")
        or "EMPTY"
    )


def _model_name(cfg: dict) -> str:
    return (
        cfg.get("analyze_model_path")
        or os.getenv("ANALYZER_MODEL")
        or "deepseek-chat"
    )


def _base_url(cfg: dict) -> str:
    return (
        cfg.get("analyze_base_url")
        or os.getenv("ANALYZER_BASE_URL")
        or "https://api.deepseek.com"
    )


def _init_model(state: LoopAIState, *, max_tokens: Optional[int] = None) -> ChatOpenAI:
    cfg = _analyzer(state)
    kwargs: Dict[str, Any] = {
        "model": _model_name(cfg),
        "api_key": _runtime_api_key(cfg),
        "base_url": _base_url(cfg),
        "temperature": float(cfg.get("analyze_temperature", 0.0) or 0.0),
        "top_p": float(cfg.get("analyze_top_p", 0.95) or 0.95),
        "timeout": float(cfg.get("analyze_request_timeout_seconds", 180) or 180),
        "max_retries": int(cfg.get("analyze_request_max_retries", 0) or 0),
    }
    out_tokens = max_tokens
    if out_tokens is None:
        out_tokens = cfg.get("math_llmaj_max_tokens")
    if out_tokens is not None:
        kwargs["max_tokens"] = max(64, int(out_tokens))
    # deepseek-v4-* defaults to thinking; reasoning tokens dominate wall time / usage.
    disable_thinking = cfg.get("math_llmaj_disable_thinking")
    if disable_thinking is None:
        disable_thinking = True
    if disable_thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(**kwargs)


def _ensure_outdir(state: LoopAIState) -> Path:
    cfg = _analyzer(state)
    runtime_outdir = cfg.get("runtime_output_dir")
    if runtime_outdir:
        outdir = Path(runtime_outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        return outdir
    base = Path(cfg.get("output_dir") or state.get("output_dir") or "./outputs")
    task_id = state.get("task_id") or "default_task"
    outdir = base / task_id / "analyzer"
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _label_cache_path(state: LoopAIState) -> Path:
    """Stable cache across --new-version runs (task-level, not version-level)."""
    cfg = _analyzer(state)
    runtime_outdir = cfg.get("runtime_output_dir")
    if runtime_outdir:
        return Path(runtime_outdir).resolve().parent / "math_llmaj_label_cache.json"
    base = Path(cfg.get("output_dir") or state.get("output_dir") or "./outputs")
    task_id = state.get("task_id") or "default_task"
    return (base / task_id / "analyzer" / "math_llmaj_label_cache.json").resolve()


def _is_math_route(state: LoopAIState, metric_result: Dict[str, Any]) -> bool:
    task_type = str((_analyzer(state).get("analyze_task_type") or "")).lower().replace("-", "_")
    if task_type in {
        "math", "mathematics", "mathematical", "math_reasoning", "math_qa", "数学", "数学推理",
    }:
        return True
    metrics = metric_result.get("metrics") or {}
    for name in metrics:
        if str(name).lower() in {"math_verify", "numerical_match", "choice_accuracy"}:
            return True
    primary = None
    for name, item in metrics.items():
        if isinstance(item, dict) and item.get("priority") == "primary":
            primary = name
            break
    return str(primary or "").lower() in {"math_verify", "numerical_match", "choice_accuracy"}


def _normalize_detail_score(detail_item: Any) -> float:
    if isinstance(detail_item, (int, float)):
        return float(detail_item)
    if isinstance(detail_item, dict):
        return float(detail_item.get("score", 0.0) or 0.0)
    return 0.0


def _load_records(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("rows", "records", "data", "examples", "items"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def _build_enriched_oj_records(
    source_records: List[Dict[str, Any]],
    labeled_records: List[Dict[str, Any]],
    failed_indices: List[int],
) -> List[Dict[str, Any]]:
    """Copy Judger records and append only the public diagnosis fields."""
    failed_set = set(failed_indices)
    enriched: List[Dict[str, Any]] = []
    for index, source_record in enumerate(source_records):
        row = dict(source_record)
        if index in failed_set and index < len(labeled_records):
            judge = labeled_records[index].get("judge")
            judge = judge if isinstance(judge, dict) else {}
            overall_tag = str(judge.get("overall_error_tag") or "").strip()
            short_critique = str(judge.get("short_critique") or "").strip()
            if not overall_tag or not short_critique:
                raise ValueError(f"失败样本 {index} 缺少完整错因或短评")
            row["overall_error_tag"] = overall_tag
            row["short_critique"] = short_critique
        enriched.append(row)
    return enriched


def _write_enriched_oj(
    source_path: str,
    outdir: Path,
    timestamp: str,
    records: List[Dict[str, Any]],
) -> Path:
    """Write an enriched copy while preserving the source JSON container shape."""
    source = Path(source_path)
    suffix = source.suffix.lower() if source.suffix.lower() in {".json", ".jsonl"} else ".jsonl"
    output_path = outdir / f"oj_records_enriched_{timestamp}{suffix}"

    if suffix == ".jsonl":
        with output_path.open("w", encoding="utf-8") as file_obj:
            for record in records:
                file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
        return output_path

    payload: Any = records
    try:
        original_payload = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        original_payload = None
    if isinstance(original_payload, dict):
        for key in ("rows", "records", "data", "examples", "items"):
            if isinstance(original_payload.get(key), list):
                payload = dict(original_payload)
                payload[key] = records
                break
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def _normalize_record_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Alias Math Judger fields onto Analyzer/Obtainer expected names."""
    out = dict(rec)
    if not out.get("question"):
        out["question"] = out.get("problem") or out.get("prompt") or out.get("input")
    if not out.get("target"):
        out["target"] = (
            out.get("answer")
            or out.get("ground_truth")
            or out.get("reference")
            or out.get("label")
        )
    if not out.get("prediction"):
        out["prediction"] = (
            out.get("generated_ans")
            or out.get("completion")
            or out.get("eval_pred")
            or out.get("response")
        )
    if not out.get("generated_ans") and out.get("prediction"):
        out["generated_ans"] = out["prediction"]
    return out


def _select_primary_metric(metric_result: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    metrics = metric_result.get("metrics") or {}
    for name, item in metrics.items():
        if isinstance(item, dict) and item.get("priority") == "primary":
            return name, item
    if metrics:
        name = next(iter(metrics.keys()))
        return name, metrics[name]
    return "unknown", {}


def _normalize_tags(raw_tags: Any) -> List[str]:
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, list):
        return []
    normalized: List[str] = []
    seen = set()
    for tag in raw_tags:
        text = str(tag or "").strip()
        if not text:
            continue
        mapped = None
        if text in MATH_ERROR_TAG_WHITELIST:
            mapped = text
        else:
            low = text.lower()
            for alias, canon in _TAG_ALIASES.items():
                if alias.lower() in low or low in alias.lower():
                    mapped = canon
                    break
        if mapped is None:
            for canon in MATH_ERROR_TAG_WHITELIST:
                if canon in text or text in canon:
                    mapped = canon
                    break
        if mapped and mapped not in seen:
            seen.add(mapped)
            normalized.append(mapped)
    return normalized


def _looks_like_repetition(text: str) -> bool:
    if not text:
        return False
    if text.count("user\n{") >= 2 or text.count("\nuser\n") >= 3:
        return True
    chunks = re.findall(r"Answer:\s*[^\n]{1,40}", text)
    if len(chunks) >= 4 and len(set(chunks)) <= 2:
        return True
    return False


def _rule_label(rec: Dict[str, Any], detail: Any, extraction_detail: Any) -> Optional[Dict[str, Any]]:
    completion = str(rec.get("generated_ans") or rec.get("prediction") or "").strip()
    extraction_score = None
    if isinstance(extraction_detail, (int, float)):
        extraction_score = float(extraction_detail)
    elif isinstance(extraction_detail, dict) and "score" in extraction_detail:
        extraction_score = float(extraction_detail.get("score") or 0.0)

    if not completion:
        return {
            "stage": "math_output",
            "tags": ["输出格式错误"],
            "reason": "模型输出为空，最终答案无法提取",
            "short_critique": "作答为空，未提供可评估的解题过程和最终答案。",
            "overall_error_tag": "输出格式错误",
            "confidence": 0.95,
            "domain": rec.get("subject") or rec.get("domain") or "unknown",
            "label_source": "rule",
            "origin_source": "rule",
            "process_status": "absent",
            "diagnosis_status": "rule_confirmed",
            "actionable": True,
            "needs_review": False,
            "context_truncated": False,
            "evidence_quote": "[empty completion]",
            "repair_target": "强制最终答案锚点/boxed",
            "first_error_step": "输出为空",
        }
    if extraction_score is not None and extraction_score <= 0.0:
        return {
            "stage": "math_output",
            "tags": ["输出格式错误"],
            "reason": "extraction_rate 诊断为最终答案提取失败",
            "short_critique": "解答未给出可稳定提取的最终答案，输出要求没有完成。",
            "overall_error_tag": "输出格式错误",
            "confidence": 0.92,
            "domain": rec.get("subject") or rec.get("domain") or "unknown",
            "label_source": "rule",
            "origin_source": "rule",
            "process_status": "absent",
            "diagnosis_status": "rule_confirmed",
            "actionable": True,
            "needs_review": False,
            "context_truncated": False,
            "evidence_quote": "extraction_rate=0",
            "repair_target": "明确最终答案格式",
            "first_error_step": "答案提取失败",
        }
    if _looks_like_repetition(completion):
        return {
            "stage": "math_output",
            "tags": ["输出格式错误"],
            "reason": "输出出现 user/Answer 模板重复污染，最终答案锚点不可靠",
            "short_critique": "作答被重复模板内容污染，无法可靠识别有效答案。",
            "overall_error_tag": "输出格式错误",
            "confidence": 0.88,
            "domain": rec.get("subject") or rec.get("domain") or "unknown",
            "label_source": "rule",
            "origin_source": "rule",
            "process_status": "superficial",
            "diagnosis_status": "rule_confirmed",
            "actionable": True,
            "needs_review": False,
            "context_truncated": False,
            "evidence_quote": "user/Answer repetition",
            "repair_target": "抑制模板重复并固定答案格式",
            "first_error_step": "输出污染",
        }
    return None


def _clip_text(value: Any, limit: int, *, keep_tail: bool = False) -> str:
    text = str(value if value is not None else "")
    if len(text) <= limit:
        return text
    if keep_tail:
        head = max(200, limit // 3)
        tail = max(200, limit - head - 40)
        return text[:head] + "\n...[truncated]...\n" + text[-tail:]
    return text[:limit] + "\n...[truncated]..."


def _escape_bare_backslashes_in_json_strings(text: str) -> str:
    """Repair model JSON containing unescaped LaTeX commands such as ``\\sqrt``."""
    repaired: List[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            in_string = not in_string
            repaired.append(ch)
            i += 1
            continue
        if in_string and ch == "\\":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in {'"', "\\", "/"}:
                repaired.extend((ch, nxt))
                i += 2
                continue
            if nxt == "u" and re.match(r"^[0-9a-fA-F]{4}$", text[i + 2 : i + 6]):
                repaired.append(ch)
                i += 1
                continue
            repaired.append("\\\\")
            i += 1
            continue
        repaired.append(ch)
        i += 1
    return "".join(repaired)


def _safe_json_any(text: str) -> Any:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    try:
        return json.loads(_escape_bare_backslashes_in_json_strings(t))
    except Exception:
        pass
    m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", t)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        try:
            return json.loads(_escape_bare_backslashes_in_json_strings(m.group(1)))
        except Exception:
            return None


def _safe_json_obj(text: str) -> Optional[Dict[str, Any]]:
    obj = _safe_json_any(text)
    return obj if isinstance(obj, dict) else None


def _infer_process_status(prediction: Any) -> str:
    return infer_process_status(prediction)


def _evidence_supported(evidence: str, prediction: str) -> bool:
    return evidence_supported(evidence, prediction)


def _postprocess_label(
    label: Dict[str, Any],
    *,
    prediction: str,
    local_process_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Delegate to math_llmaj_quality.assess_math_label_quality (single gate)."""
    return assess_math_label_quality(
        label,
        prediction,
        local_process_status=local_process_status,
        confidence_threshold=CONFIDENCE_THRESHOLD,
    )


def _normalize_label_obj(obj: Dict[str, Any], *, source: str = "llm") -> Dict[str, Any]:
    raw_overall_tag = obj.get("overall_error_tag")
    raw_tags = raw_overall_tag if raw_overall_tag not in (None, "") else obj.get("tags")
    # New responses carry one overall tag. ``tags`` remains a one-element
    # compatibility field for existing report/bucket/Obtainer consumers.
    tags = _normalize_tags(raw_tags)[:1]
    confidence = obj.get("confidence")
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.5 if tags else 0.25
    confidence = max(0.0, min(1.0, confidence))
    short_critique = str(obj.get("short_critique") or obj.get("reason") or "").strip()
    if not tags:
        inferred_tag = infer_math_tag_from_critique(short_critique)
        if inferred_tag:
            tags = [inferred_tag]
            confidence = max(confidence, 0.82 if inferred_tag == "评测异常" else 0.65)
    reason = short_critique or "证据不足，无法形成一句话短评"
    domain = str(obj.get("domain") or "unknown").strip() or "unknown"
    origin = str(obj.get("origin_source") or source or "llm")
    first_err = str(obj.get("first_error_step") or "").strip()
    evidence = str(obj.get("evidence_quote") or "").strip()
    repair = str(obj.get("repair_target") or "").strip()
    ps = str(obj.get("process_status") or "").strip().lower()
    if ps not in PROCESS_STATUS:
        ps = ""
    needs_review = obj.get("needs_review")
    if needs_review is None:
        needs_review = (
            (not tags)
            or (confidence < CONFIDENCE_THRESHOLD)
            or (not evidence)
            or (not short_critique)
        )
    else:
        needs_review = bool(needs_review)
    context_truncated = bool(obj.get("context_truncated", False))
    return {
        "tags": tags,
        "reason": reason[:REASON_CLIP],
        "short_critique": short_critique[:SHORT_CRITIQUE_CLIP],
        "overall_error_tag": tags[0] if tags else "",
        "confidence": confidence,
        "domain": domain,
        "first_error_step": first_err[:FIRST_ERROR_CLIP],
        "evidence_quote": evidence[:EVIDENCE_CLIP],
        "repair_target": repair[:REPAIR_CLIP],
        "process_status": ps,
        "needs_review": needs_review,
        "context_truncated": context_truncated,
        "label_source": source,
        "origin_source": origin,
        "cache_hit": bool(obj.get("cache_hit", False)),
    }


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: CJK ~1 tok/char, ascii ~4 chars/tok."""
    if not text:
        return 0
    cjk = 0
    for ch in text:
        o = ord(ch)
        if o > 0x2E80:
            cjk += 1
    ascii_chars = len(text) - cjk
    return max(1, cjk + (ascii_chars + 3) // 4)


def _case_prompt_block(item: Dict[str, Any]) -> str:
    return "\n".join(
        [
            f"### case_id={item.get('idx')}",
            f"match_type: {item.get('match_type')}",
            f"extracted: {_clip_text(item.get('extracted'), 80)}",
            "【题目】",
            _clip_text(item.get("question"), QUESTION_CLIP),
            "【标准答案】",
            _clip_text(item.get("target"), TARGET_CLIP),
            "【模型解答】",
            _clip_text(item.get("prediction"), PREDICTION_CLIP, keep_tail=True),
        ]
    )


def _pack_batches(
    items: List[Dict[str, Any]],
    *,
    max_items: int,
    max_input_tokens: int,
) -> List[List[Dict[str, Any]]]:
    """Pack by sample-count cap AND estimated input token budget."""
    max_items = max(1, int(max_items))
    max_input_tokens = max(800, int(max_input_tokens))
    batches: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_tok = PROMPT_OVERHEAD_TOKENS
    for item in items:
        block_tok = _estimate_tokens(_case_prompt_block(item))
        # Oversized single case: still send alone (clipped content).
        if cur and (
            len(cur) >= max_items
            or cur_tok + block_tok > max_input_tokens
        ):
            batches.append(cur)
            cur = []
            cur_tok = PROMPT_OVERHEAD_TOKENS
        cur.append(item)
        cur_tok += block_tok
    if cur:
        batches.append(cur)
    return batches


def _label_config_fingerprint(state: Optional[LoopAIState] = None, cfg: Optional[dict] = None) -> str:
    cfg = cfg or (_analyzer(state) if state is not None else {})
    payload = {
        "schema": LABEL_SCHEMA_VERSION,
        "model": _model_name(cfg) if cfg else (os.getenv("ANALYZER_MODEL") or "deepseek-chat"),
        "base_url": _base_url(cfg) if cfg else (os.getenv("ANALYZER_BASE_URL") or ""),
        "temperature": float((cfg or {}).get("analyze_temperature", 0.0) or 0.0),
        "whitelist": list(MATH_ERROR_TAG_WHITELIST),
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "clips": {
            "question": QUESTION_CLIP,
            "target": TARGET_CLIP,
            "prediction": PREDICTION_CLIP,
            "reason": REASON_CLIP,
        },
        "max_input_tokens": int(
            (cfg or {}).get("math_llmaj_max_input_tokens", DEFAULT_MAX_INPUT_TOKENS)
            or DEFAULT_MAX_INPUT_TOKENS
        ),
        "max_output_tokens_per_case": int(
            (cfg or {}).get(
                "math_llmaj_max_output_tokens_per_case",
                DEFAULT_MAX_OUTPUT_TOKENS_PER_CASE,
            )
            or DEFAULT_MAX_OUTPUT_TOKENS_PER_CASE
        ),
        "disable_thinking": bool(
            True
            if (cfg or {}).get("math_llmaj_disable_thinking") is None
            else (cfg or {}).get("math_llmaj_disable_thinking")
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key(item: Dict[str, Any], config_fp: str) -> str:
    """Content hash over full labeling inputs + config fingerprint."""
    payload = {
        "schema": LABEL_SCHEMA_VERSION,
        "config_fp": config_fp,
        "id": item.get("sample_id"),
        "question": str(item.get("question") or ""),
        "target": str(item.get("target") if item.get("target") is not None else ""),
        "prediction": str(item.get("prediction") or ""),
        "extracted": str(item.get("extracted") if item.get("extracted") is not None else ""),
        "match_type": str(item.get("match_type") or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_label_cache(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("entries"), dict):
        return data["entries"]
    if isinstance(data, dict):
        # Legacy flat cache: only reuse entries that look like labels.
        return {
            k: v
            for k, v in data.items()
            if isinstance(v, dict) and ("tags" in v or "confidence" in v)
        }
    return {}


def _save_label_cache(path: Path, cache: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": LABEL_SCHEMA_VERSION,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entries": cache,
    }
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _is_cacheable_label(label: Dict[str, Any]) -> bool:
    src = str(label.get("origin_source") or label.get("label_source") or "")
    if src.startswith(("llm_parse", "llm_error", "llm_missing")):
        return False
    has_tag = bool(label.get("overall_error_tag") or label.get("tags"))
    return has_tag and (src.startswith(("llm", "rule")) or src in {"llm", "rule"})


def _build_batch_label_prompt(batch: List[Dict[str, Any]]) -> str:
    tag_definitions = "\n".join(
        f"- {tag}: {MATH_TAG_DESCRIPTIONS[tag]}"
        for tag in MATH_ERROR_TAG_WHITELIST
    )
    joined = "\n\n".join(_case_prompt_block(item) for item in batch)
    n = len(batch)
    return f"""数学错因标注（首错定位 + 一句话短评 + 总错因标签）。对下面 {n} 个失败样本各标 1 条。

总错因标签及定义（overall_error_tag 每条必须且只能选择 1 个）：
{tag_definitions}

判定顺序（必须遵守）：
1) 输出为空 / 模板污染 / 无法提取答案 → 输出格式错误（或交由规则）
2) 判 process_status：absent / incomplete / superficial / substantive
3) absent 或 incomplete → 只能标「答题步骤不完整」
4) 从模型解答【开头向后】扫描，定位【第一个】可验证错误；禁止优先抓最后一步显眼算术/同余错误
5) 结合首错和整份作答生成 short_critique：只写一句话，概括导致本题失败的主要问题，不逐条罗列错误
6) 仅根据 short_critique 选择一个 overall_error_tag，禁止留空
7) 若完整复核后确认模型答案与参考答案数学等价、只是 Metric 未识别 → 标「评测异常」；不得伪造模型错因

首错映射：
- 作答正确但提取/归一化/等价判定误判 → 评测异常
- 空输出/污染 → 输出格式错误
- 只有最终答案 / 过程中断 → 答题步骤不完整
- 公式/定理前提错 → 公式使用错误或遗漏
- 代数恒等/符号/化简错（即使后面还有算术错）→ 化简错误
- 纯数值算术错 → 计算错误
- 条件翻译/建系错 → 题意理解错误
- 过程结论与最终答案明确冲突 → 答案与过程不符

关键禁令：
- 「最终答案错误」≠「答案与过程不符」。无过程时禁止使用「答案与过程不符」。
- 只有一句 The answer is ... / Answer: ... → 必须「答题步骤不完整」。
- 「答案与过程不符」仅当：substantive + 中间结论与最终答案不一致；evidence_quote 用 || 连接两段原样子串。
- overall_error_tag 非空时 evidence_quote 必填，且必须是模型解答中的原样子串。
- 若末步同余/算术显眼，但更早已有错误化简（如 S=10/81），必须标早期化简/策略错误。
- short_critique 必须能由 first_error_step 与 evidence_quote 支撑，不能引入作答中不存在的问题。
- 「解题思路混乱」只是表面描述，不是当前标签。应继续比较题目、参考答案和整份作答，选择最接近的具体主错因。
- 不允许输出空标签、unknown、other、待诊断或待复核。

字段：
case_id, process_status, first_error_step, evidence_quote,
short_critique(一句话，<=60字), overall_error_tag, confidence,
repair_target, needs_review, context_truncated, domain

只输出 JSON 数组，长度={n}，禁止 Markdown/解题。

示例1（无过程）：{{"case_id":0,"process_status":"absent","first_error_step":"无推导","evidence_quote":"The answer is 199.","short_critique":"作答只给出最终答案，没有展示可核验的解题过程。","overall_error_tag":"答题步骤不完整","confidence":0.92,"repair_target":"要求完整推导","needs_review":false,"context_truncated":false,"domain":"geometry"}}
示例2（首错在化简，非末步）：{{"case_id":1,"process_status":"substantive","first_error_step":"S=10/81","evidence_quote":"S = 10/81","short_critique":"级数在首次化简时得到错误表达式，导致后续结论整体失效。","overall_error_tag":"化简错误","confidence":0.88,"repair_target":"级数化简校验","needs_review":false,"context_truncated":false,"domain":"number_theory"}}
示例3（Metric 误判）：{{"case_id":2,"process_status":"substantive","first_error_step":"无模型错误","evidence_quote":"The final answer is \\boxed{{5n+10}}","short_critique":"模型推导及答案与参考答案等价，失败来自答案等价判定未识别。","overall_error_tag":"评测异常","confidence":0.95,"repair_target":"修复答案归一化与等价判定","needs_review":false,"context_truncated":false,"domain":"algebra"}}

{joined}
"""


def _output_token_budget(batch_size: int, per_case: int) -> int:
    return max(96, int(per_case) * max(1, batch_size) + 32)


def _extract_label_rows(parsed: Any) -> List[Dict[str, Any]]:
    """Accept JSON array (preferred) or single object / wrapped list."""
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        for key in ("results", "labels", "items", "data"):
            if isinstance(parsed.get(key), list):
                return [x for x in parsed[key] if isinstance(x, dict)]
        if (
            "overall_error_tag" in parsed
            or "short_critique" in parsed
            or "tags" in parsed
            or "reason" in parsed
            or "case_id" in parsed
        ):
            return [parsed]
    return []


def _parse_batch_response(
    content: str,
    expected_ids: List[int],
) -> Tuple[Dict[int, Dict[str, Any]], List[int]]:
    """Unified parser for single/batch. Returns (by_id, missing_ids)."""
    parsed = _safe_json_any(content)
    rows = _extract_label_rows(parsed)
    if not rows:
        return {}, list(expected_ids)

    by_id: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        case_id: Optional[int] = None
        raw_id = row.get("case_id")
        if raw_id is not None:
            try:
                case_id = int(raw_id)
            except Exception:
                case_id = None
        if case_id is None and len(expected_ids) == 1 and (
            "overall_error_tag" in row
            or "short_critique" in row
            or "tags" in row
            or "reason" in row
        ):
            case_id = expected_ids[0]
        if case_id is None:
            continue
        if case_id in by_id:
            logger.warning(f"[math_llmaj_label] duplicate case_id={case_id}, keeping last")
        label = _normalize_label_obj(row, source="llm")
        if not label.get("tags"):
            logger.warning(
                f"[math_llmaj_label] case_id={case_id} returned no resolvable tag; retrying"
            )
            continue
        label["origin_source"] = "llm"
        by_id[case_id] = label

    missing = [i for i in expected_ids if i not in by_id]
    return by_id, missing


def _failed_label(reason: str, source: str) -> Dict[str, Any]:
    return {
        "tags": [],
        "reason": reason,
        "short_critique": "",
        "overall_error_tag": "",
        "confidence": 0.2,
        "domain": "unknown",
        "label_source": source,
        "origin_source": source,
        "cache_hit": False,
    }


def _record_usage(resp: Any, stats: LlmCallStats) -> None:
    usage = getattr(resp, "usage_metadata", None) or getattr(resp, "response_metadata", None) or {}
    if not isinstance(usage, dict):
        return
    # LangChain variants
    prompt = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    completion = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total = usage.get("total_tokens") or 0
    token_usage = usage.get("token_usage") if isinstance(usage.get("token_usage"), dict) else {}
    if token_usage:
        prompt = prompt or token_usage.get("prompt_tokens") or 0
        completion = completion or token_usage.get("completion_tokens") or 0
        total = total or token_usage.get("total_tokens") or 0
    try:
        stats.prompt_tokens += int(prompt or 0)
        stats.completion_tokens += int(completion or 0)
        stats.total_tokens += int(total or (int(prompt or 0) + int(completion or 0)))
    except Exception:
        pass


def _llm_content(
    llm: ChatOpenAI,
    prompt: str,
    stats: Optional[LlmCallStats] = None,
    *,
    is_retry: bool = False,
) -> str:
    t0 = time.perf_counter()
    try:
        resp = llm.invoke(prompt)
        content = getattr(resp, "content", resp)
        if isinstance(content, list):
            text = "".join(
                str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content
            )
        else:
            text = str(content or "")
        if stats is not None:
            stats.requests += 1
            if is_retry:
                stats.retries += 1
            _record_usage(resp, stats)
        return text
    except Exception:
        if stats is not None:
            stats.requests += 1
            stats.errors += 1
            if is_retry:
                stats.retries += 1
        raise
    finally:
        if stats is not None:
            stats.elapsed_ms += (time.perf_counter() - t0) * 1000.0


def _invoke_label_batch(
    llm: ChatOpenAI,
    batch: List[Dict[str, Any]],
    stats: Optional[LlmCallStats] = None,
    *,
    max_retries_per_item: int = DEFAULT_MAX_RETRIES_PER_ITEM,
    max_batch_retries: int = DEFAULT_MAX_BATCH_RETRIES,
    max_tokens: Optional[int] = None,
    make_llm: Optional[Any] = None,
) -> Dict[int, Dict[str, Any]]:
    """Return mapping idx -> label. Unified parse; retry only failures."""
    if not batch:
        return {}
    results: Dict[int, Dict[str, Any]] = {}
    pending = list(batch)

    def _llm_for(size: int) -> Any:
        if callable(make_llm):
            return make_llm(size)
        return llm

    # Batch attempt(s)
    for attempt in range(max_batch_retries + 1):
        if not pending:
            break
        if stats is not None:
            stats.batch_attempts += 1
        try:
            content = _llm_content(
                _llm_for(len(pending)),
                _build_batch_label_prompt(pending),
                stats,
                is_retry=attempt > 0,
            )
            by_id, missing = _parse_batch_response(content, [int(x["idx"]) for x in pending])
            results.update(by_id)
            pending = [x for x in pending if int(x["idx"]) in missing]
            if not pending:
                break
            logger.warning(
                f"[math_llmaj_label] batch missing {len(pending)} cases after attempt {attempt + 1}"
            )
        except Exception as exc:
            logger.warning(f"[math_llmaj_label] batch invoke failed (attempt {attempt + 1}): {exc}")
            if attempt >= max_batch_retries:
                break

    # Per-item retry budget for remaining only
    still_pending = list(pending)
    for item in still_pending:
        idx = int(item["idx"])
        got = False
        last_reason = "模型返回无法解析或缺少明确标签"
        last_source = "llm_parse_failed"
        for attempt in range(max(1, max_retries_per_item)):
            if stats is not None:
                stats.single_attempts += 1
            try:
                content = _llm_content(
                    _llm_for(1),
                    _build_batch_label_prompt([item]),
                    stats,
                    is_retry=attempt > 0,
                )
                by_id, missing = _parse_batch_response(content, [idx])
                if idx in by_id and idx not in missing:
                    results[idx] = by_id[idx]
                    got = True
                    break
                last_reason = "单条结果缺失、无法解析或缺少明确标签"
                last_source = "llm_parse_failed"
            except Exception as exc:
                last_reason = f"标注调用失败: {type(exc).__name__}"
                last_source = "llm_error"
                logger.warning(f"[math_llmaj_label] single invoke failed case_id={idx}: {exc}")
        if not got:
            results[idx] = _failed_label(last_reason, last_source)
    return results


def _build_label_prompt(item: Dict[str, Any]) -> str:
    return _build_batch_label_prompt([item])


def _invoke_label(llm: ChatOpenAI, prompt: str) -> Dict[str, Any]:
    """Legacy single-prompt helper; kept for compatibility."""
    stats = LlmCallStats()
    try:
        content = _llm_content(llm, prompt, stats)
        # Fake a case_id=0 parse path via batch parser
        by_id, missing = _parse_batch_response(content, [0])
        if 0 in by_id and 0 not in missing:
            return by_id[0]
        return _failed_label("模型返回无法解析或缺少明确标签", "llm_parse_failed")
    except Exception as exc:
        logger.warning(f"[math_llmaj_label] llm invoke failed: {exc}")
        return _failed_label(f"标注调用失败: {type(exc).__name__}", "llm_error")


def _origin_is_llm(origin_source: str) -> bool:
    src = str(origin_source or "")
    if src.startswith("rule"):
        return False
    return src.startswith("llm") or src in {"llm", "cache"}


def _attach_judge(rec: Dict[str, Any], label: Dict[str, Any]) -> Dict[str, Any]:
    prediction = str(rec.get("generated_ans") or rec.get("prediction") or "")
    local_ps = _infer_process_status(prediction)
    label = _postprocess_label(label, prediction=prediction, local_process_status=local_ps)

    confidence = float(label.get("confidence") or 0.0)
    origin_source = str(
        label.get("origin_source")
        or (label.get("label_source") if label.get("label_source") != "cache" else "llm")
        or "unknown"
    )
    cache_hit = bool(label.get("cache_hit"))
    tags = list(label.get("tags") or [])
    needs_review = bool(label.get("needs_review", False))
    context_truncated = bool(label.get("context_truncated", False))
    evidence = str(label.get("evidence_quote") or "").strip()
    diagnosis_status = str(label.get("diagnosis_status") or "unknown")
    actionable = bool(label.get("actionable", False))
    evidence_valid = bool(label.get("evidence_valid", False))
    process_status = str(label.get("process_status") or local_ps)

    display_source = "cache" if cache_hit else (label.get("label_source") or origin_source)
    if diagnosis_status == "metric_anomaly":
        stage = "math_metric_anomaly"
    elif diagnosis_status == "rule_confirmed":
        stage = "math_output"
    elif actionable:
        stage = "math_labeled"
    elif needs_review:
        stage = "math_review"
    else:
        stage = "math_unknown"

    judge = {
        "stage": stage,
        "tags": tags,
        "reason": label.get("reason") or "",
        "short_critique": label.get("short_critique") or label.get("reason") or "",
        "overall_error_tag": label.get("overall_error_tag") or (tags[0] if tags else ""),
        "confidence": confidence,
        "domain": label.get("domain") or rec.get("subject") or "unknown",
        "label_source": display_source,
        "origin_source": origin_source,
        "cache_hit": cache_hit,
        "confidence_cleared": bool(label.get("confidence_cleared")),
        "needs_review": needs_review,
        "context_truncated": context_truncated,
        "process_status": process_status,
        "diagnosis_status": diagnosis_status,
        "actionable": actionable,
        "evidence_valid": evidence_valid,
        "construction_scope": str(label.get("construction_scope") or "none"),
        "resolution_route": str(label.get("resolution_route") or "labeling_error"),
        "quality_reason": str(label.get("quality_reason") or ""),
        "evidence_quote": evidence[:EVIDENCE_CLIP],
        "repair_target": str(label.get("repair_target") or "")[:REPAIR_CLIP],
    }
    if label.get("first_error_step"):
        judge["first_error_step"] = str(label["first_error_step"])[:FIRST_ERROR_CLIP]
    out = dict(rec)
    out["judge"] = judge
    if actionable and tags:
        out["pred_steps"] = [
            {
                "step_id": 1,
                "step_score": 0,
                "errors": tags,
                "note": judge.get("short_critique"),
                "evidence_quote": judge.get("evidence_quote"),
                "first_error_step": judge.get("first_error_step"),
            }
        ]
    else:
        out["pred_steps"] = []
    out["passed"] = False
    return out


def _build_obtainer_action(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Emit construct action only when quality gate already set actionable=true."""
    judge = rec.get("judge") if isinstance(rec.get("judge"), dict) else {}
    tags = judge.get("tags") or []
    if not bool(judge.get("actionable")):
        return None
    if not tags:
        return None
    primary_tag = tags[0]
    bucket = _BUCKET_FOR_TAG.get(primary_tag, "diagnostic_unknown")
    if bucket == "diagnostic_unknown":
        return None
    evidence = str(judge.get("evidence_quote") or "").strip()
    first_err = str(judge.get("first_error_step") or "").strip()
    seed = {
        "id": rec.get("id") or rec.get("unique_id") or rec.get("idx"),
        "problem": rec.get("question") or rec.get("problem"),
        "gold_answer": rec.get("target") if rec.get("target") is not None else rec.get("answer"),
        "wrong_solution": rec.get("generated_ans") or rec.get("prediction"),
        "short_critique": judge.get("short_critique") or judge.get("reason"),
        "evidence_quote": evidence,
        "first_error_step": first_err,
        "repair_target": judge.get("repair_target"),
        "process_status": judge.get("process_status"),
    }
    if validate_obtainer_seed(seed):
        return None
    return {
        "action_id": f"case_{seed['id']}_{bucket}",
        "mode": "construct",
        "schema_version": "obtainer_action_case_v2",
        "capability_bucket": bucket,
        "domain": judge.get("domain") or rec.get("subject") or "unknown",
        "error_tags": tags,
        "overall_error_tag": judge.get("overall_error_tag") or primary_tag,
        "short_critique": judge.get("short_critique") or judge.get("reason"),
        "confidence": judge.get("confidence"),
        "origin_source": judge.get("origin_source"),
        "diagnosis_status": judge.get("diagnosis_status"),
        "construction_scope": judge.get("construction_scope") or "whole_case",
        "resolution_route": judge.get("resolution_route") or "training_data",
        "actionable": True,
        "needs_review": False,
        "evidence_valid": bool(judge.get("evidence_valid", True)),
        "process_status": judge.get("process_status"),
        "evidence_quote": evidence,
        "first_error_step": first_err,
        "repair_target": judge.get("repair_target"),
        "seed_bad_case": seed,
        "suggested_construction": {
            "mode": (
                "stepwise_correction"
                if judge.get("construction_scope") == "step"
                else "contrastive_repair"
            ),
            "hint": _CONSTRUCT_HINT.get(bucket, "基于该 bad case 构造对照修复样本"),
        },
        "source": {
            "analyzer": "math_llmaj_label",
            "schema": LABEL_SCHEMA_VERSION,
            "judger_fields": ["problem/answer/generated_ans"],
        },
    }


def math_llmaj_label_node(state: LoopAIState):
    """
    After metric_score:
    1. Load records + primary metric details
    2. Rule-label obvious format failures
    3. LLM-label remaining failed cases
    4. Write one Judger-compatible OJ copy with only error tag + short critique
    """
    writer = get_safe_stream_writer()
    stage_timing: Dict[str, float] = {}
    t_node = time.perf_counter()

    def _emit(message: str, *, progress=None, data=None):
        if writer:
            writer(StreamEvent(
                current="analyzer.math_llmaj_label",
                message=message,
                progress=progress,
                data=data or {},
            ).json())

    def _mark(name: str, t0: float) -> None:
        stage_timing[name] = round((time.perf_counter() - t0) * 1000.0, 1)

    analyzer = _analyzer(state)
    metric_result = analyzer.get("metric_eval_results") or state.get("eval_results") or {}
    if not metric_result:
        path = analyzer.get("metric_eval_result_path")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                metric_result = json.load(f)

    if not _is_math_route(state, metric_result):
        _emit("非 Math 路线，跳过 math_llmaj_label", progress=1.0, data={"skipped": True})
        analyzer["math_llmaj_skipped"] = True
        return state

    t_load = time.perf_counter()
    alignment = (metric_result.get("alignment") or {})
    records_path = alignment.get("path") or analyzer.get("eval_result_path")
    source_records = _load_records(str(records_path or ""))
    records = [_normalize_record_fields(r) for r in source_records]
    if not records:
        raise ValueError("math_llmaj_label: 未加载到评测 records，无法标注")

    primary_name, primary_item = _select_primary_metric(metric_result)
    details = primary_item.get("details") or []
    extraction = (metric_result.get("metrics") or {}).get("extraction_rate") or {}
    extraction_details = extraction.get("details") if isinstance(extraction, dict) else []

    failed_indices: List[int] = []
    for idx, detail in enumerate(details):
        if _normalize_detail_score(detail) == 0.0:
            failed_indices.append(idx)

    max_failed = analyzer.get("math_llmaj_max_failed")
    if max_failed is not None:
        try:
            max_failed_i = int(max_failed)
            if max_failed_i >= 0:
                failed_indices = failed_indices[:max_failed_i]
        except Exception:
            pass

    batch_size = int(analyzer.get("analyze_batch_size") or DEFAULT_MAX_BATCH_SIZE)
    batch_size = max(1, batch_size)
    max_input_tokens = int(
        analyzer.get("math_llmaj_max_input_tokens", DEFAULT_MAX_INPUT_TOKENS)
        or DEFAULT_MAX_INPUT_TOKENS
    )
    max_output_tokens_per_case = int(
        analyzer.get(
            "math_llmaj_max_output_tokens_per_case",
            DEFAULT_MAX_OUTPUT_TOKENS_PER_CASE,
        )
        or DEFAULT_MAX_OUTPUT_TOKENS_PER_CASE
    )
    max_retries_per_item = int(
        analyzer.get("math_llmaj_max_retries_per_item", DEFAULT_MAX_RETRIES_PER_ITEM)
        or DEFAULT_MAX_RETRIES_PER_ITEM
    )
    max_batch_retries = int(
        analyzer.get("math_llmaj_max_batch_retries", DEFAULT_MAX_BATCH_RETRIES)
        or DEFAULT_MAX_BATCH_RETRIES
    )
    config_fp = _label_config_fingerprint(state)
    _mark("load_ms", t_load)

    _emit(
        "开始 Math LLMaJ 错因标注",
        progress=0.0,
        data={
            "records_path": records_path,
            "primary_metric": primary_name,
            "failed_total": len(failed_indices),
            "batch_size": batch_size,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens_per_case": max_output_tokens_per_case,
            "label_schema": LABEL_SCHEMA_VERSION,
            "config_fp": config_fp,
        },
    )

    labeled_records = [dict(r) for r in records]
    for idx, detail in enumerate(details):
        if idx >= len(labeled_records):
            break
        labeled_records[idx] = _normalize_record_fields(labeled_records[idx])
        labeled_records[idx]["passed"] = _normalize_detail_score(detail) != 0.0
        labeled_records[idx]["metric_detail"] = (
            detail if isinstance(detail, dict) else {"score": _normalize_detail_score(detail)}
        )
        if isinstance(extraction_details, list) and idx < len(extraction_details):
            labeled_records[idx].setdefault("metric_details", {})
            if not isinstance(labeled_records[idx]["metric_details"], dict):
                labeled_records[idx]["metric_details"] = {}
            labeled_records[idx]["metric_details"]["extraction_rate"] = extraction_details[idx]

    t_pre = time.perf_counter()
    need_llm: List[Dict[str, Any]] = []
    rule_count = 0
    cache_hits = 0
    outdir = _ensure_outdir(state)
    cache_path = _label_cache_path(state)
    label_cache = _load_label_cache(cache_path)

    for idx in failed_indices:
        rec = labeled_records[idx] if idx < len(labeled_records) else {}
        detail = details[idx] if idx < len(details) else {}
        extraction_detail = (
            extraction_details[idx]
            if isinstance(extraction_details, list) and idx < len(extraction_details)
            else None
        )
        rule = _rule_label(rec, detail, extraction_detail)
        if rule:
            labeled_records[idx] = _attach_judge(rec, rule)
            rule_count += 1
            continue
        extracted = detail.get("extracted") if isinstance(detail, dict) else None
        match_type = detail.get("match_type") if isinstance(detail, dict) else None
        item = {
            "idx": idx,
            "sample_id": rec.get("id") or rec.get("unique_id") or idx,
            "question": rec.get("question") or rec.get("problem") or "",
            "target": rec.get("target") or rec.get("answer") or "",
            "prediction": rec.get("generated_ans") or rec.get("prediction") or "",
            "extracted": extracted,
            "match_type": match_type,
        }
        key = _cache_key(item, config_fp)
        cached = label_cache.get(key)
        if isinstance(cached, dict) and _is_cacheable_label(cached):
            label = dict(cached)
            label["cache_hit"] = True
            label["origin_source"] = (
                cached.get("origin_source")
                or (cached.get("label_source") if cached.get("label_source") != "cache" else "llm")
                or "llm"
            )
            label["label_source"] = "cache"
            labeled_records[idx] = _attach_judge(rec, label)
            cache_hits += 1
            continue
        item["cache_key"] = key
        need_llm.append(item)
    _mark("rule_cache_ms", t_pre)

    llm_count = 0
    low_conf_count = 0
    call_stats = LlmCallStats()
    t_llm = time.perf_counter()
    packed_batches: List[List[Dict[str, Any]]] = []
    if need_llm:
        packed_batches = _pack_batches(
            need_llm,
            max_items=batch_size,
            max_input_tokens=max_input_tokens,
        )
        total = len(need_llm)
        done = 0
        for batch_i, batch in enumerate(packed_batches):
            out_budget = _output_token_budget(len(batch), max_output_tokens_per_case)
            per_case = max_output_tokens_per_case

            def _make_llm(size: int, _per_case: int = per_case) -> ChatOpenAI:
                return _init_model(
                    state,
                    max_tokens=_output_token_budget(size, _per_case),
                )

            batch_labels = _invoke_label_batch(
                _make_llm(len(batch)),
                batch,
                call_stats,
                max_retries_per_item=max_retries_per_item,
                max_batch_retries=max_batch_retries,
                max_tokens=out_budget,
                make_llm=_make_llm,
            )
            for item in batch:
                idx = int(item["idx"])
                label = batch_labels.get(idx) or _failed_label("批次结果缺失", "llm_missing_case")
                label.setdefault("origin_source", label.get("label_source") or "llm")
                pred_len = len(str(item.get("prediction") or ""))
                q_len = len(str(item.get("question") or ""))
                if pred_len > PREDICTION_CLIP or q_len > QUESTION_CLIP:
                    label["context_truncated"] = True
                    if float(label.get("confidence") or 0) < 0.85:
                        label["needs_review"] = True
                labeled_records[idx] = _attach_judge(labeled_records[idx], label)
                judge = labeled_records[idx].get("judge") or {}
                if judge.get("confidence_cleared"):
                    low_conf_count += 1
                if judge.get("tags"):
                    llm_count += 1
                if _is_cacheable_label(label):
                    cache_store = {
                        "tags": list(label.get("tags") or []),
                        "reason": label.get("reason") or "",
                        "short_critique": label.get("short_critique") or label.get("reason") or "",
                        "overall_error_tag": label.get("overall_error_tag") or (
                            (label.get("tags") or [""])[0]
                        ),
                        "confidence": float(label.get("confidence") or 0.0),
                        "domain": label.get("domain") or "unknown",
                        "first_error_step": label.get("first_error_step") or "",
                        "evidence_quote": label.get("evidence_quote") or "",
                        "repair_target": label.get("repair_target") or "",
                        "needs_review": bool(label.get("needs_review", False)),
                        "context_truncated": bool(label.get("context_truncated", False)),
                        "label_source": "llm",
                        "origin_source": "llm",
                    }
                    label_cache[item["cache_key"]] = cache_store
            _save_label_cache(cache_path, label_cache)
            done += len(batch)
            progress = 0.15 + 0.7 * min(1.0, done / max(total, 1))
            _emit(
                f"已标注失败样本 {done}/{total}",
                progress=progress,
                data={
                    "batch_index": batch_i,
                    "batch_size": len(batch),
                    "packed_batches": len(packed_batches),
                    "output_token_budget": out_budget,
                    "llm_requests": call_stats.requests,
                    "cache_hits": cache_hits,
                },
            )
    _mark("llm_ms", t_llm)

    unresolved_indices = [
        idx
        for idx in failed_indices
        if idx >= len(labeled_records)
        or not (
            (labeled_records[idx].get("judge") or {}).get("overall_error_tag")
            if isinstance(labeled_records[idx].get("judge"), dict)
            else ""
        )
    ]
    if unresolved_indices:
        preview = ", ".join(str(idx) for idx in unresolved_indices[:10])
        raise RuntimeError(
            "Math LLMaJ 未完成全部失败样本判因，已停止生成报告；"
            f"缺少明确标签 {len(unresolved_indices)} 条（索引：{preview}）。"
            "请检查模型服务后使用同一 version_id 续跑。"
        )

    t_write = time.perf_counter()
    obtainer_actions = []
    needs_review_count = 0
    tagged_total = 0
    actionable_total = 0
    diagnostic_unknown = 0
    metric_anomaly_count = 0
    step_construction_count = 0
    whole_case_construction_count = 0
    process_counts = {k: 0 for k in PROCESS_STATUS}
    evidence_valid_count = 0
    diagnosis_counts = {k: 0 for k in DIAGNOSIS_STATUS}
    bucket_distribution: Dict[str, int] = {}
    overall_tag_distribution: Dict[str, int] = {}
    short_critique_count = 0
    for idx in failed_indices:
        if idx >= len(labeled_records):
            continue
        rec = dict(labeled_records[idx])
        rec["idx"] = idx
        judge = rec.get("judge") if isinstance(rec.get("judge"), dict) else {}
        tags = list(judge.get("tags") or [])
        overall_tag = str(judge.get("overall_error_tag") or "").strip()
        if overall_tag:
            overall_tag_distribution[overall_tag] = overall_tag_distribution.get(overall_tag, 0) + 1
        if str(judge.get("short_critique") or "").strip():
            short_critique_count += 1
        if tags:
            tagged_total += 1
        if judge.get("needs_review") or not tags:
            needs_review_count += 1
        if judge.get("diagnosis_status") == "unknown" or not tags:
            diagnostic_unknown += 1
        if judge.get("diagnosis_status") == "metric_anomaly":
            metric_anomaly_count += 1
        elif judge.get("construction_scope") == "step":
            step_construction_count += 1
        elif judge.get("construction_scope") == "whole_case":
            whole_case_construction_count += 1
        ps = str(judge.get("process_status") or "")
        if ps in process_counts:
            process_counts[ps] += 1
        ds = str(judge.get("diagnosis_status") or "")
        if ds in diagnosis_counts:
            diagnosis_counts[ds] += 1
        pred = str(rec.get("generated_ans") or rec.get("prediction") or "")
        ev = str(judge.get("evidence_quote") or "")
        if judge.get("evidence_valid") or (
            tags and ev and (str(judge.get("origin_source") or "").startswith("rule") or _evidence_supported(ev, pred))
        ):
            evidence_valid_count += 1
        action = _build_obtainer_action(rec)
        if action:
            obtainer_actions.append(action)
            actionable_total += 1
            b = str(action.get("capability_bucket") or "unknown")
            bucket_distribution[b] = bucket_distribution.get(b, 0) + 1

    aggregated_actions = aggregate_obtainer_actions(obtainer_actions)

    ts = time.strftime("%Y%m%d_%H%M%S")
    public_records = _build_enriched_oj_records(
        source_records,
        labeled_records,
        failed_indices,
    )
    enriched_oj_path = _write_enriched_oj(
        str(records_path),
        outdir,
        ts,
        public_records,
    )

    stats_payload = {
        "failed_total": len(failed_indices),
        "tagged_total": tagged_total,
        "actionable_total": actionable_total,
        "actionable_count": actionable_total,
        "diagnosed_count": diagnosis_counts.get("diagnosed", 0),
        "review_required_count": diagnosis_counts.get("review_required", 0),
        "rule_confirmed_count": diagnosis_counts.get("rule_confirmed", 0),
        "unknown_count": diagnosis_counts.get("unknown", 0),
        "review_required": needs_review_count,
        "diagnostic_unknown": diagnostic_unknown,
        "metric_anomaly_count": metric_anomaly_count,
        "step_construction_count": step_construction_count,
        "whole_case_construction_count": whole_case_construction_count,
        "process_absent_count": process_counts.get("absent", 0),
        "process_incomplete_count": process_counts.get("incomplete", 0),
        "process_superficial_count": process_counts.get("superficial", 0),
        "process_substantive_count": process_counts.get("substantive", 0),
        "evidence_valid_count": evidence_valid_count,
        "needs_review_count": needs_review_count,
        "diagnosis_distribution": diagnosis_counts,
        "overall_tag_distribution": overall_tag_distribution,
        "short_critique_count": short_critique_count,
        "bucket_distribution": bucket_distribution,
        "action_distribution": dict(bucket_distribution),
        "actionable_by_bucket": dict(bucket_distribution),
        "rule_labeled": rule_count,
        "cache_hits": cache_hits,
        "llm_candidates": len(need_llm),
        "llm_packed_batches": len(packed_batches),
        "llm_labeled_with_tags": llm_count,
        "low_confidence_cleared": low_conf_count,
        "needs_review_or_unknown": needs_review_count,
        "obtainer_actions_per_case": len(obtainer_actions),
        "obtainer_actions_aggregated": len(aggregated_actions),
        "obtainer_actions": len(obtainer_actions),
        "label_cache_path": str(cache_path.resolve()),
        "label_schema": LABEL_SCHEMA_VERSION,
        "config_fp": config_fp,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens_per_case": max_output_tokens_per_case,
        **call_stats.as_dict(),
        "stage_timing_ms": stage_timing,
    }
    _mark("write_ms", t_write)
    stage_timing["total_ms"] = round((time.perf_counter() - t_node) * 1000.0, 1)
    stats_payload["stage_timing_ms"] = stage_timing

    metric_result = dict(metric_result)
    metric_result["alignment"] = {
        **(metric_result.get("alignment") or {}),
        "source_path": str(records_path),
        "path": str(enriched_oj_path.resolve()),
        "mode": "records",
        "labeled": True,
    }
    analyzer["metric_eval_results"] = metric_result
    state["eval_results"] = metric_result
    analyzer["labeled_records"] = labeled_records
    analyzer["labeled_records_path"] = str(enriched_oj_path.resolve())
    analyzer["enriched_oj_path"] = str(enriched_oj_path.resolve())
    analyzer["analyze_output_result_path"] = str(enriched_oj_path.resolve())
    analyzer.pop("labeled_failed_cases_path", None)
    analyzer.pop("obtainer_actions_path", None)
    analyzer["obtainer_actions"] = obtainer_actions
    analyzer["obtainer_actions_aggregated"] = aggregated_actions
    analyzer["math_llmaj_stats"] = stats_payload
    timing = analyzer.setdefault("stage_timing_ms", {})
    timing["label"] = stage_timing

    _emit(
        "Math LLMaJ 标注完成",
        progress=1.0,
        data=analyzer["math_llmaj_stats"],
    )
    return state
