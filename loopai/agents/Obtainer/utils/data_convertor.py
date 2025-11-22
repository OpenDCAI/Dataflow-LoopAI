import asyncio
import os
import json
import re
import zipfile
import tarfile
import gzip
import bz2
import lzma
import shutil
import tempfile
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path

try:
    import pandas as pd
    import numpy as np
except ImportError:
    pd = None
    np = None

try:
    from datasets import load_dataset, DownloadConfig, Dataset, DatasetDict
    TYPE_CHECKING = True
except ImportError:
    TYPE_CHECKING = False
    Dataset = None
    DatasetDict = None

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage

from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader
from loopai.agents.Obtainer.utils.unified_format import (
    UnifiedDataFormat,
    validate_unified_format,
    DatasetType,
    MessageRole,
    generate_id,
    extract_meta_from_context,
)

logger = get_logger()


class SimpleDataset:
    """A lightweight dataset container that mimics the interface used from HuggingFace datasets."""

    def __init__(self, records: List[Dict[str, Any]]):
        self._records = records
        self._column_names = list(records[0].keys()) if records else []

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self._records[index]

    def __iter__(self):
        return iter(self._records)

    @property
    def column_names(self) -> List[str]:
        return self._column_names


def _build_simple_dataset(records: List[Dict[str, Any]]) -> Optional[Dict[str, SimpleDataset]]:
    if not records:
        return None
    return {"train": SimpleDataset(records)}


def _ensure_hf_cache_env(download_dir: Optional[str]) -> None:
    """Ensure HuggingFace related environment variables point to download directory."""
    if not download_dir:
        return

    base_dir = os.path.abspath(download_dir)
    hf_cache_root = os.path.join(base_dir, ".cache", "hf")
    hub_dir = os.path.join(hf_cache_root, "hub")
    datasets_dir = os.path.join(hf_cache_root, "datasets")
    transformers_dir = os.path.join(hf_cache_root, "transformers")

    for path in (hf_cache_root, hub_dir, datasets_dir, transformers_dir):
        os.makedirs(path, exist_ok=True)

    os.environ.setdefault("HF_HOME", hf_cache_root)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", hub_dir)
    os.environ.setdefault("HF_DATASETS_CACHE", datasets_dir)
    os.environ.setdefault("TRANSFORMERS_CACHE", transformers_dir)


class DataConvertor:
    """Data converter for mapping and extracting data from downloaded datasets"""

    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_sample_length: int = 200,
        num_sample_records: int = 3,
        prompt_loader: Optional[PromptLoader] = None,
    ):
        """Initialize Data Convertor"""
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_sample_length = max_sample_length
        self.num_sample_records = num_sample_records
        self.prompt_loader = prompt_loader
        
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self._temp_dirs = []

    def _truncate_value(self, value: Any, max_length: int = None) -> Any:
        """Truncate a single value to prevent excessive length."""
        if max_length is None:
            max_length = self.max_sample_length
            
        if isinstance(value, str):
            if len(value) > max_length:
                return value[:max_length] + "..."
            return value
        elif isinstance(value, (list, tuple)):
            if len(value) > 3:
                return [self._truncate_value(v, max_length) for v in value[:3]] + ["..."]
            return [self._truncate_value(v, max_length) for v in value]
        elif isinstance(value, dict):
            if len(value) > 3:
                truncated = {k: self._truncate_value(v, max_length) for k, v in list(value.items())[:3]}
                truncated["..."] = "..."
                return truncated
            return {k: self._truncate_value(v, max_length) for k, v in value.items()}
        else:
            return value
    
    async def _sample_records(self, dataset: Any, num_samples: int = None) -> List[Dict[str, Any]]:
        """Sample records from dataset with truncation (async to avoid blocking event loop)."""
        if num_samples is None:
            num_samples = self.num_sample_records
            
        import random
        
        # Use asyncio.to_thread for potentially blocking operations
        def _get_dataset_size():
            return len(dataset)
        
        def _get_record(idx):
            return dataset[idx]
        
        dataset_size = await asyncio.to_thread(_get_dataset_size)
        if dataset_size == 0:
            return []
        
        actual_samples = min(num_samples, dataset_size)
        
        if dataset_size <= actual_samples:
            sample_indices = list(range(dataset_size))
        else:
            sample_indices = random.sample(range(dataset_size), actual_samples)
        
        # Sample records concurrently to avoid blocking
        async def _sample_single_record(idx: int):
            record = await asyncio.to_thread(_get_record, idx)
            truncated_record = {k: self._truncate_value(v) for k, v in record.items()}
            return truncated_record
        
        # Sample all records concurrently
        sampled_records = await asyncio.gather(*[_sample_single_record(idx) for idx in sample_indices])
        
        logger.info(f"Sampled {len(sampled_records)} records from dataset (total: {dataset_size})")
        return sampled_records

    FIELD_TOKEN_PATTERN = re.compile(r"([^\[\]]+)(?:\[(.*?)\])?")

    def _field_exists_in_columns(self, field_spec: Optional[Any], column_names: List[str]) -> bool:
        if field_spec is None:
            return False
        if isinstance(field_spec, list):
            if not field_spec:
                return False
            return all(self._field_exists_in_columns(spec, column_names) for spec in field_spec)
        # Ensure field_spec is a string before calling split
        if not isinstance(field_spec, str):
            # If it's a dict or other non-string type, try to convert to string or return False
            if isinstance(field_spec, dict):
                # If it's a dict, check if any of its keys match column names
                return any(key in column_names for key in field_spec.keys() if isinstance(key, str))
            return False
        token = field_spec.split(".")[0]
        token = token.split("[")[0]
        return token in column_names

    def _normalize_field_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    def _traverse_field_tokens(self, current: Any, tokens: List[str]) -> List[Any]:
        if current is None:
            return []
        if not tokens:
            if isinstance(current, list):
                results: List[Any] = []
                for item in current:
                    results.extend(self._traverse_field_tokens(item, []))
                return results
            if isinstance(current, dict):
                return list(current.items())
            return [current]

        token = tokens[0]
        match = self.FIELD_TOKEN_PATTERN.match(token)
        if not match:
            return []
        name, index = match.group(1), match.group(2)

        if isinstance(current, dict):
            next_value = current.get(name)
        else:
            return []

        if index is None or index == "":
            return self._traverse_field_tokens(next_value, tokens[1:])

        if not isinstance(next_value, list):
            if isinstance(next_value, dict):
                results: List[Any] = []
                for key, item in next_value.items():
                    child_results = self._traverse_field_tokens(item, tokens[1:])
                    if not child_results:
                        results.append((key, item))
                        continue
                    for child in child_results:
                        results.append((key, child))
                return results
            return []

        results: List[Any] = []
        if index == "*" or index.lower() == "all":
            for item in next_value:
                results.extend(self._traverse_field_tokens(item, tokens[1:]))
        else:
            try:
                idx = int(index)
                if 0 <= idx < len(next_value):
                    results.extend(self._traverse_field_tokens(next_value[idx], tokens[1:]))
            except ValueError:
                return []
        return results

    def _stringify_structure(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, dict):
            parts: List[str] = []
            for key, sub_value in value.items():
                key_str = self._normalize_field_value(key) or str(key)
                sub_str = self._stringify_structure(sub_value)
                if sub_str:
                    parts.append(f"{key_str}. {sub_str}")
                else:
                    parts.append(key_str)
            return "; ".join(part for part in parts if part) if parts else None
        if isinstance(value, (list, tuple, set)):
            parts: List[str] = []
            for item in value:
                sub_str = self._stringify_structure(item)
                if sub_str:
                    parts.append(sub_str)
            return "; ".join(part for part in parts if part) if parts else None
        return self._normalize_field_value(value)

    def _format_mapping_entry(self, key: Any, content: Any) -> Optional[str]:
        key_str = self._normalize_field_value(key) or str(key)
        content_str = self._stringify_structure(content)
        if content_str:
            return f"{key_str}. {content_str}"
        return key_str

    def _extract_field_values(self, row: Dict[str, Any], field_spec: Optional[str]) -> List[str]:
        if not field_spec:
            return []
        # Ensure field_spec is a string before calling strip/split
        if not isinstance(field_spec, str):
            # If field_spec is a dict, try to extract values from it
            if isinstance(field_spec, dict):
                normalized = []
                for key, value in field_spec.items():
                    entry = self._format_mapping_entry(key, value)
                    if entry:
                        normalized.append(entry)
                return normalized
            # For other non-string types, convert to string
            field_spec = str(field_spec)
        field_spec = field_spec.strip()
        if not field_spec:
            return []
        tokens = field_spec.split(".")
        raw_values = self._traverse_field_tokens(row, tokens)
        normalized: List[str] = []
        for value in raw_values:
            if isinstance(value, tuple) and len(value) == 2:
                entry = self._format_mapping_entry(value[0], value[1])
                if entry:
                    normalized.append(entry)
                continue
            if isinstance(value, dict):
                for key, sub_value in value.items():
                    entry = self._format_mapping_entry(key, sub_value)
                    if entry:
                        normalized.append(entry)
                continue
            normalized_value = self._normalize_field_value(value)
            if normalized_value is not None:
                normalized.append(normalized_value)
        return normalized

    def _sanitize_field_spec(self, field_spec: Optional[Any], column_names: List[str]) -> Optional[Any]:
        if field_spec is None:
            return None
        if isinstance(field_spec, list):
            sanitized = [
                spec for spec in field_spec if self._field_exists_in_columns(spec, column_names)
            ]
            return sanitized if sanitized else None
        return field_spec if self._field_exists_in_columns(field_spec, column_names) else None

    def _extract_text_values(self, row: Dict[str, Any], field_spec: Optional[Any]) -> List[str]:
        if field_spec is None:
            return []
        if isinstance(field_spec, list):
            pieces: List[str] = []
            for spec in field_spec:
                values = self._extract_field_values(row, spec)
                if values:
                    pieces.extend(values)
            combined = "\n".join(v for v in pieces if v)
            return [combined] if combined else []
        return self._extract_field_values(row, field_spec)

    async def invoke_data_mapping(
        self, 
        column_names: List[str], 
        sample_record: Dict[str, Any], 
        dataset: Any = None,
        user_target: str = "",
        category: str = "PT"
    ) -> Dict[str, Any]:
        """Invoke LLM for data mapping"""
        logger.info("Building data mapping prompt messages...")
        
        # Use prompt loader if available
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", f"data_conversion_{category.lower()}_prompt")
                task_prompt = self.prompt_loader("task", f"data_conversion_{category.lower()}_prompt")
                
                if dataset is not None:
                    sampled_records = await self._sample_records(dataset)
                    sample_rows_str = json.dumps(sampled_records, indent=2, ensure_ascii=False)
                    task_params = {
                        'column_names': str(column_names),
                        'sample_rows': sample_rows_str,
                        'user_target': user_target
                    }
                else:
                    truncated_record = {k: self._truncate_value(v) for k, v in sample_record.items()}
                    sample_rows_str = json.dumps([truncated_record], indent=2, ensure_ascii=False)
                    task_params = {
                        'column_names': str(column_names),
                        'sample_rows': sample_rows_str,
                        'user_target': user_target
                    }
                
                human_prompt = task_prompt.format(**task_params)
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_system_prompt(category)
                human_prompt = self._get_default_task_prompt(column_names, sample_record, user_target, category, dataset)
        else:
            system_prompt = self._get_default_system_prompt(category)
            human_prompt = self._get_default_task_prompt(column_names, sample_record, user_target, category, dataset)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            answer_msg = await self.llm.ainvoke(messages)
            answer_text = answer_msg.content.strip()
            logger.info(f'LLM data mapping response: {answer_text}')
        except Exception as e:
            error_msg = str(e)
            # Check for authentication errors
            if "401" in error_msg or "AuthenticationError" in str(type(e).__name__) or "无效的令牌" in error_msg or "invalid" in error_msg.lower() and "token" in error_msg.lower():
                logger.error(f"API Authentication Error during data mapping: {error_msg}")
                logger.error("Please check your API key configuration")
                raise ValueError(f"API Authentication Error: Invalid API key. Please check your API key configuration. Original error: {error_msg}")
            raise

            pattern = r'```json([\s\S]*?)```'
            match = re.search(pattern, answer_text)
            if match:
                match_text = match.group(1).strip()
            else:
                match_text = answer_text

            annotation_result = json.loads(match_text)
            logger.info(f"Data mapping result: {annotation_result}")
            return annotation_result
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {e}")
            raise ValueError(f"Failed to parse LLM response as JSON: {e}")
    
    def _apply_mapping_spec(
        self,
        record: Dict[str, Any],
        mapping_spec: Dict[str, Any],
        dataset_type: str,
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        user_target: Optional[str] = None
    ) -> Optional[UnifiedDataFormat]:
        """
        根据映射规则将记录转换为统一中间格式
        
        Args:
            record: 原始数据记录
            mapping_spec: LLM返回的映射规则
            dataset_type: 数据集类型
            file_path: 文件路径
            line_number: 行号
            user_target: 用户目标
        
        Returns:
            UnifiedDataFormat对象
        """
        try:
            # 生成ID
            record_id = generate_id(file_path, line_number, json.dumps(record, ensure_ascii=False))
            
            # 提取meta信息
            meta_spec = mapping_spec.get('meta', {})
            meta = extract_meta_from_context(
                file_path=file_path,
                original_data=record,
                user_query=user_target,
                source_hint=meta_spec.get('source') if isinstance(meta_spec.get('source'), str) and meta_spec.get('source') != 'auto_from_filename' else None,
                language_hint=meta_spec.get('language') if isinstance(meta_spec.get('language'), str) and meta_spec.get('language') != 'auto_detect' else None,
                original_id=record.get(meta_spec.get('original_id')) if meta_spec.get('original_id') else (record.get("id") or record.get("_id"))
            )
            
            if dataset_type == DatasetType.PRETRAIN.value:
                # PT模式：根据映射规则提取text
                text_spec = mapping_spec.get('text', {})
                text_fields = text_spec.get('fields', [])
                
                if not text_fields:
                    logger.debug(f"No text fields in mapping spec")
                    return None
                
                # 提取字段值
                all_text_values = []
                for field in text_fields:
                    values = self._extract_text_values(record, field)
                    all_text_values.extend(values)
                
                if not all_text_values:
                    logger.debug(f"No text values extracted")
                    return None
                
                # 根据merge_strategy合并
                merge_strategy = text_spec.get('merge_strategy', 'join_with_newline')
                if 'format' in merge_strategy and '{role}' in merge_strategy and '{content}' in merge_strategy:
                    # 对话格式：role: content
                    text_parts = []
                    for i in range(0, len(all_text_values), 2):
                        if i + 1 < len(all_text_values):
                            role = all_text_values[i]
                            content = all_text_values[i + 1]
                            text_parts.append(f"{role}: {content}")
                        else:
                            text_parts.append(all_text_values[i])
                    text = "\n".join(text_parts)
                else:
                    # 默认：用换行符连接
                    text = "\n".join(v.strip() for v in all_text_values if v.strip()).strip()
                
                if not text:
                    logger.debug(f"Empty text after extraction")
                    return None
                
                # 提取system
                system = None
                system_field = mapping_spec.get('system')
                if system_field and system_field in record:
                    system = str(record[system_field]).strip()
                
                return UnifiedDataFormat(
                    id=record_id,
                    dataset_type=dataset_type,
                    text=text,
                    system=system if system else None,
                    meta=meta.to_dict()
                )
            
            elif dataset_type == DatasetType.SFT.value:
                # SFT模式：根据映射规则构建messages
                messages_spec = mapping_spec.get('messages', {})
                construction_rule = messages_spec.get('construction_rule', 'simple_qa')
                
                messages = []
                
                if construction_rule == 'already_messages':
                    # 数据已经是messages格式
                    messages_field = messages_spec.get('conversation_field')
                    if messages_field and messages_field in record:
                        raw_messages = record[messages_field]
                        if isinstance(raw_messages, list):
                            for msg in raw_messages:
                                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                                    messages.append({
                                        "role": msg['role'],
                                        "content": msg['content'],
                                        "loss_mask": msg.get('loss_mask', msg['role'] == MessageRole.ASSISTANT.value)
                                    })
                
                elif construction_rule == 'simple_qa':
                    # 简单QA对
                    question_field = messages_spec.get('question_field')
                    answer_field = messages_spec.get('answer_field')
                    
                    if question_field:
                        questions = self._extract_text_values(record, question_field)
                    else:
                        questions = []
                    
                    if answer_field:
                        answers = self._extract_text_values(record, answer_field)
                    else:
                        answers = []
                    
                    max_pairs = max(len(questions), len(answers), 1)
                    for idx in range(max_pairs):
                        question = questions[idx] if idx < len(questions) else None
                        answer = answers[idx] if idx < len(answers) else None
                        
                        if question:
                            messages.append({
                                "role": MessageRole.USER.value,
                                "content": question,
                                "loss_mask": False
                            })
                        
                        if answer:
                            messages.append({
                                "role": MessageRole.ASSISTANT.value,
                                "content": answer,
                                "loss_mask": True
                            })
                
                elif construction_rule == 'multi_turn':
                    # 多轮对话
                    conversation_field = messages_spec.get('conversation_field')
                    if conversation_field and conversation_field in record:
                        turns = record[conversation_field]
                        if isinstance(turns, list):
                            for turn in turns:
                                if isinstance(turn, dict):
                                    role = turn.get('role', MessageRole.USER.value)
                                    content = turn.get('content', '')
                                    if content:
                                        messages.append({
                                            "role": role,
                                            "content": content,
                                            "loss_mask": role == MessageRole.ASSISTANT.value
                                        })
                
                if not messages:
                    logger.debug(f"No valid messages constructed")
                    return None
                
                # 提取system
                system = None
                system_field = mapping_spec.get('system')
                if system_field and system_field in record:
                    system = str(record[system_field]).strip()
                
                return UnifiedDataFormat(
                    id=record_id,
                    dataset_type=dataset_type,
                    messages=messages,
                    system=system if system else None,
                    meta=meta.to_dict()
                )
            
            else:
                logger.error(f"Unsupported dataset_type: {dataset_type}")
                return None
        
        except Exception as e:
            logger.error(f"Error applying mapping spec: {e}", exc_info=True)
            return None
    
    async def invoke_record_conversion(
        self,
        record: Dict[str, Any],
        category: str,
        user_target: str = "",
        file_path: Optional[str] = None,
        line_number: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        调用LLM直接将单条记录转换为统一中间格式
        
        Args:
            record: 原始数据记录（字典）
            category: 数据类别（PT或SFT）
            user_target: 用户目标
            file_path: 文件路径（用于上下文）
            line_number: 行号（用于上下文）
        
        Returns:
            统一中间格式的字典，如果转换失败则返回None
        """
        logger.debug(f"Converting record {line_number} to unified format using LLM")
        
        # 使用prompt loader
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", f"data_conversion_{category.lower()}_prompt")
                task_prompt = self.prompt_loader("task", f"data_conversion_{category.lower()}_prompt")
                
                # 格式化记录为JSON字符串
                record_str = json.dumps(record, indent=2, ensure_ascii=False)
                
                task_params = {
                    'column_names': str(list(record.keys())),
                    'sample_rows': record_str,  # 单条记录
                    'user_target': user_target
                }
                
                human_prompt = task_prompt.format(**task_params)
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_conversion_system_prompt(category)
                human_prompt = self._get_default_conversion_task_prompt(record, user_target, category)
        else:
            system_prompt = self._get_default_conversion_system_prompt(category)
            human_prompt = self._get_default_conversion_task_prompt(record, user_target, category)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            answer_msg = await self.llm.ainvoke(messages)
            answer_text = answer_msg.content.strip()
            logger.debug(f'LLM conversion response: {answer_text[:200]}...')
            
            # 提取JSON
            pattern = r'```json([\s\S]*?)```'
            match = re.search(pattern, answer_text)
            if match:
                match_text = match.group(1).strip()
            else:
                match_text = answer_text
            
            unified_record = json.loads(match_text)
            
            # 验证并补充字段
            # 确保id存在，如果LLM没有生成，我们生成一个
            if 'id' not in unified_record or not unified_record['id']:
                unified_record['id'] = generate_id(file_path, line_number, json.dumps(record, ensure_ascii=False))
            
            # 确保dataset_type正确
            if category.upper() == 'PT':
                unified_record['dataset_type'] = DatasetType.PRETRAIN.value
            elif category.upper() == 'SFT':
                unified_record['dataset_type'] = DatasetType.SFT.value
            
            # 补充meta信息（如果LLM没有生成完整的meta）
            if 'meta' not in unified_record:
                unified_record['meta'] = {}
            
            meta = extract_meta_from_context(
                file_path=file_path,
                original_data=record,
                user_query=user_target,
                source_hint=unified_record['meta'].get('source'),
                language_hint=unified_record['meta'].get('language'),
                original_id=record.get("id") or record.get("_id")
            )
            
            # 合并meta信息（LLM生成的优先，但补充缺失的字段）
            unified_record['meta'].update({
                'source': unified_record['meta'].get('source') or meta.source,
                'language': unified_record['meta'].get('language') or meta.language,
                'timestamp': unified_record['meta'].get('timestamp') or meta.timestamp,
                'token_count': unified_record['meta'].get('token_count') or meta.token_count,
                'quality_score': unified_record['meta'].get('quality_score') or meta.quality_score,
                'original_id': unified_record['meta'].get('original_id') or meta.original_id
            })
            
            # 移除None值
            unified_record['meta'] = {k: v for k, v in unified_record['meta'].items() if v is not None}
            
            logger.debug(f"Successfully converted record to unified format")
            return unified_record
        except Exception as e:
            logger.error(f"Failed to convert record using LLM: {e}", exc_info=True)
            return None
    
    def _get_default_conversion_system_prompt(self, category: str) -> str:
        """获取默认的转换系统提示词"""
        if category.upper() == "PT":
            return """You are a data conversion expert for Pre-training (PT) tasks. Your task is to convert raw data records into the unified intermediate format for LLM training.

You must output data in the following exact format:
{
  "id": "unique_id_here",
  "dataset_type": "pretrain",
  "text": "continuous text content for pre-training",
  "system": "optional system prompt",
  "meta": {
    "source": "data source identifier",
    "language": "language code (e.g., zh, en)",
    "original_id": "original record ID if exists"
  }
}

Extract and merge ALL relevant text from the record into a single coherent text field. Preserve all important details, context, and structure."""
        else:  # SFT
            return """You are a data conversion expert for Supervised Fine-Tuning (SFT) tasks. Your task is to convert raw data records into the unified intermediate format for LLM training.

You must output data in the following exact format:
{
  "id": "unique_id_here",
  "dataset_type": "sft",
  "messages": [
    {"role": "user", "content": "user question/instruction", "loss_mask": false},
    {"role": "assistant", "content": "assistant response", "loss_mask": true}
  ],
  "system": "optional system prompt",
  "meta": {
    "source": "data source identifier",
    "language": "language code (e.g., zh, en)",
    "original_id": "original record ID if exists"
  }
}

Extract question/instruction and answer/response from the record and build messages list. Preserve all important details, context, and formatting."""
    
    def _get_default_conversion_task_prompt(
        self,
        record: Dict[str, Any],
        user_target: str,
        category: str
    ) -> str:
        """获取默认的转换任务提示词"""
        record_str = json.dumps(record, indent=2, ensure_ascii=False)
        
        if category.upper() == "PT":
            return f"""**Sample Record to Convert**:
```json
{record_str}
```

**User Target**: {user_target}

Convert this record into unified intermediate format for pre-training. Extract and merge all relevant text into a single coherent text field. Return the complete unified format JSON."""
        else:  # SFT
            return f"""**Sample Record to Convert**:
```json
{record_str}
```

**User Target**: {user_target}

Convert this record into unified intermediate format for supervised fine-tuning. Extract question/instruction and answer/response, and build messages list. Return the complete unified format JSON."""

    def _get_default_system_prompt(self, category: str) -> str:
        """Get default system prompt"""
        if category.upper() == "PT":
            return """You are a data mapping expert for Pre-training (PT) tasks. 
Your task is to analyze the dataset structure and identify which field(s) contain text content that should be extracted for pre-training.

IMPORTANT: For pre-training, the output must be continuous, coherent text that can be directly used for language model training. This means:
1. If the data contains structured fields (like "role" and "content" for conversations), you should identify ALL relevant fields that need to be combined into a single continuous text.
2. The output should be a complete, readable text passage, not fragmented pieces.
3. For conversation data, identify fields that can be merged into a natural dialogue format.
4. For document data, identify the main content field(s) that contain the full text.

Return a JSON object with:
- "text": A list of field names or field specifications that should be combined into continuous text. For example: ["role", "content"] for conversation data, or ["content"] for single-field text data.

If multiple fields need to be combined, return them as a list. The system will automatically merge them into continuous text with appropriate formatting."""
        else:  # SFT
            return """You are a data mapping expert for Supervised Fine-Tuning (SFT) tasks.
Your task is to analyze the dataset structure and identify which fields contain question-answer pairs.

Return a JSON object with:
- "question": The field name or field specification that contains the question
- "answer": The field name or field specification that contains the answer

The field specifications should match column names in the dataset."""

    def _get_default_task_prompt(
        self, 
        column_names: List[str], 
        sample_record: Dict[str, Any], 
        user_target: str,
        category: str,
        dataset: Any = None
    ) -> str:
        """Get default task prompt"""
        if dataset is not None:
            # Note: This is called from sync context, but we'll handle it in the async method
            # For now, we'll keep it sync in the default prompt method
            import random
            dataset_size = len(dataset)
            if dataset_size == 0:
                sample_rows_str = "[]"
            else:
                num_samples = min(self.num_sample_records, dataset_size)
                if dataset_size <= num_samples:
                    sample_indices = list(range(dataset_size))
                else:
                    sample_indices = random.sample(range(dataset_size), num_samples)
                sampled_records = []
                for idx in sample_indices:
                    record = dataset[idx]
                    truncated_record = {k: self._truncate_value(v) for k, v in record.items()}
                    sampled_records.append(truncated_record)
                sample_rows_str = json.dumps(sampled_records, indent=2, ensure_ascii=False)
        else:
            truncated_record = {k: self._truncate_value(v) for k, v in sample_record.items()}
            sample_rows_str = json.dumps([truncated_record], indent=2, ensure_ascii=False)
        
        if category.upper() == "PT":
            return f"""Column names: {column_names}

Sample records:
{sample_rows_str}

User target: {user_target}

IMPORTANT: For pre-training, you need to identify field(s) that contain continuous, coherent text suitable for language model training.

If the data is structured (e.g., conversations with "role" and "content" fields, or documents with multiple text fields), identify ALL fields that should be combined into a single continuous text passage.

Return a JSON object with:
- "text": A list of field names that should be merged into continuous text. For example: ["role", "content"] for conversation data, or ["content"] for single-field text. The system will automatically format and merge these fields into readable, continuous text suitable for pre-training."""
        else:  # SFT
            return f"""Column names: {column_names}

Sample records:
{sample_rows_str}

User target: {user_target}

Please identify which fields contain question and answer. Return a JSON object with "question" and "answer" fields."""

    async def invoke_file_discovery(self, file_list_str: str) -> List[str]:
        """Invoke LLM for file discovery"""
        logger.info("Calling LLM for file discovery...")
        
        if self.prompt_loader:
            try:
                system_prompt = self.prompt_loader("system", "file_discovery_prompt")
                task_prompt = self.prompt_loader("task", "file_discovery_prompt")
                human_prompt = task_prompt.format(file_list=file_list_str)
            except Exception as e:
                logger.warning(f"Failed to load prompt, using default: {e}")
                system_prompt = self._get_default_file_discovery_system_prompt()
                human_prompt = self._get_default_file_discovery_task_prompt(file_list_str)
        else:
            system_prompt = self._get_default_file_discovery_system_prompt()
            human_prompt = self._get_default_file_discovery_task_prompt(file_list_str)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        try:
            answer_msg = await self.llm.ainvoke(messages)
            answer_text = answer_msg.content.strip()
            logger.info(f'LLM file discovery response: {answer_text}')

            pattern = r'```json([\s\S]*?)```'
            match = re.search(pattern, answer_text)
            if match:
                match_text = match.group(1).strip()
            else:
                match_text = answer_text

            result = json.loads(match_text)
            if isinstance(result, list) and all(isinstance(item, str) for item in result):
                return result
            else:
                logger.error(f"LLM did not return a JSON list of strings: {result}")
                raise ValueError("LLM did not return a JSON list of strings.")
        except Exception as e:
            logger.error(f"Failed to parse file discovery response: {e}")
            raise

    def _get_default_file_discovery_system_prompt(self) -> str:
        """Get default file discovery system prompt"""
        return """You are a file discovery expert. Your task is to identify which files in the provided file list are data files that should be processed.

Data files typically have extensions like: .json, .jsonl, .csv, .parquet, .arrow, .txt, .tsv, etc.
Exclude output files, summary files, cache files, and other non-data files.

Return a JSON array of file paths (relative paths from the root directory)."""

    def _get_default_file_discovery_task_prompt(self, file_list_str: str) -> str:
        """Get default file discovery task prompt"""
        return f"""File list:
{file_list_str}

Please identify which files are data files that should be processed. Return a JSON array of file paths."""

    # File processing methods (compressed files, loading, etc.)
    def _is_compressed_file(self, file_path: str) -> bool:
        """Check if file is compressed"""
        compressed_extensions = [
            '.zip', '.tar', '.tar.gz', '.tgz', 
            '.tar.bz2', '.tbz2', '.tar.xz', '.txz',
            '.gz', '.bz2', '.xz', '.7z', '.rar'
        ]
        path_lower = file_path.lower()
        return any(path_lower.endswith(ext) for ext in compressed_extensions)

    def _extract_compressed_file(self, compressed_path: str) -> Optional[str]:
        """Extract compressed file to temporary directory"""
        if not os.path.exists(compressed_path):
            logger.error(f"Compressed file does not exist: {compressed_path}")
            return None
            
        temp_base_dir = os.getenv("DF_TEMP_DIR") or None
        if temp_base_dir is None:
            parent_dir = os.path.dirname(os.path.abspath(compressed_path))
            tmp_candidate = os.path.join(parent_dir, ".tmp")
            try:
                os.makedirs(tmp_candidate, exist_ok=True)
                temp_base_dir = tmp_candidate
            except Exception:
                temp_base_dir = None
        temp_dir = tempfile.mkdtemp(prefix="dataflow_extract_", dir=temp_base_dir)
        self._temp_dirs.append(temp_dir)
        logger.info(f"Extracting {compressed_path} to {temp_dir}")
        
        try:
            path_lower = compressed_path.lower()
            
            if path_lower.endswith('.zip'):
                with zipfile.ZipFile(compressed_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                logger.info("Successfully extracted ZIP file")
                return temp_dir
            
            elif '.tar' in path_lower or path_lower.endswith(('.tgz', '.tbz2', '.txz')):
                with tarfile.open(compressed_path, 'r:*') as tar_ref:
                    tar_ref.extractall(temp_dir)
                logger.info("Successfully extracted TAR file")
                return temp_dir
            
            elif path_lower.endswith('.gz') and '.tar' not in path_lower:
                output_file = os.path.join(temp_dir, Path(compressed_path).stem)
                with gzip.open(compressed_path, 'rb') as f_in:
                    with open(output_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                logger.info("Successfully extracted GZIP file")
                return temp_dir
            
            elif path_lower.endswith('.bz2') and '.tar' not in path_lower:
                output_file = os.path.join(temp_dir, Path(compressed_path).stem)
                with bz2.open(compressed_path, 'rb') as f_in:
                    with open(output_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                logger.info("Successfully extracted BZIP2 file")
                return temp_dir
            
            elif path_lower.endswith('.xz') and '.tar' not in path_lower:
                output_file = os.path.join(temp_dir, Path(compressed_path).stem)
                with lzma.open(compressed_path, 'rb') as f_in:
                    with open(output_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                logger.info("Successfully extracted XZ file")
                return temp_dir
            
            else:
                logger.warning(f"Unsupported compression format: {compressed_path}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to extract file {compressed_path}: {e}")
            return None

    def _cleanup_temp_dirs(self):
        """Clean up all temporary directories"""
        for temp_dir in self._temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {temp_dir}: {e}")
        self._temp_dirs.clear()

    @staticmethod
    def _flatten_column_name(column: Any) -> str:
        if isinstance(column, tuple):
            return "__".join(str(part) for part in column if part not in (None, ""))
        return str(column)

    def _normalize_value(self, value: Any) -> Any:
        """Normalize value for pandas compatibility"""
        if pd is None or np is None:
            return value
            
        if value is None:
            return None

        if isinstance(value, dict):
            return {str(k): self._normalize_value(v) for k, v in value.items()}

        if isinstance(value, (list, tuple, set)):
            return [self._normalize_value(v) for v in list(value)]

        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, pd.Timestamp):
            return value.isoformat()

        if isinstance(value, pd.Timedelta):
            return value.isoformat()

        if isinstance(value, np.ndarray):
            return [self._normalize_value(v) for v in value.tolist()]

        if isinstance(value, (np.integer,)):
            return int(value)

        if isinstance(value, (np.floating,)):
            if np.isfinite(value):
                return float(value)
            return None

        if isinstance(value, (np.bool_,)):
            return bool(value)

        if hasattr(value, "item") and callable(getattr(value, "item")):
            try:
                return self._normalize_value(value.item())
            except Exception:
                pass

        return value

    def _dataframe_to_simple_dataset(self, df: Any) -> Optional[Dict[str, SimpleDataset]]:
        """Convert pandas DataFrame to SimpleDataset"""
        if pd is None:
            return None
            
        if df is None or len(df) == 0:
            logger.warning("pandas DataFrame is empty, cannot build SimpleDataset")
            return None

        df = df.copy()
        df.columns = [self._flatten_column_name(col) for col in df.columns]

        records = df.to_dict(orient="records")
        normalized_records: List[Dict[str, Any]] = []
        for record in records:
            normalized_record = {str(k): self._normalize_value(v) for k, v in record.items()}
            normalized_records.append(normalized_record)

        dataset = _build_simple_dataset(normalized_records)
        if dataset:
            sample_columns = dataset["train"].column_names[:5]
            logger.info(
                "Successfully built SimpleDataset from pandas, %d records, sample columns: %s",
                len(dataset["train"]),
                sample_columns,
            )
        return dataset

    def _get_file_list_string(self, root_path: str, exclude_files: List[str] = None) -> str:
        """Generate file list string from directory"""
        if exclude_files is None:
            exclude_files = []
        
        file_list = []
        for root, dirs, files in os.walk(root_path, topdown=True):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(('.', '__'))
                and d not in ('.cache', 'processed_output', '.tmp', 'rag_db', 'web_get')
                and not d.startswith(('datasets_cache_', 'dataflow_extract_', 'hf_cache_', 'kaggle_cache_'))
            ]
            files = [f for f in files if not f.startswith(('.', '__'))]
            
            for f in files:
                if f in exclude_files:
                    continue
                if f.endswith('.conda'):
                    continue
                full_path = os.path.join(root, f)
                relative_path = os.path.relpath(full_path, root_path)
                file_list.append(relative_path.replace(os.sep, '/'))
        
        if not file_list:
            return "This directory is empty."
        
        return "File list:\n" + "\n".join(sorted(file_list))

    def _chunk_file_list_for_llm(
        self,
        file_list_str: str,
        max_chars: int = 8000,
        max_lines: int = 200,
    ) -> List[str]:
        """Split file list string into chunks for LLM"""
        if not file_list_str:
            return []

        lines = file_list_str.splitlines()
        if not lines:
            return []

        header = None
        if lines[0].strip().lower().startswith("file list"):
            header = lines[0]
            content_lines = lines[1:]
        else:
            content_lines = lines

        if not content_lines:
            return [file_list_str]

        chunks: List[List[str]] = []
        current_chunk: List[str] = []
        current_char_len = 0

        for line in content_lines:
            line_len = len(line) + 1
            if current_chunk and (
                current_char_len + line_len > max_chars
                or len(current_chunk) >= max_lines
            ):
                chunks.append(current_chunk)
                current_chunk = [line]
                current_char_len = line_len
            else:
                current_chunk.append(line)
                current_char_len += line_len

        if current_chunk:
            chunks.append(current_chunk)

        if len(chunks) <= 1:
            return [file_list_str]

        total_chunks = len(chunks)
        result_chunks: List[str] = []
        for idx, chunk_lines in enumerate(chunks, start=1):
            if header:
                chunk_header = f"{header} (chunk {idx}/{total_chunks})"
            else:
                chunk_header = f"File list (chunk {idx}/{total_chunks})"
            chunk_str = chunk_header + "\n" + "\n".join(chunk_lines)
            result_chunks.append(chunk_str)

        return result_chunks

    def _get_builder_type(self, file_path: str) -> Optional[str]:
        """Guess builder type for load_dataset"""
        path_lower = file_path.lower()
        if '.jsonl' in path_lower or '.json' in path_lower:
            return 'json'
        if '.csv' in path_lower:
            return 'csv'
        if '.parquet' in path_lower:
            return 'parquet'
        if '.arrow' in path_lower:
            return 'arrow'
        if '.txt' in path_lower or '.md' in path_lower:
            return 'text'
        
        logger.warning(f"Cannot determine builder type for file '{file_path}'")
        return None

    async def _manual_load_json(self, file_path: str, max_file_size_mb: int = 5000) -> Optional[Any]:
        """Manually load JSON file"""
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                logger.warning(f"File too large ({file_size_mb:.2f} MB > {max_file_size_mb} MB), skipping: {file_path}")
                return None
            
            logger.info(f"Attempting to manually read JSON file ({file_size_mb:.2f} MB): {file_path}")

            if pd is None:
                logger.error("pandas is not available for JSON loading")
                return None

            try:
                df = pd.read_json(file_path, lines=True)
                if len(df) > 0:
                    logger.info(f"pandas line-by-line JSON read successful, got {len(df)} records")
                    return self._dataframe_to_simple_dataset(df)
            except ValueError as err:
                logger.info(f"pandas line-by-line JSON read failed: {err}")

            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            if not content:
                logger.warning(f"File is empty: {file_path}")
                return None

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing failed: {e}")
                return None

            candidate_records: List[Any] = []
            if isinstance(parsed, list):
                candidate_records = parsed
            elif isinstance(parsed, dict):
                key_candidates = ['data', 'items', 'records', 'examples', 'train', 'test', 'validation', 'val']
                for key in key_candidates:
                    if key in parsed and isinstance(parsed[key], list) and parsed[key]:
                        candidate_records = parsed[key]
                        logger.info(f"Found {len(parsed[key])} records in JSON dict key '{key}'")
                        break
                if not candidate_records:
                    candidate_records = [parsed]
            else:
                logger.warning(f"Unsupported JSON top-level type: {type(parsed)}, skipping {file_path}")
                return None

            if not candidate_records:
                logger.warning(f"JSON content does not contain usable records: {file_path}")
                return None

            normalized_records: List[Dict[str, Any]] = []
            for item in candidate_records:
                if isinstance(item, dict):
                    normalized_records.append(item)
                else:
                    normalized_records.append({"value": item})

            df = pd.json_normalize(normalized_records)
            if len(df) == 0:
                logger.warning(f"pd.json_normalize result is empty: {file_path}")
                return None

            return self._dataframe_to_simple_dataset(df)

        except Exception as e:
            logger.error(f"Error manually loading JSON file: {e}")
            return None

    async def _manual_load_parquet(self, file_path: str, max_file_size_mb: int = 5000) -> Optional[Any]:
        """Manually load Parquet file"""
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                logger.warning(f"Parquet file too large ({file_size_mb:.2f} MB > {max_file_size_mb} MB), skipping: {file_path}")
                return None
            
            logger.info(f"Attempting to manually read Parquet file ({file_size_mb:.2f} MB): {file_path}")
            
            if pd is None:
                logger.error("pandas is not available for Parquet loading")
                return None

            strategies = [
                {"name": "pandas+pyarrow", "func": lambda p: pd.read_parquet(p, engine='pyarrow')},
                {"name": "pandas default", "func": lambda p: pd.read_parquet(p)},
            ]
            
            df = None
            for strategy in strategies:
                try:
                    logger.info(f"Trying Parquet read strategy: {strategy['name']}")
                    df = strategy['func'](file_path)
                    if df is not None and len(df) > 0:
                        logger.info(f"Parquet read strategy '{strategy['name']}' succeeded!")
                        break
                except Exception as e:
                    logger.warning(f"Parquet read strategy '{strategy['name']}' failed: {e}")
                    continue
            
            if df is None:
                logger.error(f"All Parquet read strategies failed: {file_path}")
                return None
            
            if len(df) == 0:
                logger.warning(f"Parquet file is empty: {file_path}")
                return None

            return self._dataframe_to_simple_dataset(df)
                
        except Exception as e:
            logger.error(f"Error manually loading Parquet file: {e}")
            return None

    async def _manual_load_generic(self, file_path: str, max_file_size_mb: int = 5000) -> Optional[Any]:
        """Generic file loading method"""
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > max_file_size_mb:
                logger.warning(f"File too large ({file_size_mb:.2f} MB > {max_file_size_mb} MB), skipping: {file_path}")
                return None
            
            logger.info(f"Attempting generic method to read file ({file_size_mb:.2f} MB): {file_path}")
            
            file_ext = os.path.splitext(file_path)[1].lower()
            
            if pd is None:
                logger.error("pandas is not available for generic loading")
                return None
            
            if file_ext == '.csv':
                df = pd.read_csv(file_path)
                return self._dataframe_to_simple_dataset(df)
            
            elif file_ext in ['.txt', '.md']:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                records = [{"text": content}]
                dataset = _build_simple_dataset(records)
                if dataset:
                    logger.info("Successfully loaded content from text file")
                return dataset
            
            else:
                logger.warning(f"Unsupported file type: {file_ext}")
                return None
                
        except Exception as e:
            logger.error(f"Error in generic file loading method: {e}")
            return None

    async def _load_with_datasets(self, builder_type: str, file_path: str) -> Optional[Any]:
        """Load file using datasets library"""
        if not TYPE_CHECKING:
            logger.warning("datasets library not available")
            return None
            
        try:
            temp_base_dir = os.getenv("DF_TEMP_DIR") or None
            if temp_base_dir is None:
                parent_dir = os.path.dirname(os.path.abspath(file_path))
                tmp_candidate = os.path.join(parent_dir, ".tmp")
                try:
                    os.makedirs(tmp_candidate, exist_ok=True)
                    temp_base_dir = tmp_candidate
                except Exception:
                    temp_base_dir = None
            temp_cache_dir = tempfile.mkdtemp(prefix="datasets_cache_", dir=temp_base_dir)
            self._temp_dirs.append(temp_cache_dir)

            dl_config = DownloadConfig(cache_dir=temp_cache_dir)

            strategies = [
                {
                    "name": "temp cache + memory",
                    "params": {
                        "path": builder_type,
                        "data_files": file_path,
                        "cache_dir": temp_cache_dir,
                        "keep_in_memory": True,
                        "download_config": dl_config,
                    },
                },
            ]
            
            for strategy in strategies:
                try:
                    logger.info(f"Trying datasets strategy: {strategy['name']}")
                    data = load_dataset(**strategy['params'])
                    logger.info(f"datasets strategy '{strategy['name']}' succeeded!")
                    return data
                except Exception as e:
                    logger.warning(f"datasets strategy '{strategy['name']}' failed: {e}")
                    continue
            
            return None
        except Exception as e:
            logger.error(f"Error in datasets loading method: {e}")
            return None

    async def _load_with_fallback(self, builder_type: str, file_path: str) -> Optional[Any]:
        """Load file using fallback methods"""
        try:
            if builder_type == 'parquet':
                return await self._manual_load_parquet(file_path)
            if builder_type == 'json':
                return await self._manual_load_json(file_path)
            if builder_type == 'csv':
                return await self._manual_load_generic(file_path)
            return await self._manual_load_generic(file_path)
        except Exception as e:
            logger.error(f"Error in fallback loading method: {e}")
            return None

    async def _process_dataset(
        self, 
        data: Any, 
        file_path: str, 
        user_target: str,
        category: str,
        output_jsonl_prefix: str,
        processed_sources_list: List[Tuple[str, int]]
    ) -> int:
        """Process single dataset and extract data to unified format"""
        total_count = 0
        file_name = os.path.basename(file_path)
        
        # 确定dataset_type
        if category.upper() == 'PT':
            dataset_type = DatasetType.PRETRAIN.value
        elif category.upper() == 'SFT':
            dataset_type = DatasetType.SFT.value
        else:
            logger.warning(f"Unknown category '{category}', defaulting to pretrain")
            dataset_type = DatasetType.PRETRAIN.value
        
        for split_name, data_content in data.items():
            logger.info(f"--- Processing Split: '{split_name}' (from {file_name}) ---")
            
            if len(data_content) == 0:
                logger.info(f"Split '{split_name}' is empty, skipping.")
                continue
                
            column_names = data_content.column_names
            sample_record = data_content[0]
            
            # 步骤1: 采样并调用LLM获取映射规则
            try:
                mapping_spec = await self.invoke_data_mapping(
                    column_names=column_names,
                    sample_record=sample_record,
                    dataset=data_content,
                    user_target=user_target,
                    category=category
                )
                logger.info(f"LLM mapping specification: {mapping_spec}")
            except Exception as e:
                logger.error(f"LLM mapping failed, skipping Split '{split_name}': {e}")
                continue
            
            # 步骤2: 根据映射规则批量处理所有记录
            split_record_count = 0
            chunk_size = 10000
            current_chunk_index = 1
            current_chunk_count = 0
            failed_count = 0

            def _open_chunk_file(index: int):
                chunk_path = f"{output_jsonl_prefix}_{index:05d}.jsonl"
                return open(chunk_path, 'a', encoding='utf-8')

            f_out = _open_chunk_file(current_chunk_index)
            try:
                # 处理每一行数据，根据映射规则转换为统一格式
                for line_idx, row in enumerate(data_content):
                    # 将row转换为字典格式（如果还不是）
                    if not isinstance(row, dict):
                        row_dict = dict(row) if hasattr(row, '__iter__') and not isinstance(row, str) else {"value": row}
                    else:
                        row_dict = row
                    
                    try:
                        # 根据映射规则转换记录
                        unified_record = self._apply_mapping_spec(
                            record=row_dict,
                            mapping_spec=mapping_spec,
                            dataset_type=dataset_type,
                            file_path=file_path,
                            line_number=line_idx,
                            user_target=user_target
                        )
                        
                        if unified_record is None:
                            failed_count += 1
                            logger.debug(f"Conversion failed for row {line_idx}, skipping")
                            continue
                        
                        # 验证格式
                        is_valid, error_msg = validate_unified_format(unified_record.to_dict())
                        if not is_valid:
                            failed_count += 1
                            logger.warning(f"Invalid unified format for row {line_idx}: {error_msg}")
                            continue
                        
                        # 写入JSONL文件
                        json.dump(unified_record.to_dict(), f_out, ensure_ascii=False)
                        f_out.write('\n')
                        split_record_count += 1
                        current_chunk_count += 1
                        
                        if current_chunk_count >= chunk_size:
                            f_out.close()
                            current_chunk_index += 1
                            current_chunk_count = 0
                            f_out = _open_chunk_file(current_chunk_index)
                    
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"Error converting row {line_idx}: {e}", exc_info=True)
                        continue
            finally:
                try:
                    f_out.close()
                except Exception:
                    pass
            
            if split_record_count > 0:
                logger.info(f"Extracted {split_record_count} records from {file_name} ({split_name}). Failed: {failed_count}")
                processed_sources_list.append((f"{file_name}_({split_name})", split_record_count))
                total_count += split_record_count
            elif failed_count > 0:
                logger.warning(f"All {failed_count} records failed conversion for {file_name} ({split_name})")
        
        return total_count
    
    async def _process_dataset_with_mapping(
        self,
        data_content: Any,
        file_path: str,
        file_name: str,
        split_name: str,
        mapping_spec: Dict[str, Any],
        category: str,
        output_jsonl_prefix: str,
        processed_sources_list: List[Tuple[str, int]]
    ) -> int:
        """Process a single split with pre-computed mapping specification to unified format"""
        logger.info(f"--- Processing Split: '{split_name}' (from {file_name}) ---")
        
        if len(data_content) == 0:
            logger.info(f"Split '{split_name}' is empty, skipping.")
            return 0
        
        # 确定dataset_type
        if category.upper() == 'PT':
            dataset_type = DatasetType.PRETRAIN.value
        elif category.upper() == 'SFT':
            dataset_type = DatasetType.SFT.value
        else:
            logger.warning(f"Unknown category '{category}', defaulting to pretrain")
            dataset_type = DatasetType.PRETRAIN.value
        
        split_record_count = 0
        chunk_size = 10000
        current_chunk_index = 1
        current_chunk_count = 0
        failed_count = 0

        def _open_chunk_file(index: int):
            chunk_path = f"{output_jsonl_prefix}_{index:05d}.jsonl"
            return open(chunk_path, 'a', encoding='utf-8')

        f_out = _open_chunk_file(current_chunk_index)
        try:
            # 处理每一行数据，根据映射规则转换为统一格式
            for line_idx, row in enumerate(data_content):
                # 将row转换为字典格式（如果还不是）
                if not isinstance(row, dict):
                    row_dict = dict(row) if hasattr(row, '__iter__') and not isinstance(row, str) else {"value": row}
                else:
                    row_dict = row
                
                try:
                    # 根据映射规则转换记录
                    unified_record = self._apply_mapping_spec(
                        record=row_dict,
                        mapping_spec=mapping_spec,
                        dataset_type=dataset_type,
                        file_path=file_path,
                        line_number=line_idx,
                        user_target=None
                    )
                    
                    if unified_record is None:
                        failed_count += 1
                        logger.debug(f"Conversion failed for row {line_idx}, skipping")
                        continue
                    
                    # 验证格式
                    is_valid, error_msg = validate_unified_format(unified_record.to_dict())
                    if not is_valid:
                        failed_count += 1
                        logger.warning(f"Invalid unified format for row {line_idx}: {error_msg}")
                        continue
                    
                    # 写入JSONL文件
                    json.dump(unified_record.to_dict(), f_out, ensure_ascii=False)
                    f_out.write('\n')
                    split_record_count += 1
                    current_chunk_count += 1
                    
                    if current_chunk_count >= chunk_size:
                        f_out.close()
                        current_chunk_index += 1
                        current_chunk_count = 0
                        f_out = _open_chunk_file(current_chunk_index)
                
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Error converting row {line_idx}: {e}", exc_info=True)
                    continue
        finally:
            try:
                f_out.close()
            except Exception:
                pass
        
        if split_record_count > 0:
            logger.info(f"Extracted {split_record_count} records from {file_name} ({split_name}). Failed: {failed_count}")
            processed_sources_list.append((f"{file_name}_({split_name})", split_record_count))
        
        return split_record_count

