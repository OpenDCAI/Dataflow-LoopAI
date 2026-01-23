import os
import re
import json
from pathlib import Path
from typing import Any
from loopai.logger import get_logger

logger = get_logger()


class LogManager:
    """简单的日志管理器,兼容WebResearchAgent的logger接口"""
    
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.counter = 0
    
    def log_data(self, name: str, data: Any, is_json: bool = False):
        """记录日志数据"""
        self.counter += 1
        safe_name = re.sub(r'[^\w\-_]', '_', name)
        filename = f"{self.counter:03d}_{safe_name}.json"
        filepath = self.log_dir / filename
        
        try:
            if isinstance(data, (dict, list)):
                log_data = data
            else:
                log_data = {"data": str(data)}
            
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(log_data, f, ensure_ascii=False, indent=2)
            logger.info(f"   日志已保存: {filename}")
        except Exception as e:
            logger.info(f"   保存日志失败: {e}")