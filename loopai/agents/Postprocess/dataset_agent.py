"""
DatasetAgent — per-dataset ReAct agent (postprocess v2, relevance-only).

Uses two tools: ``file_read`` (docs/readme) and ``data_load`` (row samples).
Produces a :class:`DatasetRelevanceVerdict` comparing samples to ``user_query``
and optional benchmark references. No field mapping or format conversion.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any, Dict, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from loopai.logger import get_logger
from .memory import PostprocessMemoryManager
from .schemas import DatasetAgentResult, DatasetRelevanceVerdict, DatasetSourceInfo
from .tools import create_data_load_tool, create_file_read_tool

logger = get_logger()

MAX_AGENT_ITERATIONS = 12
LOG_MODEL_INPUT_ENABLED = os.getenv("POSTPROCESS_LOG_MODEL_INPUT", "1").lower() in ("1", "true", "yes")
LOG_MODEL_INPUT_MAX_CHARS = int(os.getenv("POSTPROCESS_LOG_MODEL_INPUT_MAX_CHARS", "50000"))


class _DatasetAgentState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


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
    template = _load_prompt_template("relevance_agent_system")
    if not template:
        template = _load_prompt_template("dataset_agent_system")
    category_desc = "预训练——连续文本" if category == "PT" else "监督微调——对话消息"
    return template.replace("{category_description}", category_desc)


def _build_task_message(
    source_info: DatasetSourceInfo,
    category: str,
    user_query: str,
    datasets_background: str,
    benchmark_reference: str = "",
) -> str:
    template = _load_prompt_template("relevance_agent_task")
    if not template:
        template = _load_prompt_template("dataset_agent_task")
    category_desc = "预训练——连续文本" if category == "PT" else "监督微调——对话消息"

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

    task = (
        template.replace("{dataset_name}", source_info.dataset_name)
        .replace("{dataset_dir}", source_info.dataset_dir)
        .replace("{source_type}", source_info.source_type)
        .replace("{category}", category)
        .replace("{category_description}", category_desc)
        .replace("{user_query}", user_query or "(无)")
        .replace("{datasets_background}", datasets_background or "(无)")
        .replace("{folder_overview}", folder_overview)
    )
    if benchmark_reference:
        task += (
            "\n\n## Benchmark 参考样本\n\n"
            f"{benchmark_reference}\n"
            "请将这些样本视为任务/评测边界的参考，用于判断当前数据集是否与之同域或相关。"
        )
    return task


def _format_benchmark_reference(
    benchmark_reference_samples: Optional[List[Dict[str, Any]]],
    max_items: int = 6,
    max_chars: int = 2500,
) -> str:
    if not benchmark_reference_samples:
        return ""
    chunks: List[str] = []
    for item in benchmark_reference_samples[:max_items]:
        chunks.append(
            json.dumps(
                {
                    "benchmark_name": item.get("benchmark_name", ""),
                    "file_path": item.get("file_path", ""),
                    "split_name": item.get("split_name", ""),
                    "sample_record": item.get("sample_record", {}),
                },
                ensure_ascii=False,
            )
        )
    merged = "\n".join(chunks)
    if len(merged) > max_chars:
        return merged[:max_chars] + "...(truncated)"
    return merged


def _extract_relevance_verdict(messages: List[BaseMessage]) -> Optional[DatasetRelevanceVerdict]:
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
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

        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if not isinstance(data, dict) or "related" not in data:
                    continue
                return DatasetRelevanceVerdict.model_validate(
                    {
                        "related": bool(data["related"]),
                        "is_benchmark_data": bool(data.get("is_benchmark_data", False)),
                        "reason": str(data.get("reason", "")),
                    }
                )
            except Exception:
                continue
    return None


def _truncate_for_log(text: Any, limit: int = 400) -> str:
    raw = text if isinstance(text, str) else str(text)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"...(truncated, {len(raw)} chars)"


def _message_to_debug_dict(msg: BaseMessage) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"type": msg.__class__.__name__}
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    payload["content"] = content
    if isinstance(msg, ToolMessage):
        payload["name"] = msg.name
        payload["tool_call_id"] = msg.tool_call_id
    if isinstance(msg, AIMessage):
        payload["tool_calls"] = getattr(msg, "tool_calls", None)
    return payload


class DatasetAgent:
    """ReAct agent: read files + sample data, then emit a relevance verdict."""

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
        benchmark_reference_samples: Optional[List[Dict[str, Any]]] = None,
        hf_datasets_cache_dir: Optional[str] = None,
    ):
        self.source_info = source_info
        self.category = category.upper()
        self.user_query = user_query
        self.datasets_background = datasets_background
        self.memory = memory_manager
        self.benchmark_reference = _format_benchmark_reference(benchmark_reference_samples)
        _ = tavily_api_key  # unused; kept for call-site compatibility

        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_completion_tokens=2048,
        )

        self._tools = [
            create_file_read_tool(source_info.dataset_dir),
            create_data_load_tool(source_info.dataset_dir, hf_datasets_cache_dir),
        ]
        self._tools_by_name = {t.name: t for t in self._tools}
        self._bound_llm = self.llm.bind_tools(self._tools)

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
                f"[DatasetAgent] {ds}: tool={tool_name} done, output={_truncate_for_log(result_str, 300)}"
            )

            outputs.append(ToolMessage(content=result_str, name=tool_name, tool_call_id=tc["id"]))
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

    def _build_graph(self):
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
        src = self.source_info.source_type
        ds = self.source_info.dataset_name
        logger.info(f"[DatasetAgent] Start relevance analysis: source={src}, dataset={ds}, category={self.category}")

        if self.memory:
            self.memory.on_dataset_agent_start(src, ds)

        task_message = _build_task_message(
            self.source_info,
            self.category,
            self.user_query,
            self.datasets_background,
            self.benchmark_reference,
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

        all_messages = final_state.get("messages", [])
        verdict = _extract_relevance_verdict(all_messages)
        if verdict is None:
            error_msg = f"DatasetAgent could not parse relevance verdict for {ds}"
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

        logger.info(
            f"[DatasetAgent] {ds}: related={verdict.related}, "
            f"is_benchmark_data={verdict.is_benchmark_data}, "
            f"reason={_truncate_for_log(verdict.reason, 500)}"
        )

        if self.memory:
            try:
                self.memory.write_relevance_verdict(src, ds, verdict.model_dump())
            except Exception as e:
                logger.warning(f"[DatasetAgent] Memory write failed: {e}")
            self.memory.on_dataset_agent_end(
                src,
                ds,
                {"success": True, "records_processed": 0, "related": verdict.related},
            )

        return DatasetAgentResult(
            dataset_name=ds,
            source_type=src,
            dataset_dir=self.source_info.dataset_dir,
            success=True,
            relevance_verdict=verdict,
        )
