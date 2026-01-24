from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional


@dataclass
class CrawledContent:
    """爬取的内容数据结构"""
    url: str
    title: str
    content: str
    author: Optional[str] = None
    publish_date: Optional[str] = None
    tags: List[str] = None
    code_blocks: List[Dict[str, str]] = None
    headings: List[Dict[str, str]] = None
    ai_summary: Optional[str] = None
    links: List[Dict[str, str]] = None
    relevant_links: List[str] = None
    metadata: Dict[str, Any] = None
    
    def to_dict(self):
        return asdict(self)