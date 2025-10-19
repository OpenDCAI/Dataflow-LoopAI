from typing import TypedDict
from langgraph.graph import MessagesState

class UserState(MessagesState):
    model_path: str # to defined the path of model to be evaluated and post-trained
    eval_results: str
    mined_data: str
    update_model_path: str
    
