import json
from typing import Optional, List
from pydantic import BaseModel


class response_body:
    def __init__(self, code=200, status='success', message='', data=None):
        self.code = code
        self.status = status
        self.message = message
        self.data = data

    def __call__(self):
        res = {}
        for key, value in self.__dict__.items():
            if value:
                res[key] = value
        return res
    
    def text(self):
        res = self.__call__()
        return json.dumps(res, ensure_ascii=False)
    
    def stream(self):
        text = self.text()
        return f'data: {text}\n\n'


class ConfigModel(BaseModel):
    id: int
    name: Optional[str] = None
    config: str


class TaskItem(BaseModel):
    id: Optional[int] = None
    task_id: Optional[str] = None
    name: Optional[str] = None
    config: Optional[str] = None
    state: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None

