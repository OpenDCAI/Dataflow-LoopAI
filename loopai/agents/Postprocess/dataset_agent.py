"""
DatasetAgent — per-dataset ReAct agent.

Each instance is scoped to a single dataset directory inside
`hf_datasets/<name>`, `kaggle_datasets/<name>`, or `web_downloads/<name>`.
It uses three tools (file_read, web_search, data_load) in a ReAct loop
driven by the LLM, gathers knowledge, and ultimately produces a
`DatasetMappingPlan`.  After the loop completes, the caller uses the
mapping plan to convert the raw data into intermediate-format JSONL.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Annotated, Dict, List, Optional, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from loopai.logger import get_logger
from .memory import PostprocessMemoryManager
from .schemas import (
    DatasetAgentResult,
    DatasetKnowledgeSummary,
    DatasetMappingPlan,
    DatasetSourceInfo,
)
from .tools import (
    create_apply_skill_tool,
    create_data_load_tool,
    create_file_read_tool,
    create_list_skills_tool,
    create_web_search_tool,
)

logger = get_logger()

MAX_AGENT_ITERATIONS = 15
LOG_MODEL_INPUT_ENABLED = os.getenv("POSTPROCESS_LOG_MODEL_INPUT", "1").lower() in ("1", "true", "yes")
LOG_MODEL_INPUT_MAX_CHARS = int(os.getenv("POSTPROCESS_LOG_MODEL_INPUT_MAX_CHARS", "50000"))


# ---------------------------------------------------------------------------
# Agent internal state (not exposed to LoopAIState)
# ---------------------------------------------------------------------------

class _DatasetAgentState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _load_prompt_template(key: str) -> str:
    prompt_file = os.path.join(os.path.dirname(__file__), "prompts", "postprocess_prompt.json")
    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            prompts = json.load(f)
        return prompts.get(key, "")
    except Exception as e:
        logger.warning(f"[DatasetAgent] Failed to load prompt '{key}': {e}")
        return ""


def _build_system_prompt(category: str) -> str:
    template = _load_prompt_template("dataset_agent_system")
    category_desc = "预训练——将文本字段拼接为连续文本" if category == "PT" else "监督微调——构建多轮对话消息"
    return template.replace("{category_description}", category_desc)


def _build_task_message(
    source_info: DatasetSourceInfo,
    category: str,
    user_query: str,
    datasets_background: str,
) -> str:
    template = _load_prompt_template("dataset_agent_task")
    category_desc = "预训练——将文本字段拼接为连续文本" if category == "PT" else "监督微调——构建多轮对话消息"

    overview_lines = []
    for label, files in [
        ("说明文件", source_info.readme_files),
        ("数据文件", source_info.data_files),
        ("脚本文件", source_info.script_files),
        ("其他文件", source_info.other_files),
    ]:
        if files:
            overview_lines.append(f"- {label}: {', '.join(files)}")
    folder_overview = "\n".join(overview_lines) if overview_lines else "(空文件夹)"

    return (
        template
        .replace("{dataset_name}", source_info.dataset_name)
        .replace("{dataset_dir}", source_info.dataset_dir)
        .replace("{source_type}", source_info.source_type)
        .replace("{category}", category)
        .replace("{category_description}", category_desc)
        .replace("{user_query}", user_query or "(无)")
        .replace("{datasets_background}", datasets_background or "(无)")
        .replace("{folder_overview}", folder_overview)
    )


# ---------------------------------------------------------------------------
# Mapping plan extraction
# ---------------------------------------------------------------------------

def _extract_mapping_plan(
    messages: List[BaseMessage],
    dataset_name: str,
    category: str,
) -> Optional[DatasetMappingPlan]:
    """Try to parse a DatasetMappingPlan from the last AI message."""
    plan, _ = _extract_mapping_plan_with_debug(messages, dataset_name, category)
    return plan


def _extract_mapping_plan_with_debug(
    messages: List[BaseMessage],
    dataset_name: str,
    category: str,
) -> tuple[Optional[DatasetMappingPlan], Dict[str, Any]]:
    """Parse mapping plan and return debug details for diagnostics."""
    debug: Dict[str, Any] = {
        "source": "none",
        "raw_tail": "",
        "candidates": [],
        "selected_candidate_idx": None,
        "parse_error_count": 0,
    }
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        debug["source"] = "last_ai_message"
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        debug["raw_tail"] = text[-2000:]
        # Strip markdown fences
        if "```json" in text:
            text = text.split("```json", 1)[1]
            if "```" in text:
                text = text.rsplit("```", 1)[0]
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]

        text = text.strip()
        if not text.startswith("{"):
            idx = text.find("{")
            if idx >= 0:
                text = text[idx:]
            else:
                continue

        # Try full text first, then brace-matched substring
        candidates = [text]
        brace_depth = 0
        start = text.find("{")
        if start >= 0:
            for i in range(start, len(text)):
                if text[i] == "{":
                    brace_depth += 1
                elif text[i] == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        candidates.append(text[start : i + 1])
                        break
        debug["candidates"] = [c[:2000] for c in candidates]

        for idx, candidate in enumerate(candidates):
            try:
                data = json.loads(candidate)
                data.setdefault("dataset_name", dataset_name)
                data.setdefault("category", category)
                plan = DatasetMappingPlan.model_validate(data)
                debug["selected_candidate_idx"] = idx
                return plan, debug
            except Exception:
                debug["parse_error_count"] += 1
                continue
    return None, debug


def _truncate_for_log(text: Any, limit: int = 400) -> str:
    raw = text if isinstance(text, str) else str(text)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"...(truncated, {len(raw)} chars)"


def _message_to_debug_dict(msg: BaseMessage) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": msg.__class__.__name__,
    }
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    payload["content"] = content
    if isinstance(msg, ToolMessage):
        payload["name"] = msg.name
        payload["tool_call_id"] = msg.tool_call_id
    if isinstance(msg, AIMessage):
        payload["tool_calls"] = getattr(msg, "tool_calls", None)
    return payload


def _collect_called_tools(messages: List[BaseMessage]) -> List[str]:
    called: List[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name:
            called.append(msg.name)
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            called.extend([tc.get("name", "") for tc in msg.tool_calls if tc.get("name")])
    return called


def _validate_mapping_plan_strict(
    plan: DatasetMappingPlan,
    category: str,
) -> Optional[str]:
    if (plan.quality_label or "").lower() == "unqualified":
        if not (plan.quality_reason or "").strip():
            return "quality_label=unqualified requires non-empty quality_reason."
        return None

    # confidence=0 允许作为“不适配当前任务”的显式结论
    if (plan.confidence or 0) <= 0:
        return None

    if category == "PT":
        if plan.text_field is None or (isinstance(plan.text_field, list) and not plan.text_field):
            return "PT mapping requires non-empty text_field when confidence > 0."
        return None

    # SFT strong validation
    if not plan.messages or not isinstance(plan.messages, list):
        return "SFT mapping requires non-empty messages when confidence > 0."

    roles = set()
    for spec in plan.messages:
        if not isinstance(spec, dict):
            return "Each messages item must be an object."
        role = spec.get("role")
        content = spec.get("content")
        if not role:
            return "Each messages item requires role."
        if content is None or (isinstance(content, list) and len(content) == 0):
            return f"messages role={role} requires non-empty content."
        roles.add(str(role))

    if "user" not in roles or "assistant" not in roles:
        return "SFT mapping requires both user and assistant roles in messages."
    return None


# ---------------------------------------------------------------------------
# Knowledge summarisation (separate LLM call, no memory reuse)
# ---------------------------------------------------------------------------

async def _summarise_for_memory(
    llm: ChatOpenAI,
    raw_content: str,
    dataset_name: str,
    category: str,
) -> str:
    """Condense raw tool output into a knowledge snippet for long-term memory."""
    template = _load_prompt_template("knowledge_summarizer")
    prompt = (
        template
        .replace("{dataset_name}", dataset_name)
        .replace("{category}", category)
        .replace("{raw_content}", raw_content[:4000])
    )
    try:
        resp = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=60,
        )
        return resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        logger.warning(f"[DatasetAgent] Knowledge summarisation failed: {e}")
        return raw_content[:1000]


# ---------------------------------------------------------------------------
# DatasetAgent class
# ---------------------------------------------------------------------------

class DatasetAgent:
    """ReAct agent that analyses a single dataset directory."""

    def __init__(
        self,
        source_info: DatasetSourceInfo,
        category: str,
        user_query: str,
        datasets_background: str,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
        tavily_api_key: Optional[str] = None,
        memory_manager: Optional[PostprocessMemoryManager] = None,
    ):
        self.source_info = source_info
        self.category = category.upper()
        self.user_query = user_query
        self.datasets_background = datasets_background
        self.memory = memory_manager

        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_completion_tokens=4096,
        )

        self._tools = [
            create_file_read_tool(source_info.dataset_dir),
            create_web_search_tool(tavily_api_key),
            create_data_load_tool(source_info.dataset_dir),
            create_list_skills_tool(),
            create_apply_skill_tool(),
        ]
        self._tools_by_name = {t.name: t for t in self._tools}
        self._bound_llm = self.llm.bind_tools(self._tools)

    # ------------------------------------------------------------------
    # Graph nodes
    # ------------------------------------------------------------------

    def _agent_node(self, state: dict) -> dict:
        system_prompt = _build_system_prompt(self.category)
        messages = list(state.get("messages", []))
        model_input = [SystemMessage(content=system_prompt)] + messages
        if LOG_MODEL_INPUT_ENABLED:
            try:
                payload = [_message_to_debug_dict(m) for m in model_input]
                logger.info(
                    f"[DatasetAgent] {self.source_info.dataset_name}: model_input_payload="
                    f"{_truncate_for_log(json.dumps(payload, ensure_ascii=False, default=str), LOG_MODEL_INPUT_MAX_CHARS)}"
                )
            except Exception as e:
                logger.warning(f"[DatasetAgent] Failed to log model input payload: {e}")
        response = self._bound_llm.invoke(model_input)
        return {"messages": [response]}

    def _tool_node(self, state: dict) -> dict:
        messages = state.get("messages", [])
        last = messages[-1]
        outputs = []
        if getattr(last, "tool_calls", None):
            logger.info(
                f"[DatasetAgent] {self.source_info.dataset_name}: tool_calls="
                f"{[tc.get('name') for tc in last.tool_calls]}"
            )
        for tc in last.tool_calls:
            tool_name = tc["name"]
            src = self.source_info.source_type
            ds = self.source_info.dataset_name
            logger.info(
                f"[DatasetAgent] {ds}: calling tool={tool_name}, args={_truncate_for_log(tc.get('args', {}), 300)}"
            )

            if self.memory:
                self.memory.on_tool_before(src, ds, tool_name, tc.get("args", {}))

            if tool_name not in self._tools_by_name:
                result_str = json.dumps({"error": f"Tool '{tool_name}' not found"})
            else:
                try:
                    result_str = self._tools_by_name[tool_name].invoke(tc["args"])
                    if not isinstance(result_str, str):
                        result_str = json.dumps(result_str, ensure_ascii=False, default=str)
                except Exception as e:
                    result_str = json.dumps({"error": str(e)})

            if self.memory:
                self.memory.on_tool_after(src, ds, tool_name, result_str[:300])
            logger.info(
                f"[DatasetAgent] {ds}: tool={tool_name} done, "
                f"output={_truncate_for_log(result_str, 300)}"
            )

            outputs.append(
                ToolMessage(content=result_str, name=tool_name, tool_call_id=tc["id"])
            )
        return {"messages": outputs}

    @staticmethod
    def _should_continue(state: dict) -> str:
        messages = state.get("messages", [])
        if not messages:
            return "end"
        last = messages[-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "continue"
        return "end"

    # ------------------------------------------------------------------
    # Build & run
    # ------------------------------------------------------------------

    def _build_graph(self):
        # Use the annotated state schema so `messages` are appended via `add_messages`
        # instead of being overwritten on each node transition.
        builder = StateGraph(_DatasetAgentState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", self._tool_node)
        builder.set_entry_point("agent")
        builder.add_conditional_edges(
            "agent",
            self._should_continue,
            {"continue": "tools", "end": END},
        )
        builder.add_edge("tools", "agent")
        return builder.compile()

    async def run(self) -> DatasetAgentResult:
        """Execute the ReAct loop and return a structured result."""
        src = self.source_info.source_type
        ds = self.source_info.dataset_name
        logger.info(f"[DatasetAgent] Start dataset analysis: source={src}, dataset={ds}, category={self.category}")

        if self.memory:
            self.memory.on_dataset_agent_start(src, ds)

        task_message = _build_task_message(
            self.source_info, self.category, self.user_query, self.datasets_background,
        )
        initial_state = {"messages": [HumanMessage(content=task_message)]}

        graph = self._build_graph()

        try:
            final_state = await asyncio.to_thread(
                graph.invoke, initial_state, {"recursion_limit": MAX_AGENT_ITERATIONS * 2}
            )
        except Exception as e:
            error_msg = f"DatasetAgent ReAct loop failed for {ds}: {e}"
            logger.error(error_msg, exc_info=True)
            if self.memory:
                self.memory.on_dataset_agent_error(src, ds, str(e))
            return DatasetAgentResult(
                dataset_name=ds,
                source_type=src,
                dataset_dir=self.source_info.dataset_dir,
                success=False,
                error=error_msg,
            )
        logger.info(f"[DatasetAgent] ReAct loop finished for {ds}")

        all_messages = final_state.get("messages", [])
        called_tools = _collect_called_tools(all_messages)
        logger.info(f"[DatasetAgent] {ds}: called_tools={called_tools}")
        mapping_plan, parse_debug = _extract_mapping_plan_with_debug(all_messages, ds, self.category)
        logger.info(
            f"[DatasetAgent] {ds}: final_ai_raw_tail={_truncate_for_log(parse_debug.get('raw_tail', ''), 2000)}"
        )
        logger.info(
            f"[DatasetAgent] {ds}: parse_candidates={_truncate_for_log(json.dumps(parse_debug.get('candidates', []), ensure_ascii=False), 2000)}"
        )
        logger.info(
            f"[DatasetAgent] {ds}: parse_selected_idx={parse_debug.get('selected_candidate_idx')}, "
            f"parse_error_count={parse_debug.get('parse_error_count')}"
        )

        if mapping_plan is None:
            error_msg = f"DatasetAgent could not produce a mapping plan for {ds}"
            logger.warning(error_msg)
            if self.memory:
                self.memory.on_dataset_agent_error(src, ds, error_msg)
            return DatasetAgentResult(
                dataset_name=ds,
                source_type=src,
                dataset_dir=self.source_info.dataset_dir,
                success=False,
                error=error_msg,
            )
        mapping_summary = {
            "dataset_name": mapping_plan.dataset_name,
            "category": mapping_plan.category,
            "confidence": mapping_plan.confidence,
            "quality_label": mapping_plan.quality_label,
            "quality_reason": mapping_plan.quality_reason,
            "record_path": mapping_plan.record_path,
            "text_field": mapping_plan.text_field,
            "system": mapping_plan.system,
            "messages": mapping_plan.messages,
            "meta_fields": mapping_plan.meta_fields,
            "field_joiners": mapping_plan.field_joiners,
            "field_transforms": mapping_plan.field_transforms,
        }
        logger.info(
            f"[DatasetAgent] {ds}: mapping plan detail={_truncate_for_log(json.dumps(mapping_summary, ensure_ascii=False), 1200)}"
        )

        strict_error = _validate_mapping_plan_strict(
            mapping_plan,
            self.category,
        )
        if strict_error:
            logger.warning(f"[DatasetAgent] Mapping plan quality warning for {ds}: {strict_error}")
        logger.info(f"[DatasetAgent] Mapping plan extracted for {ds}, confidence={mapping_plan.confidence}")

        # Write knowledge to long-term memory
        if self.memory:
            try:
                knowledge_text = await _summarise_for_memory(
                    self.llm,
                    mapping_plan.reasoning,
                    ds,
                    self.category,
                )
                knowledge = DatasetKnowledgeSummary(
                    dataset_name=ds,
                    description=knowledge_text,
                    available_fields=[
                        fm.source_field
                        for fm in (mapping_plan.messages or [])
                        if isinstance(fm, dict) and "content" in fm
                    ] if self.category == "SFT" else [],
                )
                self.memory.write_knowledge(src, ds, knowledge.model_dump())
                self.memory.write_mapping_plan(src, ds, mapping_plan.model_dump())
            except Exception as e:
                logger.warning(f"[DatasetAgent] Memory write failed: {e}")

        if self.memory:
            self.memory.on_dataset_agent_end(src, ds, {"success": True, "records_processed": 0})

        logger.info(f"[DatasetAgent] Completed analysis for {ds}, confidence={mapping_plan.confidence}")
        return DatasetAgentResult(
            dataset_name=ds,
            source_type=src,
            dataset_dir=self.source_info.dataset_dir,
            success=True,
            mapping_plan=mapping_plan,
        )
