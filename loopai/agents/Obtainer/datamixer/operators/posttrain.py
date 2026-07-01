"""Post-training data operators (#19): SFT / DPO / agent hygiene & validation.

The platform declares ``sft / preference / agent_trajectory`` stages but the L4
operators were pretrain-text-centric. These add the stage-specific validation and
dedup that SFT/DPO/RLHF pipelines need so DataMixer can be the single source of
truth for post-training data, not just pretrain corpora.

Validators are ``filter``-kind: ``mode="score"`` annotates (default),
``mode="filter"`` drops the invalid rows. Flags ride in ``tags`` (filter them in
recipes via ``json_extract(tags_json,'$.sft_valid')=1``); prompt dedup writes the
core ``dedup_cluster_id`` / ``is_duplicate``.
"""
from __future__ import annotations

import hashlib
import re

from .base import Batch, Operator, OperatorContext, OperatorSpec, register

_WS = re.compile(r"\s+")
_ROLES = {"system", "user", "assistant", "tool"}
_REFUSAL = re.compile(
    r"\b(i\s+(?:cannot|can't|am unable to|won'?t)|as an ai|i'?m sorry,? but|"
    r"i am not able to|i do not have the ability)\b", re.IGNORECASE)


def _content(row: dict):
    return row.get("content", row)


def _messages(content) -> list | None:
    if isinstance(content, dict) and isinstance(content.get("messages"), list):
        return content["messages"]
    return None


def _prompt_text(content) -> str:
    """Best-effort extraction of the *instruction/prompt* of a sample."""
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return str(content)
    if "prompt" in content:
        return str(content["prompt"])
    if "instruction" in content:
        return str(content["instruction"])
    msgs = _messages(content)
    if msgs:
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                return str(m.get("content", ""))
    return str(content.get("text", ""))


def _response_text(content) -> str:
    if not isinstance(content, dict):
        return ""
    if "response" in content:
        return str(content["response"])
    msgs = _messages(content)
    if msgs:
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                return str(m.get("content", ""))
    return ""


class _Validator(Operator):
    """Base for stage validators: annotate flag/error, optionally drop invalid."""
    flag = "valid"
    err = "error"

    def __init__(self, mode: str = "score"):
        self.mode = mode

    def _check(self, content) -> str | None:
        raise NotImplementedError

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        out = []
        for row in batch:
            err = self._check(_content(row))
            row[self.flag] = err is None
            row[self.err] = err
            if self.mode == "filter" and err is not None:
                continue
            out.append(row)
        return out


@register("sft_validate")
class SFTValidate(_Validator):
    """Validate SFT samples: a messages turn structure (user/assistant
    alternation, starts at user, ends at assistant, no empty/duplicate-role
    turns) or a non-empty instruction+response pair."""
    spec = OperatorSpec("sft_validate", "1.0", "filter",
                        description="Validate SFT dialogue / instruction-response.")
    flag, err = "sft_valid", "sft_error"

    def _check(self, content):
        msgs = _messages(content)
        if msgs is not None:
            turns = [m for m in msgs if isinstance(m, dict)]
            if not turns:
                return "empty messages"
            roles = [t.get("role") for t in turns]
            if any(r not in _ROLES for r in roles):
                return f"unknown role in {roles}"
            if any(not str(t.get("content", "")).strip() for t in turns):
                return "empty turn content"
            convo = [r for r in roles if r in ("user", "assistant")]
            if not convo or convo[0] != "user":
                return "conversation must start with a user turn"
            if convo[-1] != "assistant":
                return "conversation must end with an assistant turn"
            for a, b in zip(convo, convo[1:]):
                if a == b:
                    return f"consecutive {a} turns (must alternate)"
            return None
        if isinstance(content, dict) and ("instruction" in content or "response" in content):
            if not str(content.get("instruction", "")).strip():
                return "empty instruction"
            if not str(content.get("response", "")).strip():
                return "empty response"
            return None
        return "not an SFT sample (no messages / instruction+response)"


@register("preference_validate")
class PreferenceValidate(_Validator):
    """Validate DPO/preference triples: non-empty prompt/chosen/rejected and
    chosen != rejected."""
    spec = OperatorSpec("preference_validate", "1.0", "filter",
                        description="Validate (prompt, chosen, rejected) triples.")
    flag, err = "pref_valid", "pref_error"

    def _check(self, content):
        if not isinstance(content, dict):
            return "not a preference object"
        for k in ("prompt", "chosen", "rejected"):
            if not str(content.get(k, "")).strip():
                return f"empty {k}"
        if str(content["chosen"]).strip() == str(content["rejected"]).strip():
            return "chosen == rejected"
        return None


@register("agent_validate")
class AgentValidate(_Validator):
    """Validate agent trajectories: a non-empty list of step objects."""
    spec = OperatorSpec("agent_validate", "1.0", "filter",
                        description="Validate agent_trajectory structure.")
    flag, err = "agent_valid", "agent_error"

    def _check(self, content):
        traj = content.get("trajectory") if isinstance(content, dict) else None
        if not isinstance(traj, list) or not traj:
            return "missing/empty trajectory"
        if any(not isinstance(s, dict) for s in traj):
            return "trajectory steps must be objects"
        return None


@register("refusal_detect")
class RefusalDetect(Operator):
    """Flag low-quality assistant responses: empty, templated refusal, or highly
    repetitive (degenerate) output. Sets ``response_issue`` (tag)."""
    spec = OperatorSpec("refusal_detect", "1.0", "score",
                        description="Flag empty/refusal/repetitive responses.")

    def __init__(self, min_unique_ratio: float = 0.3):
        self.min_unique_ratio = float(min_unique_ratio)

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        for row in batch:
            resp = _response_text(_content(row))
            issue = None
            words = resp.split()
            if not resp.strip():
                issue = "empty"
            elif _REFUSAL.search(resp):
                issue = "refusal"
            elif len(words) >= 12 and len(set(words)) / len(words) < self.min_unique_ratio:
                issue = "repetitive"
            row["response_issue"] = issue
            row["response_ok"] = issue is None
        return batch


@register("prompt_dedup")
class PromptDedup(Operator):
    """De-duplicate by *prompt* (instruction), not whole content. SFT corpora
    often repeat the same instruction with different answers, over-sampling some
    tasks; this clusters on the normalized prompt and flags repeats."""
    spec = OperatorSpec("prompt_dedup", "1.0", "dedup", stateful=True,
                        description="Exact prompt-level de-duplication.")

    def __init__(self):
        self._seen: dict[str, str] = {}

    def process(self, batch: Batch, ctx: OperatorContext) -> Batch:
        for row in batch:
            norm = _WS.sub(" ", _prompt_text(_content(row)).lower()).strip()
            key = hashlib.blake2b(norm.encode(), digest_size=10).hexdigest()
            if key not in self._seen:
                self._seen[key] = "pc-" + key[:10]
                row["is_duplicate"] = False
            else:
                row["is_duplicate"] = True
            row["dedup_cluster_id"] = self._seen[key]
        return batch
