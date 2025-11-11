# -*- coding: utf-8 -*-
# utils/vllm_chat.py —— 标准版 vLLM(OpenAI 兼容) 简单封装；支持批量对话
from __future__ import annotations
from typing import List, Optional
from openai import OpenAI

from loopai.logger import get_logger
from loopai.common.prompts.prompt_loader import PromptLoader

logger = get_logger()

class VLLMChat:
    """
    轻量封装：
    - 使用官方 openai-python SDK，base_url 指向 vLLM 的 OpenAI 兼容服务
    - 批量 messages 调用，默认注入 system prompt（从 PromptLoader 读取）
    """
    def __init__(self,
                 model: str,
                 base_url: str,
                 api_key: str,
                 temperature: float = 0.0,
                 top_p: float = 0.95,
                 system_prompt_type: str = "system",
                 system_prompt_name: str = "default_prompt"):
        self.client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.system_prompt_type = system_prompt_type
        self.system_prompt_name = system_prompt_name
        self.loader = PromptLoader()  # 会自动扫描 common/prompts/*_prompt.json

    def _system_prompt(self, prompt_type: Optional[str]=None, prompt_name: Optional[str]=None) -> str:
        ptype = prompt_type or self.system_prompt_type
        pname = prompt_name or self.system_prompt_name
        return self.loader(ptype, pname)

    def batch(self, user_texts: List[str],
              prompt_type: Optional[str]=None,
              prompt_name: Optional[str]=None) -> List[str]:
        sys_prompt = self._system_prompt(prompt_type, prompt_name)
        outs: List[str] = []
        for u in user_texts:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": u}
                ],
                temperature=self.temperature,
                top_p=self.top_p
            )
            content = ""
            try:
                content = (resp.choices[0].message.content or "").strip()
            except Exception as e:
                logger.error(f"vLLM 返回解析失败：{e}")
            outs.append(content)
        return outs