"""
Test script for WebCrawler Dataset Node
"""
import sys
import json
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loopai.agents.WebCrawler import WebCrawlerAgent
from loopai.memory import checkpointer, store
from loopai.schema.states import LoopAIState
from langchain_core.messages import HumanMessage

# Configuration
# Read DeepSeek API key
deepseek_api_key = None
deepseek_api_key_file = Path(__file__).parent / 'deepseek_api_key.txt'
if deepseek_api_key_file.exists():
    with open(deepseek_api_key_file, 'r') as f:
        deepseek_api_key = f.read().strip()
else:
    deepseek_api_key = os.getenv('DEEPSEEK_API_KEY', '')

# Read Tavily API key
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
    'webcrawler_temperature': 0.7,
    
    # === 爬取策略 ===
    'webcrawler_num_queries': int(os.getenv('WEBCRAWLER_NUM_QUERIES', '10')),  
    'webcrawler_max_pages': int(os.getenv('WEBCRAWLER_MAX_PAGES', '5')), 
    'webcrawler_crawl_depth': int(os.getenv('WEBCRAWLER_DEPTH', '2')),  # 深度为2
    'webcrawler_max_links_per_page': int(os.getenv('WEBCRAWLER_MAX_LINKS', '10')),  
    'webcrawler_concurrent_pages': int(os.getenv('WEBCRAWLER_CONCURRENT', '3')),  
    
    # === 内容过滤 ===
    'webcrawler_min_text_length': int(os.getenv('WEBCRAWLER_MIN_TEXT_LENGTH', '3000')), 
    'webcrawler_min_code_length': int(os.getenv('WEBCRAWLER_MIN_CODE_LENGTH', '30')),
    'webcrawler_min_relevance_score': int(os.getenv('WEBCRAWLER_MIN_RELEVANCE', '7')),  
    
    # === 数据集生成配置 ===
    'webcrawler_max_records_per_page': int(os.getenv('WEBCRAWLER_MAX_RECORDS', '5')),  # 每页最多5条记录
    'webcrawler_min_relevance_score': float(os.getenv('WEBCRAWLER_DATASET_MIN_RELEVANCE', '0.5')),  # 最低相关性0.5
    'webcrawler_dataset_concurrent_limit': int(os.getenv('WEBCRAWLER_DATASET_CONCURRENT', '3')),  # 并发3个
    'webcrawler_max_content_length': int(os.getenv('WEBCRAWLER_MAX_CONTENT_LENGTH', '50000')),  # 每页最大内容长度
    
    # === 运行时配置 ===
    'webcrawler_request_delay': float(os.getenv('WEBCRAWLER_REQUEST_DELAY', '1.0')),  
    'webcrawler_timeout': int(os.getenv('WEBCRAWLER_TIMEOUT', '20')),
    'webcrawler_max_retries': int(os.getenv('WEBCRAWLER_MAX_RETRIES', '2')),
    
    # === 调试配置 ===
    'webcrawler_debug': True,  # 开启调试模式
    
    # === 输出配置 ===
    'webcrawler_output_format': os.getenv('WEBCRAWLER_OUTPUT_FORMAT', 'jsonl'),
    'webcrawler_save_html': False,

    # === 映射后数据集目标格式（对应 Obtainer.mapping.script_mapping_node.FORMAT_MAPPERS）===
    # 可选值示例: alpaca, chatml, jsonl_pt, jsonl_sft, openai_fine_tune, llama2_chat
    'webcrawler_sft_mapping_format': os.getenv('WEBCRAWLER_SFT_FORMAT', 'alpaca'),
    'webcrawler_pt_mapping_format': os.getenv('WEBCRAWLER_PT_FORMAT', 'alpaca'),
}

# Output directory
output_dir = os.getenv('OUTPUT_DIR', str(Path(__file__).parent.parent.parent / 'output' / 'webcrawler_dataset_test'))
os.makedirs(output_dir, exist_ok=True)

# 从 report.txt 读取任务描述
report_file = Path(__file__).parent / 'report.txt'
report_content = ""
if report_file.exists():
    with open(report_file, 'r', encoding='utf-8') as f:
        report_content = f.read().strip()
else:
    print(f"[警告] 未找到 report.txt 文件: {report_file}")

# 构建测试查询：report 内容 + 通用任务目标
task_objective = """
**任务目标**
你需要基于以上报告里的背景及评测结果，自动从互联网检索能够提升模型性能的高质量训练数据和相关信息。请根据报告中提到的错误类型、改进建议等信息，智能识别需要补充的数据类型和特征，然后检索并提取相关的示例、最佳实践、教程文档、问题解答等高质量内容。你的核心任务是：找到对提升模型在评测中的表现有直接帮助的数据源和内容网页。
"""

test_query = os.getenv('TEST_QUERY', None)
if test_query is None:
    if report_content:
        test_query = report_content + "\n\n" + task_objective
    else:
        test_query = task_objective

print("=" * 80)
print("WebCrawler Dataset Node - 数据集转换测试")
print("=" * 80)
print(f"Model: {MODEL_CONFIG['webcrawler_model']}")
print(f"API Base: {MODEL_CONFIG['webcrawler_deepseek_api_base']}")
print(f"Max Pages: {MODEL_CONFIG['webcrawler_max_pages']}")
print(f"Crawl Depth: {MODEL_CONFIG['webcrawler_crawl_depth']}")
print(f"Max Records Per Page: {MODEL_CONFIG['webcrawler_max_records_per_page']}")
print(f"Max Content Length: {MODEL_CONFIG['webcrawler_max_content_length']}")
print(f"Min Relevance Score: {MODEL_CONFIG['webcrawler_min_relevance_score']}")
print(f"Test Query: {test_query}")
print(f"Output Dir: {output_dir}")
print(f"Debug Mode: {MODEL_CONFIG['webcrawler_debug']}")
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
print("\n初始化 WebCrawlerAgent...")
agent = WebCrawlerAgent(
    checkpointer=checkpointer,
    store=store,
)

# Create graph
print("创建执行图...")
graph = agent()

# Prepare state
config = {"configurable": {"thread_id": "webcrawler_dataset_test_1"}}

initial_state = {
    'task_id': 'webcrawler_dataset_test_001',
    'output_dir': output_dir,
    'exception': '',
    'current': 'webcrawl',
    'next_to': '',
    'automated_query': '',
    'messages': [HumanMessage(content=test_query)],
    **MODEL_CONFIG
}

print("\n开始执行 WebCrawler 任务...")
print("流程: start_node → crawl_node → webcrawler_dataset_node → end_node\n")

try:
    # Run the graph
    result = graph.invoke(initial_state, config=config)
    
    print("\n" + "=" * 80)
    print("执行结果")
    print("=" * 80)
    
    # Print crawl results
    if result.get('webcrawler_output_result'):
        crawl_result = result['webcrawler_output_result']
        print("\n[1. 爬取阶段结果]")
        print("-" * 80)
        print(f"  Run ID: {crawl_result.get('run_id', 'N/A')}")
        print(f"  爬取页面数: {crawl_result.get('total_pages', 0)}")
        print(f"  输出目录: {result.get('webcrawler_output_dir', 'N/A')}")
        
        if crawl_result.get('error'):
            print(f"  错误: {crawl_result.get('error')}")
    
    # Print dataset generation results
    print("\n[2. 数据集生成结果]")
    print("-" * 80)
    sft_count = result.get('webcrawler_dataset_sft_count', 0)
    pt_count = result.get('webcrawler_dataset_pt_count', 0)
    sft_path = result.get('webcrawler_dataset_sft_path', '')
    pt_path = result.get('webcrawler_dataset_pt_path', '')
    
    print(f"  SFT 记录数: {sft_count}")
    print(f"  PT 记录数: {pt_count}")
    print(f"  总记录数: {sft_count + pt_count}")
    
    if sft_path:
        print(f"  SFT 数据路径: {sft_path}")
        if os.path.exists(sft_path):
            size = os.path.getsize(sft_path)
            print(f"  SFT 文件大小: {size:,} bytes")
            
            # 显示前几条记录
            print("\n  [SFT 数据示例（前2条）]")
            with open(sft_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i >= 2:
                        break
                    record = json.loads(line)
                    print(f"    记录 {i+1}:")
                    print(f"      - 消息数: {len(record.get('messages', []))}")
                    print(f"      - 相关性: {record.get('relevance_score', 'N/A')}")
                    print(f"      - 来源: {record.get('meta', {}).get('webpage_url', 'N/A')[:60]}...")
    
    if pt_path:
        print(f"\n  PT 数据路径: {pt_path}")
        if os.path.exists(pt_path):
            size = os.path.getsize(pt_path)
            print(f"  PT 文件大小: {size:,} bytes")
            
            # 显示前几条记录
            print("\n  [PT 数据示例（前2条）]")
            with open(pt_path, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if i >= 2:
                        break
                    record = json.loads(line)
                    text = record.get('text', '')
                    text_preview = text[:100] + "..." if len(text) > 100 else text
                    print(f"    记录 {i+1}:")
                    print(f"      - 文本长度: {len(text)} 字符")
                    print(f"      - 相关性: {record.get('relevance_score', 'N/A')}")
                    print(f"      - 文本预览: {text_preview}")
    
    if not sft_path and not pt_path:
        print("  [未生成任何数据集]")
        if result.get('webcrawler_dataset_summary'):
            print(f"  说明: {result['webcrawler_dataset_summary']}")
    
    # Print mapped dataset paths (after Obtainer.mapping.script_mapping_node)
    mapped_sft_path = result.get('webcrawler_dataset_sft_mapped_path', '')
    mapped_pt_path = result.get('webcrawler_dataset_pt_mapped_path', '')
    mapping_results = result.get('webcrawler_dataset_mapping_results', {})

    print("\n[总结]")
    if sft_count > 0 or pt_count > 0:
        print(f"✓ 成功生成 {sft_count + pt_count} 条训练数据")
        print(f"  - SFT 格式（代码问答）: {sft_count} 条")
        print(f"  - PT 格式（文本内容）: {pt_count} 条")
    else:
        print("⚠ 未生成任何数据")
        print("- 请查看调试日志了解详情")

    print("\n[3. 网页摘要和相关性评分]")
    print("-" * 80)
    # 查找网页摘要文件
    dataset_dir = os.path.join(output_dir, "webcrawler_dataset")
    if os.path.exists(dataset_dir):
        import glob
        summary_files = glob.glob(os.path.join(dataset_dir, "webpage_summaries_*.jsonl"))
        if summary_files:
            latest_summary_file = max(summary_files, key=os.path.getctime)
            print(f"  摘要文件: {latest_summary_file}")
            
            # 读取并显示摘要
            with open(latest_summary_file, 'r', encoding='utf-8') as f:
                summaries = [json.loads(line) for line in f]
            
            print(f"  生成了SFT的网页数: {len(summaries)}")
            if summaries:
                avg_relevance = sum(s.get('relevance_score', 0) for s in summaries) / len(summaries)
                print(f"  平均相关性评分: {avg_relevance:.2f}/10")
                
                # 显示相关性最高的2个网页
                print("\n  [相关性最高的网页（前2个）]")
                sorted_summaries = sorted(summaries, key=lambda x: x.get('relevance_score', 0), reverse=True)
                for i, summary in enumerate(sorted_summaries[:2], 1):
                    print(f"    {i}. {summary.get('title', 'N/A')}")
                    print(f"       URL: {summary.get('url', 'N/A')[:60]}...")
                    print(f"       相关性评分: {summary.get('relevance_score', 0)}/10")
                    summary_text = summary.get('summary', '')
                    summary_preview = summary_text[:100] + "..." if len(summary_text) > 100 else summary_text
                    print(f"       摘要: {summary_preview}")
        else:
            print("  [未生成网页摘要文件]")
    else:
        print("  [数据集目录不存在]")

    print("\n[4. 映射后的最终数据集文件]")
    print("-" * 80)
    if mapped_sft_path:
        print(f"  SFT 映射文件: {mapped_sft_path}")
    if mapped_pt_path:
        print(f"  PT 映射文件: {mapped_pt_path}")
    if not mapped_sft_path and not mapped_pt_path:
        print("  [尚未生成映射后的最终数据集文件，或脚本映射未执行]")

    # Check for errors
    if result.get('exception'):
        print(f"\n[错误]")
        print("-" * 80)
        print(f"  {result['exception']}")
    
    # Save test results
    output_file = Path(output_dir) / 'test_results.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'query': test_query,
            'config': {k: v for k, v in MODEL_CONFIG.items() if 'api_key' not in k},
            'crawl_result': result.get('webcrawler_output_result', {}),
            'dataset_sft_count': sft_count,
            'dataset_pt_count': pt_count,
            'dataset_sft_path': sft_path,
            'dataset_pt_path': pt_path,
            'dataset_sft_mapped_path': mapped_sft_path,
            'dataset_pt_mapped_path': mapped_pt_path,
            'dataset_mapping_results': mapping_results,
            'exception': result.get('exception', ''),
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n[测试结果已保存至: {output_file}]")
    
    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)

    
except Exception as e:
    print(f"\n[错误] 测试失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

