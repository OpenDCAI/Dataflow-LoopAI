# %%
from langgraph.graph import StateGraph
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import MessagesState

class AState(MessagesState):
    current: str

def node_a(state: AState):
    # 执行节点 B 的逻辑
    return {"current": "A"}

def node_b(state: AState):
    # 执行节点 B 的逻辑
    return {"current": "B"}

def node_c(state: AState):
    # 执行节点 C 的逻辑
    return {"current": "C"}

def node_d(state: AState):
    # 执行节点 D 的逻辑
    return {"current": f"from {state['current']} to D"}

def conditional_edge(state: AState):
    # Fill in arbitrary logic here that uses the state
    # to determine the next node
    approved = interrupt("Do you approve proceeding to C?")
    if approved == 'toC':
        return "C"
    else:
        return "B"

builder = StateGraph(AState)
builder.add_node("A", node_a)
builder.add_node("B", node_b)
builder.add_node("C", node_c)
builder.add_node("D", node_d)

builder.set_entry_point("A")
builder.set_finish_point("D")
builder.add_edge("B", "D")
builder.add_edge("C", "D")
builder.add_conditional_edges("A", conditional_edge)

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)

# %%
# 执行图
config = {"configurable": {"thread_id": "some_id"}}
result = graph.invoke({"current": "?"}, config=config)
print(result)

# %%
result = graph.invoke(Command(resume="toC"), config=config)
print(result)

# %%
