from typing import TypedDict, Any
from langgraph.graph import MessagesState

class LoopAIState(MessagesState):
    model_path: str # to defined the path of model to be evaluated and post-trained
    eval_data_path: str # to defined the path of data to be evaluated
    eval_results_data: Any # to defined the path of results of evaluation
    mined_data: str
    update_model_path: str
    current: str # to defined the current task, e.g. train, evaluate, obtain, naive
    next_to: str
