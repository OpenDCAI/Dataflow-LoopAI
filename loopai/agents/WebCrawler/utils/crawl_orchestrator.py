import os
import re
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from openai import AsyncOpenAI
from playwright.async_api import async_playwright

from .data_structures import CrawledContent
from .content_analyzer import ContentAnalyzer
from .log_manager import LogManager
from loopai.logger import get_logger

logger = get_logger()


def extract_code_blocks_from_markdown(text: str) -> List[Dict[str, str]]:
    """
    从 Markdown 文本中提取代码块
    
    支持两种格式:
    1. 带语言标识的代码块: ```python ... ```
    2. 不带语言标识的代码块: ``` ... ```
    
    Returns:
        List[Dict[str, str]]: 每个字典包含 'language', 'code', 'length' 字段
    """
    code_blocks = []
    
    # 正则匹配 markdown 代码块
    # 匹配 ```language\n code \n``` 或 ```\n code \n```
    pattern = r'```(\w*)\n(.*?)```'
    matches = re.findall(pattern, text, re.DOTALL)
    
    for match in matches:
        language = match[0].strip() if match[0] else 'unknown'
        code = match[1].strip()
        
        # 过滤掉过短的代码块（可能是示例或占位符）
        if len(code) >= 20:  # 至少20字符
            code_blocks.append({
                'language': language,
                'code': code,
                'length': len(code)
            })
    
    # 如果没有找到，尝试匹配缩进代码块（4个空格或1个tab开头的连续行）
    if not code_blocks:
        lines = text.split('\n')
        current_block = []
        in_code_block = False
        
        for line in lines:
            # 检查是否是缩进的代码行
            if line.startswith('    ') or line.startswith('\t'):
                if not in_code_block:
                    in_code_block = True
                    current_block = []
                # 去掉缩进
                clean_line = line[4:] if line.startswith('    ') else line[1:]
                current_block.append(clean_line)
            else:
                if in_code_block and current_block:
                    code = '\n'.join(current_block).strip()
                    if len(code) >= 20:
                        code_blocks.append({
                            'language': 'unknown',
                            'code': code,
                            'length': len(code)
                        })
                    current_block = []
                in_code_block = False
        
        # 处理最后一个代码块
        if in_code_block and current_block:
            code = '\n'.join(current_block).strip()
            if len(code) >= 20:
                code_blocks.append({
                    'language': 'unknown',
                    'code': code,
                    'length': len(code)
                })
    
    return code_blocks


class MockRAGManager:
    """临时的 RAG Mock 类，用于禁用 RAG 功能（测试用）"""
    
    async def add_webpage_content(self, url: str, text_content: str, metadata: dict = None):
        """空操作：不存储内容到向量数据库"""
        logger.debug(f"[RAG 已禁用] 跳过存储: {url}")
        pass
    
    async def force_persist(self):
        """空操作：不持久化数据"""
        logger.debug("[RAG 已禁用] 跳过持久化")
        pass
    
    async def get_context_for_single_query(self, query: str, max_chars: int = 18000) -> str:
        """返回空上下文，不使用向量检索"""
        logger.debug(f"[RAG 已禁用] 跳过向量检索，返回空上下文")
        return ""


class CrawlOrchestrator:
    """爬取编排器"""
    
    def __init__(
        self,
        deepseek_api_key: str,
        tavily_api_key: str,
        deepseek_api_base: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        max_pages: int = 10000,
        output_dir: str = "./crawl_output",
        stream_callback: Optional[Callable] = None,
        # 爬取策略参数
        num_queries: int = 5,
        crawl_depth: int = 3,
        max_links_per_page: int = 5,
        concurrent_pages: int = 3,
        # 内容过滤参数
        min_text_length: int = 500,
        min_code_length: int = 50,
        min_relevance_score: int = 6,
        url_patterns: Optional[List[str]] = None,
        # 运行时配置参数
        request_delay: float = 2.0,
        timeout: int = 30,
        max_retries: int = 3,
        # 输出配置参数
        output_format: str = "jsonl",
        save_html: bool = False
    ):
        self.client = AsyncOpenAI(
            api_key=deepseek_api_key,
            base_url=deepseek_api_base
        )
        self.model = model
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_api_base = deepseek_api_base
        self.tavily_api_key = tavily_api_key
        
        # 调试日志：打印实际使用的配置
        logger.info(f"[DEBUG] CrawlOrchestrator 初始化配置:")
        logger.info(f"[DEBUG]   API Base: {deepseek_api_base}")
        logger.info(f"[DEBUG]   Model: {model}")
        logger.info(f"[DEBUG]   API Key: {deepseek_api_key[:10]}...{deepseek_api_key[-4:] if len(deepseek_api_key) > 14 else '(空)'}")
        self.content_analyzer = ContentAnalyzer(self.client, model)
        self.max_pages = max_pages
        self.output_dir = Path(output_dir)
        self.stream_callback = stream_callback
        
        # 爬取策略配置
        self.num_queries = num_queries
        self.crawl_depth = crawl_depth
        self.max_links_per_page = max_links_per_page
        self.concurrent_pages = concurrent_pages
        
        # 内容过滤配置
        self.min_text_length = min_text_length
        self.min_code_length = min_code_length
        self.min_relevance_score = min_relevance_score
        self.url_patterns = url_patterns
        
        # 运行时配置
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_retries = max_retries
        
        # 输出配置
        self.output_format = output_format
        self.save_html = save_html
        
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = self.output_dir / f"run_{self.run_id}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = LogManager(self.run_dir)

    
    def _send_stream_event(self, current: str, message: str, data: Optional[Dict] = None, 
                          progress: Optional[float] = None, progress_num: Optional[int] = None, 
                          total: Optional[int] = None):
        """发送流式事件（如果有回调函数）"""
        if self.stream_callback:
            from loopai.schema.events import StreamEvent
            event = StreamEvent(
                current=current,
                message=message,
                data=data,
                progress=progress,
                progress_num=progress_num,
                total=total
            )
            try:
                self.stream_callback(event.json())
            except Exception as e:
                logger.warning(f"Failed to send stream event: {e}")

    
    async def run(self, task: str) -> Dict[str, Any]:
        """执行爬取任务"""
        logger.info("="*60)
        logger.info(f"开始爬取任务: {task}")
        logger.info(f"输出目录: {self.run_dir}")
        logger.info(f"模型: {self.model}")
        logger.info(f"最大页面数: {self.max_pages}")
        logger.info("="*60)
        
        self._send_stream_event(
            current="crawl_orchestrator",
            message="开始网页爬取流程",
            data={"task": task, "max_pages": self.max_pages}
        )
        
        # 1. 生成搜索查询
        logger.info("\n步骤1: 生成搜索查询...")
        self._send_stream_event(
            current="generate_queries",
            message="正在生成搜索查询..."
        )
        
        search_queries = await self._generate_search_queries(task)
        self.logger.log_data("search_queries", search_queries)
        logger.info(f"生成了 {len(search_queries)} 个搜索查询: {search_queries}")
        
        if not search_queries:
            logger.warning("未能生成搜索查询,使用默认查询")
            search_queries = [task[:100]]
        
        # 输出生成的查询
        self._send_stream_event(
            current="generate_queries",
            message=f"成功生成 {len(search_queries)} 个搜索查询",
            data={"queries": search_queries, "query_count": len(search_queries)}
        )
        
        # 2. 执行深度搜索
        logger.info("\n步骤2: 执行深度搜索（WebSearch Node）...")
        self._send_stream_event(
            current="deep_search",
            message="开始执行深度网页搜索",
            total=len(search_queries)
        )

        from loopai.agents.Obtainer.utils import (
            # RAGManager,  # 临时禁用 RAG（测试用）
            WebTools,
            QueryGenerator,
            SummaryAgent,
            URLSelector,
        )
        from loopai.common.prompts import PromptLoader

        # 引入 websearch_node 和内部 workflow
        from loopai.agents.Obtainer.nodes.websearch_node import (
            websearch_node,
            _websearch_workflow,
        )

        # 初始化组件
        # 使用与本爬虫相同的模型 / Base URL / API Key 配置，保持行为一致
        prompt_loader = PromptLoader()
        # ===== 临时禁用 RAG（测试用）=====
        # rag_manager = RAGManager()  # 仍然走 RAG 自己的 API 配置（RAG_API_URL/RAG_API_KEY 等）
        rag_manager = MockRAGManager()  # 使用 Mock 类，跳过 RAG 初始化和向量检索
        logger.info("[RAG 已禁用] 使用 MockRAGManager，跳过向量检索功能")
        # =================================
        web_tools = WebTools()
        query_generator = QueryGenerator(
            model_name=self.model,
            base_url=self.deepseek_api_base,
            api_key=self.deepseek_api_key,
            temperature=0.7,
            prompt_loader=prompt_loader,
        )
        summary_agent = SummaryAgent(
            model_name=self.model,
            base_url=self.deepseek_api_base,
            api_key=self.deepseek_api_key,
            temperature=0.7,
            prompt_loader=prompt_loader,
        )
        url_selector = URLSelector(
            model_name=self.model,
            base_url=self.deepseek_api_base,
            api_key=self.deepseek_api_key,
            temperature=0.3,
            prompt_loader=prompt_loader,
        )

        all_crawled_data = []

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()

            for i, query in enumerate(search_queries, 1):
                logger.info(f"\n[查询 {i}/{len(search_queries)}] 开始 WebSearch Node 深度搜索: {query}")
                
                self._send_stream_event(
                    current="deep_search",
                    message=f"执行查询 {i}/{len(search_queries)}: {query[:50]}{'...' if len(query) > 50 else ''}",
                    progress_num=i,
                    total=len(search_queries),
                    progress=i / len(search_queries)
                )

                try:
                    # === 直接调用 BFS 深度爬取的完整 workflow ===
                    result = await _websearch_workflow(
                        user_query=query,
                        query_generator=query_generator,
                        summary_agent=summary_agent,
                        rag_manager=rag_manager,
                        url_selector=url_selector,
                        search_engine="tavily",
                        max_urls=self.max_links_per_page,
                        max_depth=self.crawl_depth,
                        concurrent_limit=self.concurrent_pages,
                        topk_urls=self.max_links_per_page,
                        url_timeout=self.timeout,
                        tavily_api_key=self.tavily_api_key,
                        debug_mode=False,
                    )

                    crawled_items = result.get("crawled_pages", [])
                    logger.info(f"  搜索完成，收集到 {len(crawled_items)} 个页面")
                    
                    self._send_stream_event(
                        current="deep_search",
                        message=f"查询 {i} 完成 - 收集到 {len(crawled_items)} 个页面",
                        data={"query_index": i, "pages_collected": len(crawled_items)}
                    )

                    all_crawled_data.extend(crawled_items)

                    # 保存每次小总结
                    if result.get("research_summary"):
                        self.logger.log_data(
                            f"research_summary_{i:02d}_{query[:20]}",
                            result["research_summary"]
                        )

                    # 达到限制提前停止
                    if len(all_crawled_data) >= self.max_pages:
                        logger.info(f"达到最大页面数量 {self.max_pages}，提前停止")
                        break

                except Exception as e:
                    logger.error(f"执行 WebSearch Node 查询失败 '{query}': {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    continue

            await browser.close()

        
        logger.info(f"\n搜索阶段完成,总共收集 {len(all_crawled_data)} 个网页内容")
        
        self._send_stream_event(
            current="deep_search",
            message=f"搜索阶段完成 - 共收集 {len(all_crawled_data)} 个网页",
            data={"total_collected": len(all_crawled_data)}
        )
        
        # 3. 转换为统一格式并生成AI摘要
        logger.info("\n步骤3: 处理网页数据并生成AI摘要...")
        self._send_stream_event(
            current="process_content",
            message="开始处理网页内容并生成AI摘要",
            total=min(len(all_crawled_data), self.max_pages)
        )
        
        converted_data = []
        for i, raw_data in enumerate(all_crawled_data[:self.max_pages], 1):
            logger.info(f"\n[页面 {i}/{min(len(all_crawled_data), self.max_pages)}] 处理网页数据...")
            
            self._send_stream_event(
                current="process_content",
                message=f"处理页面 {i}/{min(len(all_crawled_data), self.max_pages)}",
                progress_num=i,
                total=min(len(all_crawled_data), self.max_pages),
                progress=i / min(len(all_crawled_data), self.max_pages)
            )
            
            source_url = raw_data.get('source_url', '')
            text_content = raw_data.get('text_content', '')
            
            logger.info(f"  URL: {source_url}")
            logger.info(f"  内容长度: {len(text_content)} 字符")
            
            # 应用最小文本长度过滤
            if not text_content or len(text_content.strip()) < self.min_text_length:
                logger.info(f"  跳过: 空内容或内容长度 < {self.min_text_length} 字符")
                continue
            
            # 从 Markdown 内容中提取代码块
            code_blocks = extract_code_blocks_from_markdown(text_content)
            if code_blocks:
                logger.info(f"  提取到 {len(code_blocks)} 个代码块")
            
            content = CrawledContent(
                url=source_url,
                title=raw_data.get('structured_content', {}).get('title', '') if 'structured_content' in raw_data else '',
                content=text_content,
                code_blocks=code_blocks if code_blocks else None,
                metadata={
                    "extraction_method": raw_data.get('extraction_method', 'unknown'),
                    "content_length": len(text_content),
                    "code_blocks_count": len(code_blocks) if code_blocks else 0
                }
            )
            
            logger.info(f"  生成AI摘要...")
            try:
                summary = await self.content_analyzer.generate_summary(content)
                content.ai_summary = summary
                preview = summary[:100] + "..." if len(summary) > 100 else summary
                logger.info(f"  AI摘要: {preview}")
            except Exception as e:
                logger.error(f"  生成摘要失败: {e}")
                content.ai_summary = "摘要生成失败"
            
            converted_data.append(content)
            self.logger.log_data(f"page_{i:03d}", content.to_dict())
        
        logger.info(f"\n数据处理完成,有效页面: {len(converted_data)}")
        
        self._send_stream_event(
            current="process_content",
            message=f"网页处理完成 - 有效页面 {len(converted_data)} 个",
            data={"valid_pages": len(converted_data)}
        )
        
        # 4. 生成整体摘要
        if converted_data:
            logger.info("\n步骤4: 生成整体摘要...")
            self._send_stream_event(
                current="generate_summary",
                message="正在生成整体摘要..."
            )
            
            overall_summary = await self._generate_overall_summary(task, converted_data)
            self.logger.log_data("overall_summary", overall_summary)
            logger.info(f"整体摘要生成完成")
            
            self._send_stream_event(
                current="generate_summary",
                message="整体摘要生成完成",
                data={
                    "overview": overall_summary.get("overview", "")[:200],
                    "key_findings_count": len(overall_summary.get("key_findings", [])),
                    "sources_count": len(overall_summary.get("sources", []))
                }
            )
        else:
            logger.warning("无有效爬取数据,跳过整体摘要生成")
            overall_summary = {"message": "无有效爬取数据"}
            
            self._send_stream_event(
                current="generate_summary",
                message="无有效数据，跳过摘要生成"
            )
        
        # 5. 保存最终结果
        logger.info("\n步骤5: 保存最终结果...")
        self._send_stream_event(
            current="save_results",
            message="正在保存最终结果..."
        )
        
        final_result = {
            "task": task,
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "total_pages": len(converted_data),
            "search_queries": search_queries,
            "crawled_data": [c.to_dict() for c in converted_data],
            "overall_summary": overall_summary,
            "statistics": {
                "pages_analyzed": len(converted_data),
                "total_content_length": sum(len(c.content) for c in converted_data),
                "avg_content_length": sum(len(c.content) for c in converted_data) // len(converted_data) if converted_data else 0
            }
        }
        
        self.logger.log_data("final_result", final_result)
        
        logger.info("\n"+"="*60)
        logger.info("爬取任务完成!")
        logger.info(f"  - 爬取页面数: {len(converted_data)}")
        logger.info(f"  - 搜索查询数: {len(search_queries)}")
        logger.info(f"  - 总内容长度: {final_result['statistics']['total_content_length']} 字符")
        logger.info(f"  - 输出目录: {self.run_dir}")
        logger.info("="*60)
        
        self._send_stream_event(
            current="save_results",
            message=f"爬取任务全部完成 - 成功处理 {len(converted_data)} 个页面",
            data={
                "total_pages": len(converted_data),
                "search_queries": len(search_queries),
                "total_content_length": final_result['statistics']['total_content_length'],
                "output_dir": str(self.run_dir)
            }
        )
        
        return final_result
    
    def _create_error_result(self, error_msg: str) -> Dict[str, Any]:
        """创建错误结果"""
        logger.error(f"创建错误结果: {error_msg}")
        return {
            "task": "",
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "total_pages": 0,
            "search_queries": [],
            "crawled_data": [],
            "overall_summary": {"error": error_msg},
            "statistics": {
                "pages_analyzed": 0,
                "total_content_length": 0,
                "avg_content_length": 0
            }
        }
    
    async def _generate_search_queries(self, task: str) -> List[str]:
        """生成搜索查询"""
        prompt = f"""你是一个专业的网页信息搜寻助手。根据代码生成模型的评估报告,生成{self.num_queries}个搜索查询关键词,用于查找能够改进数据集的相关内容。

评估报告问题分析: {task}

你的任务:
1. 仔细分析评估报告中提到的问题和建议
2. 识别出需要补充的数据类型和内容方向
3. 提取出最关键的几个改进点
4. 针对这些改进点,生成能够找到相关高质量内容的搜索关键词

搜索关键词要求:
- 具体明确,适合搜索引擎检索
- 能够找到包含代码示例、教程、最佳实践的技术内容
- 覆盖评估报告中提到的不同改进方向
- 优先使用英文关键词(技术内容英文资源更丰富)
- 关键词应该能够定位到详细的、可学习的内容
- 生成恰好{self.num_queries}个查询

请直接返回JSON格式(不要markdown代码块):
{{
    "queries": ["query1", "query2", ..., "query{self.num_queries}"]
}}"""
        
        try:
            logger.info("  调用LLM生成搜索查询...")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            
            content = response.choices[0].message.content.strip()
            self.logger.log_data("search_queries_raw", {"raw": content})
            
            # 清理markdown代码块
            content = re.sub(r'^```json\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            
            data = json.loads(content)
            queries = data.get("queries", [])
            
            if not queries:
                logger.warning("LLM未返回查询")
                return []
            
            logger.info(f"  成功生成 {len(queries)} 个查询")
            return queries
            
        except json.JSONDecodeError as e:
            logger.error(f"解析JSON失败: {e}")
            logger.error(f"LLM返回内容: {content}")
            return []
        except Exception as e:
            logger.error(f"生成搜索查询失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []
    
    async def _generate_overall_summary(
        self, 
        task: str, 
        crawled_data: List[CrawledContent]
    ) -> Dict[str, Any]:
        """生成整体摘要(基于各网页的AI摘要)"""
        
        context_parts = []
        for i, item in enumerate(crawled_data, 1):
            context_parts.append(
                f"【来源 {i}】\n"
                f"标题: {item.title}\n"
                f"URL: {item.url}\n"
                f"AI摘要: {item.ai_summary or '无摘要'}\n"
            )
        
        context = "\n---\n".join(context_parts)
        
        prompt = f"""基于爬取的网页内容和AI分析摘要,生成一个整体的结构化报告。

用户任务: {task}

已分析的网页内容:
{context}

请生成JSON格式的整体摘要(不要markdown代码块),包含:
{{
    "overview": "整体概述(3-5句话,总结所有网页的共同主题)",
    "key_findings": ["发现1", "发现2", "发现3"],
    "sources": [
        {{"title": "标题", "url": "链接", "summary": "简短总结", "relevance_score": 数字(1-10,表示与用户任务的相关性)}},
        ...
    ],
    "recommendations": ["建议1", "建议2"]
}}

要求:
1. 对sources按照与用户任务的相关性进行排序,最相关的排在前面
2. 为每个source添加relevance_score字段(1-10分),表示与用户任务"{task}"的相关程度
3. sources数组应该按relevance_score从高到低排序
4. 只包含relevance_score >= {self.min_relevance_score}的网页,低于该分数的网页从报告中剔除"""
        
        try:
            logger.info("  调用LLM生成整体摘要...")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            content = response.choices[0].message.content.strip()
            self.logger.log_data("overall_summary_raw", {"raw": content})
            
            # 清理markdown代码块
            content = re.sub(r'^```json\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            
            summary = json.loads(content)
            
            # 应用最小相关性评分过滤
            if "sources" in summary and isinstance(summary["sources"], list):
                original_count = len(summary["sources"])
                summary["sources"] = [
                    s for s in summary["sources"] 
                    if s.get("relevance_score", 0) >= self.min_relevance_score
                ]
                filtered_count = original_count - len(summary["sources"])
                if filtered_count > 0:
                    logger.info(f"  根据最小相关性评分({self.min_relevance_score})过滤掉 {filtered_count} 个低相关性网页")
            
            logger.info("  整体摘要生成成功")
            return summary
            
        except json.JSONDecodeError as e:
            logger.error(f"解析整体摘要JSON失败: {e}")
            logger.error(f"LLM返回内容: {content}")
            return {
                "error": f"JSON parse error: {str(e)}",
                "raw_summary": f"爬取了 {len(crawled_data)} 个页面"
            }
        except Exception as e:
            logger.error(f"生成整体摘要失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {
                "error": str(e),
                "raw_summary": f"爬取了 {len(crawled_data)} 个页面"
            }