# loopai/agents/Analyzer/utils/openai_compat_llm.py

import requests
from typing import Any, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    AIMessage,
)
from langchain_core.outputs import ChatResult, ChatGeneration


class OpenAICompatChat(BaseChatModel):
    """
    一个最小可用的 OpenAI-compatible Chat 模型封装，直接走 HTTP 请求，
    避免官方 openai-python 客户端自动把 max_tokens 改名为 max_completion_tokens。

    只依赖：
    - POST {base_url}/chat/completions
    - body: {model, messages, max_tokens, temperature, top_p, [stop]}
    - 返回结构兼容 OpenAI:
        {"choices": [{"message": {"content": "..."}}, ...], ...}
    """

    model: str
    base_url: str
    api_key: str
    max_tokens: int = 512
    temperature: float = 0.0
    top_p: float = 0.95

    @property
    def _llm_type(self) -> str:
        # 给 LangChain 一个类型标识
        return "openai-compat"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        LangChain -> OpenAI 格式 -> HTTP 请求 -> ChatResult
        """
        # 1. LangChain Message -> OpenAI chat messages
        openai_msgs = []
        for m in messages:
            if isinstance(m, HumanMessage):
                role = "user"
            elif isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, AIMessage):
                role = "assistant"
            else:
                role = "user"
            openai_msgs.append({"role": role, "content": m.content})

        # 2. 组装 payload
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": openai_msgs,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }

        if stop:
            payload["stop"] = stop

        extra_kwargs = dict(kwargs) if kwargs else {}
        extra_kwargs.pop("max_completion_tokens", None)
        extra_kwargs.pop("max_output_tokens", None)
        payload.update(extra_kwargs)

        # 3. headers
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 4. 调用后端 /chat/completions
        resp = requests.post(
            self.base_url.rstrip("/") + "/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        # 5. 解析返回内容
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as e:  
            raise RuntimeError(f"Unexpected response format: {data}") from e

        ai_msg = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])