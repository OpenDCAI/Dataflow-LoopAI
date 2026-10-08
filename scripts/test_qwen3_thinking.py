#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 vLLM 部署的 Qwen3 是否支持用 chat_template_kwargs 控制思考模式。

用法（只需要 Python 标准库，无需额外依赖）：

    python test_qwen3_thinking.py --base-url http://localhost:8911/v1 --model <模型名>

说明：Qwen3 的思考标记是 <think> / </think>（尖括号，见 tokenizer_config.json 的
added_tokens_decoder）。chat_template 末尾逻辑：

    {% if enable_thinking is defined and enable_thinking is false %}
        {{ '<think>\\n\\n</think>\\n\\n' }}   # 注入空 think 块 -> 强制不思考
    {% endif %}

即「默认思考，enable_thinking=false 才强制关闭」。所以验证重点是：
    - enable_thinking=false -> 直接回答，无 <think> 块
    - 不传 / enable_thinking=true -> 模型默认思考（可能带 <think> 推理）
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

DEFAULT_PROMPT = "9.9 和 9.11 哪个更大？"

# 思考块标记（本模型用 <think>/</think>，也覆盖其他常见格式）
THINKING_MARKERS = (
    "<think>",
    "</think>",
    "/think",
    "<thinking",
    "</thinking>",
    "<reasoning",
    "</reasoning>",
    "<thought",
    "</thought>",
)


def chat_once(base_url, model, api_key, prompt, enable_thinking=None, max_tokens=2048, timeout=180.0):
    """发一次 chat/completions 请求，返回 (message_dict, error)。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    if enable_thinking is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": bool(enable_thinking)}

    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, "HTTP {}: {}".format(e.code, e.read().decode("utf-8", errors="replace")[:800])
    except Exception as e:  # noqa: BLE001
        return None, "请求异常: {}".format(e)

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, "响应结构异常: " + json.dumps(data, ensure_ascii=False)[:500]

    return msg, None


def _render(text, limit=2000):
    if not text:
        return "(空)"
    return text.replace("\n", "\\n\n  ")[:limit]


def detect_thinking(*texts):
    joined = "\n".join(t for t in texts if t)
    return any(marker in joined for marker in THINKING_MARKERS)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--base-url", default="http://localhost:8911/v1", help="vLLM OpenAI 兼容接口地址")
    ap.add_argument("--model", required=True, help="vLLM 部署的模型名（--served-model-name 或模型路径）")
    ap.add_argument("--api-key", default="EMPTY", help="API key，本地 vLLM 默认 EMPTY")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="测试用问题")
    ap.add_argument("--max-tokens", type=int, default=2048, help="最大输出 token 数，思考模式建议 ≥2048")
    args = ap.parse_args()

    cases = [
        ("基线（不传 chat_template_kwargs）", None),
        ("关思考 enable_thinking=false", False),
        ("默认 enable_thinking=true ", True),
    ]

    print("=" * 74)
    print("base_url :", args.base_url)
    print("model    :", args.model)
    print("prompt   :", args.prompt)
    for label, val in cases:
        msg, err = chat_once(
            args.base_url, args.model, args.api_key, args.prompt,
            enable_thinking=val, max_tokens=args.max_tokens,
        )
        kw = None if val is None else {"enable_thinking": val}
        print("=" * 74)
        print("[{}]  chat_template_kwargs={}".format(label, kw))
        if err:
            print("  ❌ {}".format(err))
            continue

        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        has_reasoning_field = "reasoning_content" in msg and bool(reasoning)

        print("  reasoning_content ({}字符):".format(len(reasoning)))
        print("  " + _render(reasoning))
        print("  content ({}字符):".format(len(content)))
        print("  " + _render(content))

        if has_reasoning_field:
            print("  🧠 vLLM 已把推理拆到 reasoning_content 字段")
        elif detect_thinking(content, reasoning):
            print("  🧠 content 里检测到 <think>/</think> 思考块")
        else:
            print("  ✅ 未检测到思考块")

    print("=" * 74)
    print("判定（本模型默认思考，false 才强制关闭）：")
    print("  - 关思考 false：content 直接是答案，无 <think> 块")
    print("  - 不传 / true：模型默认思考，content 可能带 <think>...</think>")
    print("  false 与 true 输出不同 => chat_template_kwargs 生效。")


if __name__ == "__main__":
    main()
