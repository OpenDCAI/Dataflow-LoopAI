#!/usr/bin/env python3
"""
追踪 WebCrawler 实际使用的配置
"""
import sys
import os
import asyncio
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 模拟 run_webcrawler.py 的配置
deepseek_api_key_file = project_root / 'examples/scripts/deepseek_api_key.txt'
with open(deepseek_api_key_file, 'r') as f:
    deepseek_api_key = f.read().strip()

MODEL_CONFIG = {
    'webcrawler_deepseek_api_key': deepseek_api_key,
    'webcrawler_deepseek_api_base': os.getenv('WEBCRAWLER_API_BASE', 'https://api.deepseek.com/v1'),
    'webcrawler_model': os.getenv('WEBCRAWLER_MODEL', 'deepseek-chat'),
}

print("=" * 80)
print("追踪 CrawlOrchestrator 和 QueryGenerator 的配置")
print("=" * 80)
print()

# 模拟 crawl_node.py 创建 CrawlOrchestrator
from loopai.agents.WebCrawler.utils import CrawlOrchestrator

print("【步骤 1】创建 CrawlOrchestrator")
print(f"  deepseek_api_key: {MODEL_CONFIG['webcrawler_deepseek_api_key'][:10]}...{MODEL_CONFIG['webcrawler_deepseek_api_key'][-4:]}")
print(f"  deepseek_api_base: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"  model: {MODEL_CONFIG['webcrawler_model']}")
print()

orchestrator = CrawlOrchestrator(
    deepseek_api_key=MODEL_CONFIG['webcrawler_deepseek_api_key'],
    tavily_api_key="fake",
    deepseek_api_base=MODEL_CONFIG['webcrawler_deepseek_api_base'],
    model=MODEL_CONFIG['webcrawler_model'],
    max_pages=10,
    output_dir="./test_output"
)

print("【步骤 2】检查 CrawlOrchestrator 的实际配置")
print(f"  self.deepseek_api_key: {orchestrator.deepseek_api_key[:10]}...{orchestrator.deepseek_api_key[-4:]}")
print(f"  self.deepseek_api_base: {orchestrator.deepseek_api_base}")
print(f"  self.model: {orchestrator.model}")
print()

print("【步骤 3】检查 AsyncOpenAI client 的配置")
print(f"  client.api_key: {orchestrator.client.api_key[:10]}...{orchestrator.client.api_key[-4:]}")
print(f"  client.base_url: {orchestrator.client.base_url}")
print()

# 测试直接调用
print("【步骤 4】测试 AsyncOpenAI client 是否能正常调用")
print("-" * 80)

async def test_llm():
    try:
        response = await orchestrator.client.chat.completions.create(
            model=orchestrator.model,
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=10
        )
        print(f"✓ 调用成功！")
        print(f"  响应: {response.choices[0].message.content}")
        print(f"  使用的模型: {response.model}")
    except Exception as e:
        print(f"✗ 调用失败！")
        print(f"  错误: {e}")
        print()
        print("【问题诊断】")
        if "(governor)" in str(e):
            print("  ⚠️ 错误信息包含 '(governor)'，说明还在调用你的网关！")
            print(f"  实际调用的 URL: {orchestrator.client.base_url}")
        else:
            print("  这是 DeepSeek 官方 API 的错误")

asyncio.run(test_llm())

