import json
from typing import (
    Annotated,
    Sequence,
    TypedDict,
    Optional
)
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langchain_core.messages import ToolMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langchain_core.messages import message_to_dict

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent


class AgentState(TypedDict):
    """代理的状态。"""

    # add_messages 是一个 reducer。
    # 请查看 https://langchain-ai.github.io/langgraph/concepts/low_level/#reducers
    messages: Annotated[Sequence[BaseMessage], add_messages]


def ReAct_Node(model: ChatOpenAI, tools: list[tool], prompt: str, messages_key: str = "messages", name: Optional[str] = None, debug: bool = False, checkpointer=None,
               store=None):
    """
    ReAct Node
    """
    tools_by_name = {tool.name: tool for tool in tools}
    system_prompt = SystemMessage(content=prompt)
    messages_key = messages_key
    model = model.bind_tools(tools)

    # 定义我们的工具节点。
    def tool_node(state):
        outputs = []
        for tool_call in state[messages_key][-1].tool_calls:
            tool_name = tool_call["name"]
            if tool_name not in tools_by_name:
                error_result = {
                    "status": 'tool_not_found',
                    "message": f"Try to use tool failed. Error: Tool {tool_name} not found"
                }
                outputs.append(
                    ToolMessage(
                        content=json.dumps(error_result),
                        name=tool_name,
                        tool_call_id=tool_call["id"],
                    )
                )
                continue
            tool_result = tools_by_name[tool_name].invoke(
                tool_call["args"])
            outputs.append(
                ToolMessage(
                    content=json.dumps(tool_result),
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                )
            )
        return {messages_key: outputs}

    # 定义调用模型的节点

    def call_model(
        state,
        config: RunnableConfig,
    ):
        writer = get_stream_writer()
        writer(StreamEvent(current='llm_node', data={
            'stream_message_state': 'start',
            'history': [message_to_dict(msg) for msg in state[messages_key]]
        }).json())
        max_context_len = state.get("max_context_len", 0)
        response = model.invoke([system_prompt] + state[messages_key][-max_context_len:], config)
        writer(StreamEvent(current='llm_node', data={
            'stream_message_state': 'finished',
            'history': [message_to_dict(msg) for msg in state[messages_key]] + [message_to_dict(response)],
            'current_message': message_to_dict(response)
        }).json())
        # 我们返回一个列表，因为这将被添加到现有列表中。
        return {messages_key: [response]}

    # 定义决定是否继续的条件边缘。

    def should_continue(state):
        messages = state[messages_key]
        last_message = messages[-1]
        # 如果没有函数调用，那么我们就结束。
        if not last_message.tool_calls:
            return "end"
        # 否则，如果有的话，我们继续。
        else:
            return "continue"

    workflow = StateGraph(AgentState)

    workflow.add_node("agent", call_model)
    workflow.add_node("tools", tool_node)

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        # 首先，我们定义起始节点。我们使用`agent`。
        # 这意味着这些是调用 `agent` 节点后获取的边缘。
        "agent",
        # 接下来，我们传入决定下一个调用哪个节点的函数。
        should_continue,
        # 最后我们传入一个映射。
        # 键是字符串，而值是其他节点。
        # END是一个特殊节点，标记图形应结束。
        # 我们将调用 `should_continue`，然后其输出。
        # 将与此映射中的键进行匹配。
        # 根据匹配的结果，那个节点将被调用。
        {
            # 如果是“工具”，那么我们称之为工具节点。
            "continue": "tools",
            # 否则我们就结束了。
            "end": END,
        },
    )

    # 我们现在从`tools`到`agent`添加一条普通边。
    # 这意味着在调用 `tools` 之后，接下来会调用 `agent` 节点。
    workflow.add_edge("tools", "agent")

    # 现在我们可以编译并可视化我们的图表。
    return workflow.compile(name=name, debug=debug, checkpointer=checkpointer, store=store)
