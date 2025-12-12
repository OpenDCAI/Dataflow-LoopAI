"""
Test script for WebCrawlerAgent
"""
import sys
import json
import os
from pathlib import Path

# 添加项目根目录到 Python 路径，这样就能找到 loopai 文件夹
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loopai.agents.WebCrawler import WebCrawlerAgent
from loopai.memory import checkpointer, store
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage

# Configuration
# Read DeepSeek API key from file if exists, otherwise use environment variable
deepseek_api_key = None
deepseek_api_key_file = Path(__file__).parent / 'deepseek_api_key.txt'
if deepseek_api_key_file.exists():
    with open(deepseek_api_key_file, 'r') as f:
        deepseek_api_key = f.read().strip()
else:
    deepseek_api_key = os.getenv('DEEPSEEK_API_KEY', '')

# Read Tavily API key if exists
tavily_api_key = None
tavily_api_key_file = Path(__file__).parent / 'tavily_api_key.txt'
if tavily_api_key_file.exists():
    with open(tavily_api_key_file, 'r') as f:
        tavily_api_key = f.read().strip()
else:
    tavily_api_key = os.getenv('TAVILY_API_KEY', '')

# Model configuration
MODEL_CONFIG = {
    'webcrawler_deepseek_api_key': deepseek_api_key,
    'webcrawler_tavily_api_key': tavily_api_key,
    'webcrawler_deepseek_api_base': os.getenv('WEBCRAWLER_API_BASE', 'https://api.deepseek.com'),
    'webcrawler_model': os.getenv('WEBCRAWLER_MODEL', 'deepseek-chat'),
    
    # === 生成查询配置 ===
    'num_queries': int(os.getenv('WEBCRAWLER_NUM_QUERIES', '5')),  # 生成的搜索查询数量
    
    # === 爬取策略 ===
    'webcrawler_max_pages': int(os.getenv('WEBCRAWLER_MAX_PAGES', '100')),  # 最大爬取页面数
    'crawl_depth': int(os.getenv('WEBCRAWLER_DEPTH', '3')),  # 爬取深度
    'max_links_per_page': int(os.getenv('WEBCRAWLER_MAX_LINKS', '5')),  # 每页最大链接数
    'concurrent_pages': int(os.getenv('WEBCRAWLER_CONCURRENT', '3')),  # 并发爬取页面数
    
    # === 内容过滤 ===
    'min_text_length': int(os.getenv('WEBCRAWLER_MIN_TEXT_LENGTH', '500')),  # 最小文本长度
    'min_code_length': int(os.getenv('WEBCRAWLER_MIN_CODE_LENGTH', '50')),  # 最小代码长度
    'min_relevance_score': int(os.getenv('WEBCRAWLER_MIN_RELEVANCE', '6')),  # 最小相关性分数
    'url_patterns': os.getenv('WEBCRAWLER_URL_PATTERNS', None),  # URL 模式匹配（可选）
    
    # === 运行时配置 ===
    'request_delay': float(os.getenv('WEBCRAWLER_REQUEST_DELAY', '2.0')),  # 请求延迟（秒）
    'timeout': int(os.getenv('WEBCRAWLER_TIMEOUT', '30')),  # 请求超时（秒）
    'max_retries': int(os.getenv('WEBCRAWLER_MAX_RETRIES', '3')),  # 最大重试次数
    
    # === 输出配置 ===
    'output_format': os.getenv('WEBCRAWLER_OUTPUT_FORMAT', 'jsonl'),  # 输出格式 (jsonl/json)
    'save_html': os.getenv('WEBCRAWLER_SAVE_HTML', 'False').lower() == 'true',  # 是否保存HTML
}

# Output directory
output_dir = os.getenv('OUTPUT_DIR', str(Path(__file__).parent.parent.parent / 'output' / 'webcrawler_outputs'))
os.makedirs(output_dir, exist_ok=True)

# Test query - 爬取任务描述
test_query = os.getenv('TEST_QUERY', """
**任务背景**代码自动评测任务测试了text2sql领域的BIg Bench for LaRge-scale Database Grounded Text-to-SQL数据集，主要针对代码的语法和结构进行评估。评测结果显示，整体通过率较低，反映出代码在SQL语法和模式定义方面存在较多问题。此外，所有样本的代码复杂度和长度均较高，这进一步增加了评测的难度。

**评测结果**
样本正确率：127/400（31.75%）
Pass@k(任务口径)： Pass@1=80.00%, Pass@10=80.00%
主要错因(stage)分布：
  - sql_syntax: 109
  - other: 61
  - sql_schema: 55
  - sql_perf: 41
  - sql_type: 7
标签 Top： SQL:90, other:61, VariableError:44, SyntaxError:36, ExecutionFailure:33, SQL错误:24, variable:24, error:23
SQL 语句长度分布（按字符数粗分）： >60:400
SQL 词元数量分布（粗粒度）： >8:400
I/O断言解析成功：0/400

**评测总结**
1. 增加样本覆盖：确保数据集中包含各种类型的SQL语句，特别是那些涉及复杂查询和性能优化的案例，以提高模型的泛化能力。
2. 设计边界/异常用例：增加边界条件和异常情况的样本，如极端数据量、复杂嵌套查询等，以测试模型在极端情况下的表现。
3. 优化题型分布：调整数据集中不同题型的比例，确保SQL语法和性能优化相关的题目比例适当增加，以反映实际应用场景的需求。
4. 改进断言设计：设计更严格的断言来验证SQL语句的正确性和性能，确保模型生成的SQL语句不仅语法正确，而且执行效率高。
5. 引入难度分层：根据SQL语句的复杂度和性能要求，对数据集进行难度分层，逐步提升模型处理复杂SQL语句的能力。

**任务目标**
你需要基于以上背景及评测结果，自动从互联网检索并提取能够提升模型SQL生成能力的高质量示例代码、评测样例、最佳实践、以及与SQL语法、模式定义、异常处理和性能优化相关的可执行代码片段。你的核心任务是：找到对提升SQL代码生成与评测通过率有直接帮助的代码网页。
""")

print("=" * 80)
print("WebCrawlerAgent - Web Crawling Test")
print("=" * 80)
print(f"Model: {MODEL_CONFIG['webcrawler_model']}")
print(f"API Base: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"Max Pages: {MODEL_CONFIG['webcrawler_max_pages']}")
print(f"Crawl Depth: {MODEL_CONFIG['crawl_depth']}")
print(f"Concurrent Pages: {MODEL_CONFIG['concurrent_pages']}")
print(f"Max Links Per Page: {MODEL_CONFIG['max_links_per_page']}")
print(f"Num Queries: {MODEL_CONFIG['num_queries']}")
print(f"Min Text Length: {MODEL_CONFIG['min_text_length']}")
print(f"Min Code Length: {MODEL_CONFIG['min_code_length']}")
print(f"Min Relevance Score: {MODEL_CONFIG['min_relevance_score']}")
print(f"Request Delay: {MODEL_CONFIG['request_delay']}s")
print(f"Timeout: {MODEL_CONFIG['timeout']}s")
print(f"Output Format: {MODEL_CONFIG['output_format']}")
print(f"Save HTML: {MODEL_CONFIG['save_html']}")
print(f"Test Query: {test_query}")
print(f"Output Dir: {output_dir}")
print("=" * 80)

# Check API keys
if not deepseek_api_key:
    print("\n[警告] DeepSeek API Key 未设置！")
    print("请创建 deepseek_api_key.txt 文件或设置 DEEPSEEK_API_KEY 环境变量")
    exit(1)

if not tavily_api_key:
    print("\n[警告] Tavily API Key 未设置！")
    print("请创建 tavily_api_key.txt 文件或设置 TAVILY_API_KEY 环境变量")
    exit(1)

# Initialize agent
agent = WebCrawlerAgent(
    checkpointer=checkpointer,
    store=store,
)

# Create graph
graph = agent()

# Prepare state
config = {"configurable": {"thread_id": "webcrawler_test_1"}}

initial_state = {
    'task_id': 'webcrawler_test_001',
    'output_dir': output_dir,
    'exception': '',
    'current': 'webcrawl',
    'next_to': '',
    'automated_query': '',
    'messages': [HumanMessage(content=test_query)],
    **MODEL_CONFIG
}

print("\n" + "=" * 80)
print("配置追踪（运行前确认）")
print("=" * 80)
print(f"webcrawler_deepseek_api_key: {MODEL_CONFIG['webcrawler_deepseek_api_key'][:10]}...{MODEL_CONFIG['webcrawler_deepseek_api_key'][-4:]}")
print(f"webcrawler_deepseek_api_base: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"webcrawler_model: {MODEL_CONFIG['webcrawler_model']}")
print(f"webcrawler_tavily_api_key: {MODEL_CONFIG['webcrawler_tavily_api_key'][:10]}...{MODEL_CONFIG['webcrawler_tavily_api_key'][-4:]}")
print("=" * 80)

print("\nStarting WebCrawlerAgent crawling task...")
print(f"Query: {test_query}\n")

try:
    # Run the graph
    result = graph.invoke(initial_state, config=config)
    
    print("\n" + "=" * 80)
    print("Web Crawling Results")
    print("=" * 80)
    
    # Print crawl results
    if result.get('webcrawler_output_result'):
        crawl_result = result['webcrawler_output_result']
        print("\n[Crawl Summary]")
        print("-" * 80)
        print(f"  Run ID: {crawl_result.get('run_id', 'N/A')}")
        print(f"  Total Pages Crawled: {crawl_result.get('total_pages', 0)}")
        print(f"  Output Directory: {result.get('webcrawler_output_dir', 'N/A')}")
        
        if crawl_result.get('error'):
            print(f"  Error: {crawl_result.get('error')}")
    else:
        print("\n[No crawl results available]")
    
    # Check for errors
    if result.get('exception'):
        print(f"\n[ERROR] {result['exception']}")
        print("-" * 80)
    
    # Save results to file
    output_file = Path(output_dir) / 'webcrawler_test_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'query': test_query,
            'config': {k: v for k, v in MODEL_CONFIG.items() if 'api_key' not in k},  # 不保存API密钥
            'crawl_result': result.get('webcrawler_output_result', {}),
            'output_dir': result.get('webcrawler_output_dir', ''),
            'exception': result.get('exception', ''),
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[Results saved to: {output_file}]")
    
    # Print output directory info
    if result.get('webcrawler_output_dir'):
        output_path = Path(result['webcrawler_output_dir'])
        if output_path.exists():
            print(f"\n[Output Files in {output_path}]")
            print("-" * 80)
            for file in sorted(output_path.iterdir()):
                if file.is_file():
                    size = file.stat().st_size
                    size_str = f"{size:,} bytes" if size < 1024*1024 else f"{size/(1024*1024):.2f} MB"
                    print(f"  - {file.name} ({size_str})")
    
    print("=" * 80)
    print("Web crawling test finished!")
    
except Exception as e:
    print(f"\n[ERROR] Test failed: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

