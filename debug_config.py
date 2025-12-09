#!/usr/bin/env python3
"""
调试脚本：显示 WebCrawler 实际使用的配置
"""
import sys
import os
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 模拟 run_webcrawler.py 的配置读取逻辑
deepseek_api_key_file = Path(__file__).parent / 'examples/scripts/deepseek_api_key.txt'
if deepseek_api_key_file.exists():
    with open(deepseek_api_key_file, 'r') as f:
        deepseek_api_key = f.read().strip()
else:
    deepseek_api_key = os.getenv('DEEPSEEK_API_KEY', '')

tavily_api_key_file = Path(__file__).parent / 'examples/scripts/tavily_api_key.txt'
if tavily_api_key_file.exists():
    with open(tavily_api_key_file, 'r') as f:
        tavily_api_key = f.read().strip()
else:
    tavily_api_key = os.getenv('TAVILY_API_KEY', '')

MODEL_CONFIG = {
    'webcrawler_deepseek_api_key': deepseek_api_key,
    'webcrawler_tavily_api_key': tavily_api_key,
    'webcrawler_deepseek_api_base': os.getenv('WEBCRAWLER_API_BASE', 'https://api.deepseek.com/v1'),
    'webcrawler_model': os.getenv('WEBCRAWLER_MODEL', 'deepseek-chat'),
}

print("=" * 80)
print("WebCrawler 配置追踪")
print("=" * 80)
print()
print("【步骤 1】run_webcrawler.py 读取配置:")
print(f"  API Key 来源: {'examples/scripts/deepseek_api_key.txt' if deepseek_api_key_file.exists() else 'DEEPSEEK_API_KEY 环境变量'}")
print(f"  API Key: {deepseek_api_key[:10]}...{deepseek_api_key[-4:] if len(deepseek_api_key) > 14 else '(空)'}")
print(f"  API Base: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"  Model: {MODEL_CONFIG['webcrawler_model']}")
print(f"  Tavily Key: {tavily_api_key[:10]}...{tavily_api_key[-4:] if len(tavily_api_key) > 14 else '(空)'}")
print()

print("【步骤 2】crawl_node.py 创建 CrawlOrchestrator:")
print(f"  deepseek_api_key = state.get('webcrawler_deepseek_api_key', '')")
print(f"    实际值: {MODEL_CONFIG['webcrawler_deepseek_api_key'][:10]}...{MODEL_CONFIG['webcrawler_deepseek_api_key'][-4:]}")
print(f"  deepseek_api_base = state.get('webcrawler_deepseek_api_base', 'https://api.deepseek.com/v1')")
print(f"    实际值: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"  model = state.get('webcrawler_model', 'deepseek-chat')")
print(f"    实际值: {MODEL_CONFIG['webcrawler_model']}")
print()

print("【步骤 3】CrawlOrchestrator 初始化 QueryGenerator:")
print(f"  QueryGenerator(")
print(f"    model_name = '{MODEL_CONFIG['webcrawler_model']}'")
print(f"    base_url = '{MODEL_CONFIG['webcrawler_deepseek_api_base']}'")
print(f"    api_key = '{MODEL_CONFIG['webcrawler_deepseek_api_key'][:10]}...{MODEL_CONFIG['webcrawler_deepseek_api_key'][-4:]}'")
print(f"  )")
print()

print("=" * 80)
print("结论:")
print("=" * 80)
print(f"WebSearch 里的 QueryGenerator 会调用:")
print(f"  URL: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"  Model: {MODEL_CONFIG['webcrawler_model']}")
print(f"  Key: {MODEL_CONFIG['webcrawler_deepseek_api_key'][:10]}...{MODEL_CONFIG['webcrawler_deepseek_api_key'][-4:]}")
print()

# 测试这个配置是否有效
print("【验证】测试这个配置是否能调用成功:")
print("-" * 80)

from openai import OpenAI

client = OpenAI(
    api_key=MODEL_CONFIG['webcrawler_deepseek_api_key'],
    base_url=MODEL_CONFIG['webcrawler_deepseek_api_base']
)

try:
    response = client.chat.completions.create(
        model=MODEL_CONFIG['webcrawler_model'],
        messages=[{"role": "user", "content": "你好"}],
        max_tokens=10
    )
    print(f"✓ 调用成功！响应: {response.choices[0].message.content}")
except Exception as e:
    print(f"✗ 调用失败！")
    print(f"  错误: {e}")
    print()
    print("这就是为什么 WebCrawler 无法正常工作的原因！")

