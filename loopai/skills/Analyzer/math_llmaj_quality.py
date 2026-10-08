# -*- coding: utf-8 -*-
"""Math LLMaJ quality evaluation + obtainer action aggregation helpers.

Online gate (used by ``math_llmaj_label_node``):
- process completeness overrides
- evidence grounding
- diagnosis_status / actionable

Offline:
- gold evaluation
- obtainer action aggregation

Keeps Analyzer → Obtainer contract backward compatible:
- ``per_case_actions``: one action per labeled failure (legacy)
- ``aggregated_actions``: bucket×domain groups with seed ids + budget
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

MATH_TAG_WHITELIST = (
    "评测异常",
    "输出格式错误",
    "计算错误",
    "化简错误",
    "题意理解错误",
    "公式使用错误或遗漏",
    "答案与过程不符",
    "答题步骤不完整",
)

MATH_TAG_DESCRIPTIONS = {
    "评测异常": (
        "模型作答与参考答案等价或本身正确，但答案提取、格式归一化或等价判定未能识别；"
        "该类样本用于修复 Metric，不进入模型训练数据。"
    ),
    "输出格式错误": (
        "输出为空、格式不符合题目要求、受到模板重复污染，或没有给出可稳定提取的最终答案。"
    ),
    "计算错误": "运算错误。",
    "化简错误": (
        "在代数恒等变换、展开、因式分解或符号化简过程中进行了不等价变换。"
    ),
    "题意理解错误": (
        "没有正确理解题目条件、变量关系或求解目标，导致建模、列式或后续思路偏离题意。"
    ),
    "公式使用错误或遗漏": (
        "许多数学简答题会涉及到某些常见的公式，学生在解题时可能忘记使用这些公式，"
        "或者在使用公式时出了问题，比如公式套用错误或遗漏了条件。"
    ),
    "答案与过程不符": (
        "部分学生在解答过程中虽然写出了完整的步骤，但在最后填写答案时不注意与步骤一致，"
        "导致答案与前面过程不符，最终得分受到影响。"
    ),
    "答题步骤不完整": "在解题过程中没有展示完整的步骤或推理过程。",
}

PROCESS_STATUS = ("absent", "incomplete", "superficial", "substantive")
DIAGNOSIS_STATUS = (
    "diagnosed",
    "review_required",
    "unknown",
    "rule_confirmed",
    "metric_anomaly",
)
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
EVIDENCE_CLIP = 160
FIRST_ERROR_CLIP = 80
REASON_CLIP = 80

BUCKET_FOR_TAG = {
    "评测异常": "math_metric_anomaly",
    "输出格式错误": "math_output_contract",
    "计算错误": "math_arithmetic_calculation",
    "化简错误": "math_algebra_symbolic",
    "题意理解错误": "math_modeling",
    "公式使用错误或遗漏": "math_strategy_theorem",
    "答案与过程不符": "math_reasoning_consistency",
    "答题步骤不完整": "math_verification_completeness",
}

_METRIC_ANOMALY_POSITIVE_RE = re.compile(
    r"(?:最终(?:答案|结果)|答案|结果|作答|解答).{0,12}与(?:参考|标准)答案(?:完全)?一致|"
    r"(?:模型|作答|答案|结果|过程|推导|解答|求解|计算|算式|运算|列式|展开|化简|翻译|表达式).{0,24}"
    r"(?:正确|无误|准确|等价|吻合|符合(?:题意|要求|标准答案))|"
    r"(?:正确|无误|准确|等价).{0,18}(?:答案|结果|过程|推导|解答|求解)|"
    r"未发现(?:明显|实质)?错误|无(?:明显|实质)?错误|无(?:明显|实质)?首错|"
    r"(?:题目|本题|该题|作答).{0,8}(?:作对|答对)|无需修订|"
    r"评测.{0,16}(?:误判|偏差|不匹配|匹配问题|提取错误)|"
    r"被判(?:定为)?失败.{0,18}(?:评测|系统|格式|匹配)",
    re.IGNORECASE,
)
_METRIC_ANOMALY_NEGATIVE_RE = re.compile(
    r"(?:但|然而|不过|可是).{0,24}(?:存在|出现|导致|造成|仍有).{0,10}(?:错误|有误|不符|遗漏)|"
    r"最终(?:答案|结果)\s*(?:错误|有误|不符)|"
    r"(?:导致|因此).{0,12}(?:答案|结果|判定).{0,8}(?:错误|失败|不符)",
    re.IGNORECASE,
)
_STRONG_METRIC_ANOMALY_RE = re.compile(
    r"(?:题目|本题|该题|作答|模型(?:答案|作答)|最终(?:答案|结果)|答案|结果|过程|推导|解答|求解)"
    r".{0,20}(?:正确|无误|准确|等价|作对|答对).{0,60}"
    r"(?:评测|指标|系统|格式|提取|匹配|判定).{0,30}"
    r"(?:误判|偏差|异常|错误|失败|不匹配|未通过)|"
    r"被判(?:定为)?失败.{0,30}(?:评测|指标|系统|格式|提取|匹配)",
    re.IGNORECASE,
)

CONSTRUCTION_MODES = (
    "stepwise_correction",
    "contrastive_repair",
    "verification",
)

DEFAULT_ACCEPTANCE = (
    "保留完整推导",
    "明确指出首个错误步骤",
    "最终答案可验证",
)


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return rows
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def write_jsonl(path: str | Path, rows: Sequence[Dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def primary_tag(tags: Any) -> Optional[str]:
    if isinstance(tags, str) and tags.strip():
        return tags.strip()
    if isinstance(tags, list) and tags:
        t = str(tags[0] or "").strip()
        return t or None
    return None


def case_id_of(rec: Dict[str, Any]) -> str:
    for key in ("case_id", "id", "unique_id", "idx"):
        if rec.get(key) is not None and str(rec.get(key)) != "":
            return str(rec.get(key))
    seed = rec.get("seed_bad_case") if isinstance(rec.get("seed_bad_case"), dict) else {}
    if seed.get("id") is not None:
        return str(seed.get("id"))
    return ""


def pred_label_from_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    judge = rec.get("judge") if isinstance(rec.get("judge"), dict) else {}
    tags = judge.get("tags") or rec.get("error_tags") or []
    tag = str(judge.get("overall_error_tag") or "").strip() or primary_tag(tags)
    conf = judge.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else None
    except Exception:
        conf_f = None
    needs_review = bool(judge.get("needs_review"))
    is_unknown = (not tag) or needs_review or judge.get("stage") == "math_unknown"
    return {
        "case_id": case_id_of(rec),
        "pred_tag": tag,
        "overall_error_tag": tag,
        "pred_domain": judge.get("domain") or rec.get("domain") or "unknown",
        "pred_confidence": conf_f,
        "needs_review": needs_review,
        "is_unknown": is_unknown,
        "evidence_quote": judge.get("evidence_quote") or "",
        "first_error_step": judge.get("first_error_step") or "",
        "short_critique": judge.get("short_critique") or judge.get("reason") or "",
        "context_truncated": bool(judge.get("context_truncated")),
    }


def export_gold_template_from_labeled(
    labeled_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build human-annotation template; gold fields left blank for filling."""
    out: List[Dict[str, Any]] = []
    for rec in labeled_rows:
        pred = pred_label_from_record(rec)
        problem = rec.get("question") or rec.get("problem") or ""
        target = rec.get("target") if rec.get("target") is not None else rec.get("answer")
        wrong = rec.get("generated_ans") or rec.get("prediction") or ""
        out.append(
            {
                "case_id": pred["case_id"],
                "idx": rec.get("idx"),
                "gold_tag": "",
                "gold_domain": "",
                "is_diagnosable": None,
                "usable_for_construction": None,
                "notes": "",
                "pred_tag": pred["pred_tag"],
                "pred_short_critique": pred["short_critique"],
                "pred_domain": pred["pred_domain"],
                "pred_confidence": pred["pred_confidence"],
                "pred_needs_review": pred["needs_review"],
                "problem_preview": str(problem)[:400],
                "gold_answer": target,
                "wrong_solution_preview": str(wrong)[:500],
            }
        )
    return out


def _safe_div(n: float, d: float) -> float:
    return float(n) / float(d) if d else 0.0


def _f1(p: float, r: float) -> float:
    if p + r <= 0:
        return 0.0
    return 2.0 * p * r / (p + r)


def evaluate_labels(
    gold_rows: Sequence[Dict[str, Any]],
    pred_rows: Sequence[Dict[str, Any]],
    *,
    high_conf_threshold: float = 0.8,
) -> Dict[str, Any]:
    """Offline quality metrics against human gold."""
    pred_by_id: Dict[str, Dict[str, Any]] = {}
    for r in pred_rows:
        p = pred_label_from_record(r)
        if p["case_id"]:
            pred_by_id[p["case_id"]] = p

    paired: List[Dict[str, Any]] = []
    missing_pred = 0
    for g in gold_rows:
        cid = str(g.get("case_id") or "")
        gold_tag = str(g.get("gold_tag") or "").strip()
        if not cid or not gold_tag:
            continue
        pred = pred_by_id.get(cid)
        if not pred:
            missing_pred += 1
            continue
        paired.append({"gold": g, "pred": pred})

    labels = list(MATH_TAG_WHITELIST)
    conf_mat: Dict[str, Dict[str, int]] = {
        g: {p: 0 for p in labels + ["__empty__"]} for g in labels + ["__empty__"]
    }
    correct = 0
    high_correct = 0
    high_total = 0
    unknown_pred = 0
    unknown_should = 0
    unknown_correct = 0
    per_class = {t: {"tp": 0, "fp": 0, "fn": 0} for t in labels}

    for item in paired:
        g = item["gold"]
        p = item["pred"]
        gold_tag = str(g.get("gold_tag") or "").strip() or "__empty__"
        pred_tag = p.get("pred_tag") or "__empty__"
        if gold_tag not in conf_mat:
            conf_mat[gold_tag] = {x: 0 for x in labels + ["__empty__"]}
        if pred_tag not in conf_mat[gold_tag]:
            for gt in conf_mat:
                conf_mat[gt].setdefault(pred_tag, 0)
        conf_mat[gold_tag][pred_tag] = conf_mat[gold_tag].get(pred_tag, 0) + 1

        if g.get("is_diagnosable") is False:
            unknown_should += 1
            if p.get("is_unknown"):
                unknown_correct += 1
        if p.get("is_unknown"):
            unknown_pred += 1

        if gold_tag == pred_tag and gold_tag != "__empty__":
            correct += 1

        conf = p.get("pred_confidence")
        if conf is not None and float(conf) >= high_conf_threshold and not p.get("is_unknown"):
            high_total += 1
            if gold_tag == pred_tag:
                high_correct += 1

        for t in labels:
            if gold_tag == t and pred_tag == t:
                per_class[t]["tp"] += 1
            elif pred_tag == t and gold_tag != t:
                per_class[t]["fp"] += 1
            elif gold_tag == t and pred_tag != t:
                per_class[t]["fn"] += 1

    n = len(paired)
    f1s = []
    class_report = {}
    for t, c in per_class.items():
        prec = _safe_div(c["tp"], c["tp"] + c["fp"])
        rec = _safe_div(c["tp"], c["tp"] + c["fn"])
        f1 = _f1(prec, rec)
        if c["tp"] + c["fp"] + c["fn"] > 0:
            f1s.append(f1)
        class_report[t] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            **c,
        }

    return {
        "n_gold_complete": n,
        "missing_pred": missing_pred,
        "accuracy": round(_safe_div(correct, n), 4),
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "high_conf_threshold": high_conf_threshold,
        "high_conf_precision": round(_safe_div(high_correct, high_total), 4),
        "high_conf_support": high_total,
        "unknown_precision": round(_safe_div(unknown_correct, unknown_pred), 4),
        "unknown_pred_count": unknown_pred,
        "unknown_should_count": unknown_should,
        "per_class": class_report,
        "confusion_matrix": conf_mat,
        "target": "high_conf_precision >= 0.8",
        "pass_high_conf_precision": (_safe_div(high_correct, high_total) >= 0.8) if high_total else False,
    }


def aggregate_obtainer_actions(
    per_case_actions: Sequence[Dict[str, Any]],
    *,
    max_seeds_per_group: int = 3,
    default_target_count: int = 20,
    min_confidence: float = 0.6,
) -> List[Dict[str, Any]]:
    """Group per-case construct actions into bucket×domain training demands."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for act in per_case_actions:
        if not isinstance(act, dict):
            continue
        if act.get("mode") and act.get("mode") != "construct":
            continue
        conf = act.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 1.0
        except Exception:
            conf_f = 1.0
        if conf_f < min_confidence:
            continue
        if act.get("needs_review"):
            continue
        bucket = str(act.get("capability_bucket") or "diagnostic_unknown")
        domain = str(act.get("domain") or "unknown")
        groups[(bucket, domain)].append(act)

    aggregated: List[Dict[str, Any]] = []
    for i, ((bucket, domain), acts) in enumerate(
        sorted(groups.items(), key=lambda x: (-len(x[1]), x[0][0], x[0][1]))
    ):
        ranked = sorted(acts, key=lambda a: float(a.get("confidence") or 0.0), reverse=True)
        seeds = []
        seed_ids = []
        tags_counter: Counter = Counter()
        for a in ranked[: max(1, max_seeds_per_group)]:
            seed = a.get("seed_bad_case") if isinstance(a.get("seed_bad_case"), dict) else {}
            sid = seed.get("id")
            if sid is not None:
                seed_ids.append(sid)
            if seed:
                seeds.append(
                    {
                        "id": sid,
                        "problem": seed.get("problem"),
                        "gold_answer": seed.get("gold_answer"),
                        "short_critique": seed.get("short_critique"),
                        "evidence_quote": a.get("evidence_quote") or seed.get("evidence_quote"),
                        "first_error_step": a.get("first_error_step")
                        or seed.get("first_error_step"),
                    }
                )
            for t in a.get("error_tags") or []:
                tags_counter[str(t)] += 1
        sample_count = len(acts)
        target = int(max(10, min(80, default_target_count * max(1, math.ceil(sample_count / 2)))))
        is_stable = sample_count >= 8
        aggregated.append(
            {
                "action_id": f"agg_{i:03d}_{bucket}_{domain}",
                "mode": "construct_budget",
                "schema_version": "obtainer_action_agg_v1",
                "capability_bucket": bucket,
                "domain": domain,
                "error_tags_top": [t for t, _ in tags_counter.most_common(3)],
                "seed_bad_case_ids": seed_ids,
                "seed_bad_cases": seeds,
                "sample_count": sample_count,
                "target_count": target,
                "confidence": round(
                    sum(float(a.get("confidence") or 0.0) for a in acts) / max(len(acts), 1),
                    4,
                ),
                "stability": {
                    "sample_count": sample_count,
                    "is_stable": is_stable,
                    "note": (
                        "小样本，推荐比例仅作 pilot 参考"
                        if not is_stable
                        else "样本较多，可作配比参考"
                    ),
                },
                "construction_modes": list(CONSTRUCTION_MODES),
                "acceptance_criteria": list(DEFAULT_ACCEPTANCE),
                "source": {
                    "analyzer": "math_llmaj_label",
                    "aggregation": "bucket_domain",
                    "compatible_with": ["Judger step jsonl", "Obtainer construct"],
                },
            }
        )
    return aggregated


def assert_math_field_aliases(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Judger Math fields onto Analyzer/Obtainer expected names."""
    out = dict(rec)
    if not out.get("question"):
        out["question"] = out.get("problem") or out.get("prompt") or out.get("input")
    if out.get("target") is None or out.get("target") == "":
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


def validate_obtainer_seed(seed: Dict[str, Any]) -> List[str]:
    errors = []
    if seed.get("problem") in (None, ""):
        errors.append("problem_empty")
    if seed.get("gold_answer") in (None, ""):
        errors.append("gold_answer_empty")
    if seed.get("wrong_solution") in (None, ""):
        errors.append("wrong_solution_empty")
    return errors


def normalize_evidence_text(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def normalize_short_critique(value: Any) -> str:
    """Normalize a model critique to one compact sentence."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    sentence_end = re.search(r"[。！？!?](?:\s|$)", text)
    if sentence_end:
        text = text[: sentence_end.end()].strip()
    else:
        english_end = re.search(r"(?<!\d)\.(?:\s|$)", text)
        if english_end:
            text = text[: english_end.end()].strip()
    return text[:REASON_CLIP]


def looks_like_metric_anomaly_critique(value: Any) -> bool:
    """Return true only for an explicitly positive solution audit."""
    text = normalize_short_critique(value)
    if not text:
        return False
    if _STRONG_METRIC_ANOMALY_RE.search(text):
        return True
    if _METRIC_ANOMALY_NEGATIVE_RE.search(text):
        return False
    return bool(_METRIC_ANOMALY_POSITIVE_RE.search(text))


def infer_math_tag_from_critique(value: Any) -> Optional[str]:
    """Recover a concrete tag from a model critique without using a catch-all bucket."""
    text = normalize_short_critique(value)
    if not text:
        return None
    if looks_like_metric_anomaly_critique(text):
        return "评测异常"
    mappings = (
        ("输出格式错误", ("输出格式", "无法提取", "提取失败", "模板污染", "空答案")),
        ("答题步骤不完整", ("步骤不完整", "过程不完整", "未展示", "中途停止", "推导中断", "漏答")),
        ("公式使用错误或遗漏", ("公式", "定理", "方法选择", "策略错误", "概念错误")),
        ("化简错误", ("化简", "展开", "因式分解", "等价变换", "符号变换")),
        ("题意理解错误", ("题意", "理解错误", "建模", "列式", "变量关系", "条件理解")),
        ("答案与过程不符", ("答案与过程", "前后矛盾", "结论不一致", "抄写答案")),
        ("计算错误", ("计算错误", "运算错误", "算错", "数值错误", "代入错误", "舍入")),
    )
    for tag, markers in mappings:
        if any(marker in text for marker in markers):
            return tag
    return None


def split_evidence_fragments(evidence: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\|\||；|;", str(evidence or "")) if p.strip()]
    if parts:
        return parts
    text = str(evidence or "").strip()
    return [text] if text else []


def evidence_supported(evidence: str, prediction: str) -> bool:
    """Require evidence quote to appear in the model solution (fuzzy whitespace)."""
    ev = str(evidence or "").strip()
    if not ev:
        return False
    # synthetic placeholders used by process/rule gates
    if ev.startswith("[empty") or ev in {"extraction_rate=0", "user/Answer repetition"}:
        return True
    pred = str(prediction or "")
    if ev in pred:
        return True
    parts = split_evidence_fragments(ev)
    if len(parts) >= 2:
        return all(evidence_supported(p, pred) for p in parts)
    nev = normalize_evidence_text(ev)
    npred = normalize_evidence_text(pred)
    if len(nev) >= 6 and nev in npred:
        return True
    if len(nev) >= 3 and nev in npred:
        return True
    return False


def infer_process_status(prediction: Any) -> str:
    """Local heuristic for process completeness."""
    text = str(prediction or "").strip()
    if not text:
        return "absent"
    compact = re.sub(r"\s+", " ", text)
    if len(compact) <= 80 and re.search(
        r"(?i)^(the\s+)?answer\s+is\b|^answer\s*[:：]|^\\boxed\{",
        compact,
    ):
        return "absent"
    if len(compact) <= 40 and not re.search(r"[=\\]|step|证明|推导|therefore|hence", compact, re.I):
        return "absent"
    low = text.lower()
    incomplete_markers = (
        "cannot compute",
        "cannot calculate",
        "not provided",
        "if the roots are found",
        "will lead to the correct",
        "reach max function",
        "枚举后中断",
        "未求解",
        "无法计算",
        "暂不计算",
    )
    if any(m in low or m in text for m in incomplete_markers):
        return "incomplete"
    if re.search(r"(?i)case\s+\d+\s*[:：].{0,80}$", text[-120:]):
        return "incomplete"
    eq_count = len(re.findall(r"[=\\]|\\frac|\\sum|\\binom", text))
    step_count = len(re.findall(r"(?i)step\s*\d|###\s*step|第[一二三四五六七八九\d]+步", text))
    if len(text) < 220 and eq_count < 2 and step_count < 2:
        return "superficial"
    if eq_count >= 2 or step_count >= 2 or len(text) >= 400:
        return "substantive"
    return "superficial"


def _origin_is_llm(origin_source: str) -> bool:
    src = str(origin_source or "")
    if src.startswith("rule"):
        return False
    return src.startswith("llm") or src in {"llm", "cache"}


def _find_evidence_span(evidence: str, prediction: str) -> int:
    """Return start index of evidence in prediction, or -1."""
    ev = str(evidence or "").strip()
    pred = str(prediction or "")
    if not ev or not pred:
        return -1
    primary = split_evidence_fragments(ev)[0]
    idx = pred.find(primary)
    if idx >= 0:
        return idx
    nev = normalize_evidence_text(primary)
    npred = normalize_evidence_text(pred)
    pos = npred.find(nev) if len(nev) >= 3 else -1
    if pos < 0:
        return -1
    # approximate map back to original index
    return min(len(pred) - 1, max(0, int(pos * len(pred) / max(len(npred), 1))))


def _find_early_simplification_quote(prediction: str, before_idx: int) -> Optional[str]:
    """Light heuristic: earlier algebraic assignment before a late arithmetic cite."""
    head = str(prediction or "")[: max(0, before_idx)]
    if not head:
        return None
    patterns = (
        r"S\s*=\s*[^\n,]{3,40}",
        r"(?:sum|series)\s*=\s*[^\n,]{3,40}",
        r"[A-Za-z]\\?\w*\s*=\s*\\frac\{[^}]+\}\{[^}]+\}",
        r"[A-Za-z]\\?\w*\s*=\s*\d+\s*/\s*\d+",
        r"=\s*\\frac\{[^}]+\}\{[^}]+\}",
        r"=\s*\d+\s*/\s*\d+",
    )
    best = None
    best_pos = -1
    for pat in patterns:
        for m in re.finditer(pat, head, flags=re.I):
            if m.start() >= best_pos:
                best_pos = m.start()
                best = m.group(0).strip()
    if best and len(best) >= 5:
        return best[:EVIDENCE_CLIP]
    return None


def _looks_like_late_modular_or_final_arith(evidence: str) -> bool:
    ev = str(evidence or "").lower()
    return bool(
        re.search(r"\\equiv|\\pmod|mod\s*\d|%\s*\d|10\^\{\d+\}|final\s+arithmetic|2\s*\+\s*2\s*=", ev)
        or ("mod" in ev and any(ch.isdigit() for ch in ev))
    )


def correct_late_arithmetic_adsorption(
    tags: List[str],
    evidence: str,
    first_error_step: str,
    prediction: str,
) -> Tuple[List[str], str, str, Optional[str]]:
    """If LLM cites a late mod/arith error but an earlier simplification exists, prefer that.

    Returns (tags, evidence, first_error_step, note_or_none).
    """
    if "计算错误" not in tags:
        return tags, evidence, first_error_step, None
    if not _looks_like_late_modular_or_final_arith(evidence):
        return tags, evidence, first_error_step, None
    pred = str(prediction or "")
    if len(pred) < 200:
        return tags, evidence, first_error_step, None
    span = _find_evidence_span(evidence, pred)
    if span < 0:
        return tags, evidence, first_error_step, None
    # evidence in last ~40% of solution → likely end-adsorption
    if span < int(len(pred) * 0.55):
        return tags, evidence, first_error_step, None
    early = _find_early_simplification_quote(pred, span)
    if not early:
        # cannot relocate; force review rather than keep arithmetic as actionable
        return tags, evidence, first_error_step, "late_arith_no_early_quote"
    new_tags = ["化简错误"]
    new_first = first_error_step if "化简" in str(first_error_step) or "S" in str(first_error_step) else early[:FIRST_ERROR_CLIP]
    return new_tags, early, new_first, "late_arith_to_early_simplify"


def assess_math_label_quality(
    label: Dict[str, Any],
    prediction: str,
    *,
    local_process_status: Optional[str] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> Dict[str, Any]:
    """Resolve every labeled failure to a concrete downstream route.

    Exact step evidence enables step-level repair. A valid tag with weaker
    evidence still supports whole-case contrastive construction, so it must
    not disappear from bucket statistics. Mathematically correct responses
    rejected by the metric are routed to metric audit instead of training.
    """
    out = dict(label)
    pred = str(prediction or "")
    ps = str(out.get("process_status") or "").strip().lower()
    local_ps = local_process_status or infer_process_status(pred)
    if ps not in PROCESS_STATUS:
        ps = local_ps
    if local_ps in {"absent", "incomplete"} and ps == "substantive":
        ps = local_ps
    if local_ps == "absent":
        ps = "absent"
    out["process_status"] = ps

    raw_tags = out.get("tags") or (
        [out.get("overall_error_tag")] if out.get("overall_error_tag") else []
    )
    tags = [t for t in raw_tags if t][:1]
    evidence = str(out.get("evidence_quote") or "").strip()
    first_err = str(out.get("first_error_step") or "").strip()
    short_critique = normalize_short_critique(
        out.get("short_critique") or out.get("reason")
    )
    try:
        confidence = float(out.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    origin = str(out.get("origin_source") or out.get("label_source") or "llm")
    is_rule = origin.startswith("rule")
    gate_notes: List[str] = []
    model_requested_review = bool(out.get("needs_review", False))
    out["needs_review"] = False
    out["confidence_cleared"] = False

    # Older checkpoints cleared an "答案与过程不符" label when the exact
    # contradiction quote was weak, leaving only this migration marker. The
    # case remains useful as whole-case contrastive data, so restore the
    # semantic label instead of carrying an unresolved legacy row.
    if not tags and "inconsistency_demote" in str(out.get("quality_reason") or ""):
        tags = ["答案与过程不符"]
        confidence = max(confidence, 0.65)
        gate_notes.append("legacy_inconsistency_restored")

    if not tags:
        inferred_tag = infer_math_tag_from_critique(short_critique)
        if inferred_tag:
            tags = [inferred_tag]
            confidence = max(confidence, 0.82 if inferred_tag == "评测异常" else 0.65)
            gate_notes.append("tag_recovered_from_short_critique")

    is_metric_anomaly = tags == ["评测异常"]
    if model_requested_review and tags:
        gate_notes.append("model_review_resolved_by_routing")
    if (not is_rule) and _origin_is_llm(origin) and confidence < confidence_threshold and tags:
        gate_notes.append("low_confidence_whole_case")

    process_derived = False
    if (not is_rule) and not is_metric_anomaly and ps in {"absent", "incomplete"}:
        tags = ["答题步骤不完整"]
        if not first_err:
            first_err = "过程缺失或中断" if ps == "absent" else "关键步骤缺失/中途停止"
        if not evidence or not evidence_supported(evidence, pred):
            evidence = pred.strip()[:80] if pred.strip() else "[empty/incomplete process]"
        confidence = max(confidence, 0.85)
        out["needs_review"] = False
        out["confidence_cleared"] = False
        process_derived = True
        short_critique = (
            "作答没有展示可核验的解题过程。"
            if ps == "absent"
            else "作答在关键推导完成前中断，解题步骤不完整。"
        )
        gate_notes.append(f"process_{ps}_override")
    elif (not is_rule) and not is_metric_anomaly and ps == "superficial" and "答案与过程不符" in tags:
        tags = ["公式使用错误或遗漏"]
        gate_notes.append("superficial_inconsistency_demote")

    if (not is_rule) and "答案与过程不符" in tags:
        frags = split_evidence_fragments(evidence)
        ok = (
            ps == "substantive"
            and len(frags) >= 2
            and all(evidence_supported(f, pred) for f in frags)
            and bool(first_err)
        )
        if not ok:
            if ps in {"absent", "incomplete"}:
                tags = ["答题步骤不完整"]
                process_derived = True
            elif ps == "superficial":
                tags = ["公式使用错误或遗漏"]
            gate_notes.append("inconsistency_whole_case")

    # Late arithmetic adsorption correction (before evidence gate locks actionable).
    if (not is_rule) and (not process_derived) and tags:
        new_tags, new_ev, new_first, note = correct_late_arithmetic_adsorption(
            tags, evidence, first_err, pred
        )
        if note == "late_arith_to_early_simplify":
            tags, evidence, first_err = new_tags, new_ev, new_first
            short_critique = "解答在较早的化简步骤中出现错误，导致后续计算和结论失效。"
            out["reason"] = (
                str(out.get("reason") or "")[:50] + "；首错前移至早期化简"
            )[:REASON_CLIP]
            gate_notes.append(note)
        elif note == "late_arith_no_early_quote":
            gate_notes.append(note)

    evidence_valid = False
    if is_rule and evidence:
        evidence_valid = True
    elif process_derived and evidence:
        evidence_valid = True
    elif evidence and evidence_supported(evidence, pred):
        evidence_valid = True

    if tags and (not is_rule) and origin.startswith(("llm", "cache")) and (not process_derived):
        if not evidence_valid:
            gate_notes.append("evidence_ungrounded_or_empty")
        if tags and not first_err:
            gate_notes.append("missing_first_error_step")
        if not short_critique:
            gate_notes.append("missing_short_critique")

    if tags and not short_critique:
        short_critique = MATH_TAG_DESCRIPTIONS.get(tags[0], "该样本存在明确数学能力缺口。")
        gate_notes.append("short_critique_filled_from_tag")

    construction_scope = "none"
    resolution_route = "labeling_error"
    needs_review = False
    if is_metric_anomaly:
        diagnosis_status = "metric_anomaly"
        actionable = False
        resolution_route = "metric_audit"
    elif is_rule and tags:
        diagnosis_status = "rule_confirmed"
        actionable = True
        evidence_valid = True
        construction_scope = "step"
        resolution_route = "training_data"
    elif tags:
        diagnosis_status = "diagnosed"
        actionable = True
        resolution_route = "training_data"
        precise_step = bool(
            evidence_valid
            and first_err
            and confidence >= confidence_threshold
            and not (
                "答案与过程不符" in tags
                and len(split_evidence_fragments(evidence)) < 2
            )
        )
        construction_scope = "step" if precise_step else "whole_case"
        if not precise_step:
            gate_notes.append("whole_case_construction")
    else:
        diagnosis_status = "unknown"
        actionable = False
        needs_review = True
        gate_notes.append("unresolved_label")

    reason_gate = ";".join(gate_notes) if gate_notes else "ok"
    return {
        **out,
        "tags": tags,
        "overall_error_tag": tags[0] if tags else "",
        "short_critique": short_critique,
        "reason": short_critique or normalize_short_critique(out.get("reason")),
        "evidence_quote": evidence[:EVIDENCE_CLIP],
        "first_error_step": first_err[:FIRST_ERROR_CLIP],
        "confidence": confidence,
        "needs_review": needs_review,
        "diagnosis_status": diagnosis_status,
        "actionable": actionable,
        "process_status": ps,
        "evidence_valid": evidence_valid,
        "construction_scope": construction_scope,
        "resolution_route": resolution_route,
        "quality_reason": reason_gate,
    }
