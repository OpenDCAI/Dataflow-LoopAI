"""
后处理工具算子库

该模块提供各种文件格式的后处理工具，用于将不同格式的数据文件转换为目标格式（PT/SFT）。
"""

import os
import json
import random
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import pyarrow.parquet as pq
    import pyarrow as pa
except ImportError:
    pq = None
    pa = None

try:
    from datasets import load_dataset, Dataset, DatasetDict
except ImportError:
    load_dataset = None
    Dataset = None
    DatasetDict = None

from loopai.logger import get_logger

logger = get_logger()


class PostprocessToolRegistry:
    """后处理工具注册表，用于管理和调用不同的后处理工具"""
    
    def __init__(self):
        """初始化工具注册表"""
        self._tools: Dict[str, 'PostprocessTool'] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        """注册默认的后处理工具"""
        # 注册各种文件格式的处理工具
        self.register_tool(JsonPostprocessTool())
        self.register_tool(JsonlPostprocessTool())
        self.register_tool(CsvPostprocessTool())
        self.register_tool(ParquetPostprocessTool())
        self.register_tool(TxtPostprocessTool())
        # 可以继续注册其他格式的工具
    
    def register_tool(self, tool: 'PostprocessTool'):
        """注册一个后处理工具"""
        for file_ext in tool.supported_extensions:
            if file_ext in self._tools:
                logger.warning(f"工具 {tool.__class__.__name__} 覆盖了已存在的 {file_ext} 处理工具")
            self._tools[file_ext] = tool
        logger.debug(f"注册后处理工具: {tool.__class__.__name__}, 支持格式: {tool.supported_extensions}")
    
    def get_tool(self, file_path: str) -> Optional['PostprocessTool']:
        """
        根据文件路径获取对应的后处理工具
        
        Args:
            file_path: 文件路径
            
        Returns:
            对应的后处理工具，如果找不到则返回 None
        """
        file_ext = Path(file_path).suffix.lower()
        tool = self._tools.get(file_ext)
        if tool is None:
            logger.warning(f"未找到支持 {file_ext} 格式的后处理工具: {file_path}")
        return tool
    
    def is_supported(self, file_path: str) -> bool:
        """
        检查文件格式是否支持
        
        Args:
            file_path: 文件路径
            
        Returns:
            如果支持则返回 True，否则返回 False
        """
        return self.get_tool(file_path) is not None


class PostprocessTool:
    """后处理工具基类"""
    
    def __init__(self, supported_extensions: List[str]):
        """
        初始化后处理工具
        
        Args:
            supported_extensions: 支持的文件扩展名列表（如 ['.json', '.jsonl']）
        """
        self.supported_extensions = [ext.lower() if not ext.startswith('.') else ext.lower() 
                                     for ext in supported_extensions]
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """
        读取文件的抽样数据
        
        Args:
            file_path: 文件路径
            sample_size: 抽样数量，默认 10 条
            
        Returns:
            抽样数据列表，每个元素为一条记录（字典格式）
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("子类必须实现 read_sample 方法")
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """
        处理文件并转换为目标格式
        
        Args:
            file_path: 输入文件路径
            target_format: 目标格式（如 'jsonl'）
            output_path: 输出文件路径
            category: 数据类别（PT 或 SFT），默认 PT
            **kwargs: 其他参数
            
        Returns:
            处理结果字典，包含：
            - success: 是否成功
            - records_processed: 处理的记录数
            - output_path: 输出文件路径
            - error: 错误信息（如果有）
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("子类必须实现 process 方法")
    
    def detect_format(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        检测文件格式和结构
        
        Args:
            file_path: 文件路径
            
        Returns:
            格式信息字典，包含：
            - format: 文件格式
            - encoding: 编码方式
            - structure: 数据结构信息
            如果检测失败则返回 None
        """
        # 默认实现：检查文件是否存在
        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None
        
        return {
            "format": Path(file_path).suffix.lower(),
            "file_size": os.path.getsize(file_path),
            "exists": True
        }


class JsonPostprocessTool(PostprocessTool):
    """JSON 文件后处理工具"""
    
    def __init__(self):
        super().__init__(supported_extensions=['.json'])
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """读取 JSON 文件的抽样数据"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 判断JSON结构：列表还是字典
            if isinstance(data, list):
                # 如果是列表，直接采样
                total = len(data)
                if total == 0:
                    return []
                sample_size = min(sample_size, total)
                if total <= sample_size:
                    return data
                return random.sample(data, sample_size)
            elif isinstance(data, dict):
                # 如果是字典，尝试找到包含数据的键
                # 常见的数据键名
                data_keys = ['data', 'records', 'items', 'results', 'content']
                for key in data_keys:
                    if key in data and isinstance(data[key], list):
                        records = data[key]
                        total = len(records)
                        if total == 0:
                            continue
                        sample_size = min(sample_size, total)
                        if total <= sample_size:
                            return records
                        return random.sample(records, sample_size)
                # 如果没有找到列表，返回整个字典作为一条记录
                return [data]
            else:
                logger.warning(f"Unexpected JSON structure: {type(data)}")
                return []
        except Exception as e:
            logger.error(f"Failed to read JSON sample from {file_path}: {e}")
            return []
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """处理 JSON 文件并转换为目标格式"""
        # 实际处理由DataConvertor完成，这里返回指示
        logger.info(f"JSON file processing delegated to DataConvertor: {file_path}")
        return {
            "success": True,
            "records_processed": 0,  # 实际数量由DataConvertor统计
            "output_path": output_path,
            "delegated": True
        }


class JsonlPostprocessTool(PostprocessTool):
    """JSONL 文件后处理工具"""
    
    def __init__(self):
        super().__init__(supported_extensions=['.jsonl'])
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """读取 JSONL 文件的抽样数据"""
        try:
            samples = []
            with open(file_path, 'r', encoding='utf-8') as f:
                # 先读取所有行
                lines = []
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(line)
                
                if not lines:
                    return []
                
                # 采样
                total = len(lines)
                sample_size = min(sample_size, total)
                if total <= sample_size:
                    selected_lines = lines
                else:
                    selected_indices = random.sample(range(total), sample_size)
                    selected_lines = [lines[i] for i in sorted(selected_indices)]
                
                # 解析JSON
                for line in selected_lines:
                    try:
                        record = json.loads(line)
                        if isinstance(record, dict):
                            samples.append(record)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON line: {e}")
                        continue
            
            return samples
        except Exception as e:
            logger.error(f"Failed to read JSONL sample from {file_path}: {e}")
            return []
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """处理 JSONL 文件并转换为目标格式"""
        # 实际处理由DataConvertor完成，这里返回指示
        logger.info(f"JSONL file processing delegated to DataConvertor: {file_path}")
        return {
            "success": True,
            "records_processed": 0,  # 实际数量由DataConvertor统计
            "output_path": output_path,
            "delegated": True
        }


class CsvPostprocessTool(PostprocessTool):
    """CSV 文件后处理工具"""
    
    def __init__(self):
        super().__init__(supported_extensions=['.csv', '.tsv'])
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """读取 CSV 文件的抽样数据"""
        if pd is None:
            logger.error("pandas is required for CSV file processing")
            return []
        
        try:
            # 判断分隔符
            delimiter = ',' if file_path.endswith('.csv') else '\t'
            
            # 读取前几行来估算总行数（如果文件很大）
            df = pd.read_csv(file_path, nrows=sample_size * 2, delimiter=delimiter, encoding='utf-8')
            
            if len(df) == 0:
                return []
            
            # 如果文件较小，读取全部
            if len(df) < sample_size * 2:
                df_full = pd.read_csv(file_path, delimiter=delimiter, encoding='utf-8')
                if len(df_full) <= sample_size:
                    return df_full.to_dict('records')
                return df_full.sample(n=sample_size).to_dict('records')
            
            # 文件较大，采样
            return df.sample(n=min(sample_size, len(df))).to_dict('records')
        except Exception as e:
            logger.error(f"Failed to read CSV sample from {file_path}: {e}")
            return []
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """处理 CSV 文件并转换为目标格式"""
        # 实际处理由DataConvertor完成，这里返回指示
        logger.info(f"CSV file processing delegated to DataConvertor: {file_path}")
        return {
            "success": True,
            "records_processed": 0,  # 实际数量由DataConvertor统计
            "output_path": output_path,
            "delegated": True
        }


class ParquetPostprocessTool(PostprocessTool):
    """Parquet 文件后处理工具"""
    
    def __init__(self):
        super().__init__(supported_extensions=['.parquet', '.arrow'])
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """读取 Parquet 文件的抽样数据"""
        try:
            # 方法1: 使用datasets库（推荐，支持HuggingFace格式）
            if load_dataset is not None:
                try:
                    # 尝试使用datasets加载
                    dataset = load_dataset('parquet', data_files=file_path, split='train')
                    total = len(dataset)
                    if total == 0:
                        return []
                    
                    sample_size = min(sample_size, total)
                    if total <= sample_size:
                        indices = list(range(total))
                    else:
                        indices = random.sample(range(total), sample_size)
                    
                    samples = []
                    for idx in sorted(indices):
                        record = dataset[idx]
                        # 转换为普通字典
                        if hasattr(record, '__dict__'):
                            samples.append(dict(record))
                        else:
                            samples.append(record)
                    return samples
                except Exception as e:
                    logger.debug(f"Failed to load with datasets library: {e}, trying alternative methods")
            
            # 方法2: 使用pandas
            if pd is not None:
                try:
                    df = pd.read_parquet(file_path)
                    if len(df) == 0:
                        return []
                    sample_size = min(sample_size, len(df))
                    if len(df) <= sample_size:
                        return df.to_dict('records')
                    return df.sample(n=sample_size).to_dict('records')
                except Exception as e:
                    logger.debug(f"Failed to load with pandas: {e}, trying pyarrow")
            
            # 方法3: 使用pyarrow
            if pq is not None:
                try:
                    table = pq.read_table(file_path)
                    df = table.to_pandas()
                    if len(df) == 0:
                        return []
                    sample_size = min(sample_size, len(df))
                    if len(df) <= sample_size:
                        return df.to_dict('records')
                    return df.sample(n=sample_size).to_dict('records')
                except Exception as e:
                    logger.error(f"Failed to load with pyarrow: {e}")
            
            logger.error("No suitable library available for reading Parquet files (need datasets, pandas, or pyarrow)")
            return []
        except Exception as e:
            logger.error(f"Failed to read Parquet sample from {file_path}: {e}")
            return []
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """处理 Parquet 文件并转换为目标格式"""
        # 实际处理由DataConvertor完成，这里返回指示
        logger.info(f"Parquet file processing delegated to DataConvertor: {file_path}")
        return {
            "success": True,
            "records_processed": 0,  # 实际数量由DataConvertor统计
            "output_path": output_path,
            "delegated": True
        }


class TxtPostprocessTool(PostprocessTool):
    """TXT 文件后处理工具"""
    
    def __init__(self):
        super().__init__(supported_extensions=['.txt', '.text'])
    
    def read_sample(self, file_path: str, sample_size: int = 10) -> List[Dict[str, Any]]:
        """读取 TXT 文件的抽样数据"""
        try:
            samples = []
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                for line in f:
                    line = line.strip()
                    if line:  # 跳过空行
                        lines.append(line)
                
                if not lines:
                    return []
                
                # 采样
                total = len(lines)
                sample_size = min(sample_size, total)
                if total <= sample_size:
                    selected_lines = lines
                else:
                    selected_indices = random.sample(range(total), sample_size)
                    selected_lines = [lines[i] for i in sorted(selected_indices)]
                
                # 将每行作为一条记录
                for line in selected_lines:
                    samples.append({"text": line})
            
            return samples
        except Exception as e:
            logger.error(f"Failed to read TXT sample from {file_path}: {e}")
            return []
    
    def process(
        self,
        file_path: str,
        target_format: str,
        output_path: str,
        category: str = "PT",
        **kwargs
    ) -> Dict[str, Any]:
        """处理 TXT 文件并转换为目标格式"""
        # 实际处理由DataConvertor完成，这里返回指示
        logger.info(f"TXT file processing delegated to DataConvertor: {file_path}")
        return {
            "success": True,
            "records_processed": 0,  # 实际数量由DataConvertor统计
            "output_path": output_path,
            "delegated": True
        }


# 全局工具注册表实例
_tool_registry: Optional[PostprocessToolRegistry] = None


def get_tool_registry() -> PostprocessToolRegistry:
    """获取全局工具注册表实例（单例模式）"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = PostprocessToolRegistry()
    return _tool_registry

