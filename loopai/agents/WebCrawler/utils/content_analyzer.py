from typing import Optional
from openai import AsyncOpenAI
from .data_structures import CrawledContent
from loopai.logger import get_logger

logger = get_logger()


class ContentAnalyzer:
    """内容分析器 - 使用LLM分析网页内容"""
    
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model
    
    async def generate_summary(self, content: CrawledContent) -> str:
        """生成网页内容摘要"""
        
        prompt_parts = []
        prompt_parts.append(f"网页标题: {content.title}")
        prompt_parts.append(f"URL: {content.url}")
        
        if content.author:
            prompt_parts.append(f"作者: {content.author}")
        
        if content.publish_date:
            prompt_parts.append(f"发布日期: {content.publish_date}")
        
        if content.headings:
            headings_text = "\n".join([f"- {h['level']}: {h['text']}" for h in content.headings[:10]])
            prompt_parts.append(f"\n标题结构:\n{headings_text}")
        
        if content.code_blocks:
            code_info = []
            for i, code in enumerate(content.code_blocks[:3], 1):
                code_preview = code['code'][:200] + "..." if len(code['code']) > 200 else code['code']
                code_info.append(f"代码块{i} ({code['language']}, {code['length']}字符):\n{code_preview}")
            prompt_parts.append(f"\n代码块示例:\n" + "\n\n".join(code_info))
        
        content_preview = content.content[:10000] + "..." if len(content.content) > 10000 else content.content
        prompt_parts.append(f"\n主要内容:\n{content_preview}")
        
        analysis_prompt = "\n\n".join(prompt_parts)
        
        system_prompt = """你是一个专业的内容分析助手。请分析给定的网页内容,生成一个简洁的摘要(约100字)。

要求:
1. 概括网页的主题和核心内容
2. 如果有代码,简要说明代码的用途和技术栈
3. 突出重点信息和关键技术点
4. 使用简洁专业的语言
5. 不要包含无关信息或过多细节

直接输出摘要文本,不需要其他格式。"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": analysis_prompt}
                ],
                temperature=0.3,
                max_tokens=300
            )
            
            summary = response.choices[0].message.content.strip()
            return summary
            
        except Exception as e:
            logger.error(f"生成摘要失败: {e}")
            return f"内容摘要生成失败: {str(e)}"