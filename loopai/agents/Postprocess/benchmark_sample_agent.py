from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Dict, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from loopai.logger import get_logger
from .schemas import DatasetSourceInfo
from .tools.data_load import create_data_load_tool

logger = get_logger()

MAX_BENCHMARK_AGENT_ITERATIONS = 8


class _BenchmarkSampleState(dict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


def _build_system_prompt() -> str:
    return (
        "你是 benchmark 采样子 Agent，只负责从候选数据文件中抽取 1 条真实样本。"
        "你只能调用 data_load。"
        "严禁读取或假设说明文件/配置文件/目录信息。"
        "严禁输出 schema 或 markdown。"
        "最终必须输出一个 JSON 对象，字段为：file_path(string), split_name(string), "
        "sample_record(object), reason(string)。"
    )


def _build_task_message(source_info: DatasetSourceInfo, candidate_files: List[str]) -> str:
    candidates = "\n".join(f"- {x}" for x in candidate_files) if candidate_files else "(空)"
    return (
        "任务：从下列候选数据文件中选择一个文件，调用 data_load 获取小样本，"
        "并输出其中一条最能代表真实 benchmark 记录的 sample_record。\n\n"
        f"dataset_name: {source_info.dataset_name}\n"
        f"dataset_dir: {source_info.dataset_dir}\n"
        f"source_type: {source_info.source_type}\n\n"
        f"候选数据文件（只能从这里选）:\n{candidates}\n\n"
        "约束：\n"
        "1) 只允许使用 data_load。\n"
        "2) file_path 必须是候选列表中的相对路径。\n"
        "3) sample_record 必须来自工具返回的 sample_records，不能为空对象。\n"
        "4) split_name 固定填 train。\n"
        "5) 若无法得到有效样本，返回 JSON："
        '{"file_path":"","split_name":"train","sample_record":{},"reason":"no_valid_sample"}'
    )


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    txt = (text or "").strip()
    if not txt:
        return None
    if "```json" in txt:
        txt = txt.split("```json", 1)[1]
        if "```" in txt:
            txt = txt.rsplit("```", 1)[0]
    elif "```" in txt:
        parts = txt.split("```")
        if len(parts) >= 3:
            txt = parts[1]
    txt = txt.strip()
    if not txt.startswith("{"):
        i = txt.find("{")
        if i < 0:
            return None
        txt = txt[i:]
    candidates = [txt]
    depth = 0
    start = txt.find("{")
    if start >= 0:
        for i in range(start, len(txt)):
            if txt[i] == "{":
                depth += 1
            elif txt[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(txt[start : i + 1])
                    break
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


class BenchmarkSampleAgent:
    def __init__(
        self,
        source_info: DatasetSourceInfo,
        candidate_files: List[str],
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
        hf_datasets_cache_dir: Optional[str] = None,
    ):
        self.source_info = source_info
        self.candidate_files = candidate_files
        self._candidate_set = set(candidate_files)
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_completion_tokens=1024,
        )
        self._tools = [create_data_load_tool(source_info.dataset_dir, hf_datasets_cache_dir)]
        self._tools_by_name = {t.name: t for t in self._tools}
        self._bound_llm = self.llm.bind_tools(self._tools)

    def _agent_node(self, state: dict) -> dict:
        messages = list(state.get("messages", []))
        response = self._bound_llm.invoke([SystemMessage(content=_build_system_prompt())] + messages)
        return {"messages": [response]}

    def _tool_node(self, state: dict) -> dict:
        messages = state.get("messages", [])
        last = messages[-1]
        outputs = []
        for tc in getattr(last, "tool_calls", []) or []:
            tool_name = tc.get("name")
            if tool_name not in self._tools_by_name:
                result = json.dumps({"error": f"Tool '{tool_name}' not found"}, ensure_ascii=False)
                outputs.append(ToolMessage(content=result, name=tool_name or "unknown", tool_call_id=tc.get("id", "")))
                continue

            args = tc.get("args", {}) or {}
            req_path = str(args.get("file_path", "")).strip()
            if req_path and req_path not in self._candidate_set:
                result = json.dumps(
                    {"error": "file_path_not_allowed", "allowed_files": self.candidate_files[:40]},
                    ensure_ascii=False,
                )
            else:
                if not req_path and self.candidate_files:
                    args["file_path"] = self.candidate_files[0]
                if "max_rows" not in args:
                    args["max_rows"] = 8
                try:
                    out = self._tools_by_name[tool_name].invoke(args)
                    result = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
                except Exception as e:
                    result = json.dumps({"error": str(e)}, ensure_ascii=False)
            outputs.append(ToolMessage(content=result, name=tool_name, tool_call_id=tc.get("id", "")))
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
        builder = StateGraph(_BenchmarkSampleState)
        builder.add_node("agent", self._agent_node)
        builder.add_node("tools", self._tool_node)
        builder.set_entry_point("agent")
        builder.add_conditional_edges("agent", self._should_continue, {"continue": "tools", "end": END})
        builder.add_edge("tools", "agent")
        return builder.compile()

    @staticmethod
    def _extract_first_sample_record(messages: List[BaseMessage]) -> Optional[Dict[str, Any]]:
        for msg in reversed(messages):
            if not isinstance(msg, ToolMessage):
                continue
            try:
                payload = json.loads(msg.content if isinstance(msg.content, str) else str(msg.content))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            samples = payload.get("sample_records")
            if isinstance(samples, list):
                for it in samples:
                    if isinstance(it, dict) and it:
                        return it
        return None

    async def run(self) -> Dict[str, Any]:
        if not self.candidate_files:
            return {
                "file_path": "",
                "split_name": "train",
                "sample_record": {},
                "reason": "no_candidate_files",
            }
        graph = self._build_graph()
        task_message = _build_task_message(self.source_info, self.candidate_files)
        initial_state = {"messages": [HumanMessage(content=task_message)]}

        try:
            final_state = await asyncio.to_thread(
                graph.invoke, initial_state, {"recursion_limit": MAX_BENCHMARK_AGENT_ITERATIONS * 2}
            )
        except Exception as e:
            logger.warning(f"[BenchmarkSampleAgent] run failed for {self.source_info.dataset_name}: {e}")
            return {
                "file_path": "",
                "split_name": "train",
                "sample_record": {},
                "reason": f"agent_error: {e}",
            }

        messages = final_state.get("messages", [])
        final_ai: Optional[AIMessage] = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                final_ai = msg
                break

        parsed = _extract_json_object(final_ai.content if final_ai else "")
        sample_record = self._extract_first_sample_record(messages)

        file_path = ""
        split_name = "train"
        reason = "ok"
        if isinstance(parsed, dict):
            p = str(parsed.get("file_path", "")).strip()
            if p in self._candidate_set:
                file_path = p
            split_name = str(parsed.get("split_name", "train") or "train")
            reason = str(parsed.get("reason", "ok") or "ok")
            if isinstance(parsed.get("sample_record"), dict) and parsed.get("sample_record"):
                sample_record = parsed["sample_record"]

        if not sample_record:
            return {
                "file_path": file_path,
                "split_name": split_name,
                "sample_record": {},
                "reason": "no_valid_sample",
            }
        if not file_path:
            file_path = self.candidate_files[0]
        return {
            "file_path": file_path,
            "split_name": split_name,
            "sample_record": sample_record,
            "reason": reason,
        }
