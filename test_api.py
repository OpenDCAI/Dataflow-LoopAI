#!/usr/bin/env python3
"""
测试脚本：验证模型 API 是否可调用
"""
import os
from pathlib import Path
from openai import OpenAI

# 读取配置
project_root = Path(__file__).parent
api_key_file = project_root / "api_key.txt"

# 读取 API key
if api_key_file.exists():
    with open(api_key_file, 'r') as f:
        api_key = f.read().strip()
    print(f"✓ 成功读取 API key: {api_key[:10]}...{api_key[-4:]}")
else:
    print(f"✗ 未找到 API key 文件: {api_key_file}")
    exit(1)

# 配置（使用 DeepSeek 官方 API）
API_BASE = "https://api.deepseek.com"
MODEL = "deepseek-chat"

print(f"\n测试配置:")
print(f"  API Base: {API_BASE}")
print(f"  Model: {MODEL}")
print(f"  API Key: {api_key[:10]}...{api_key[-4:]}")

# 创建客户端
client = OpenAI(
    api_key=api_key,
    base_url=API_BASE
)

print(f"\n正在调用模型...")
try:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": "你好，请用一句话介绍你自己"}
        ],
        max_tokens=100,
        temperature=0.7
    )
    
    print(f"\n✓ 调用成功！")
    print(f"\n模型响应:")
    print(f"  {response.choices[0].message.content}")
    print(f"\n使用的 Token:")
    print(f"  Prompt tokens: {response.usage.prompt_tokens}")
    print(f"  Completion tokens: {response.usage.completion_tokens}")
    print(f"  Total tokens: {response.usage.total_tokens}")
    print(f"\n✓ API 配置正确，模型可正常调用！")
    
except Exception as e:
    print(f"\n✗ 调用失败！")
    print(f"错误类型: {type(e).__name__}")
    exit(1)

