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
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """Initialize Data Convertor
        
        Args:
            timeout: Timeout in seconds for LLM API calls (default: 120.0)
            max_retries: Maximum number of retries for failed LLM calls (default: 3)
        """
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_sample_length = max_sample_length
        self.num_sample_records = num_sample_records
        self.prompt_loader = prompt_loader
        self.timeout = timeout
        self.max_retries = max_retries
        
        self.llm = ChatOpenAI(
            model=model_name,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
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

    def _extract_meta_field(self, row: Dict[str, Any], field_spec: Optional[Any], column_names: List[str]) -> Optional[Any]:
        """Extract a single metadata field value from a row.
        
        Args:
            row: The data row
            field_spec: Field specification (can be a field path string, a direct string value, or null)
            column_names: List of column names for validation
            
        Returns:
            The extracted value, or None if not found
        """
        if field_spec is None:
            return None
        
        # If it's a direct string value (not a field path), return it as-is
        if isinstance(field_spec, str):
            # Check if it's a field path (contains dot or bracket) or a direct value
            if '.' in field_spec or '[' in field_spec:
                # It's a field path, extract it
                values = self._extract_field_values(row, field_spec)
                if values:
                    # Return the first value, or join if multiple
                    return values[0] if len(values) == 1 else " ".join(str(v) for v in values if v)
                return None
            else:
                # Check if it exists as a column name
                if field_spec in column_names:
                    # It's a field path, extract it
                    values = self._extract_field_values(row, field_spec)
                    if values:
                        return values[0] if len(values) == 1 else " ".join(str(v) for v in values if v)
                    return None
                else:
                    # It's a direct string value, return as-is
                    return field_spec
        
        # For other types, try to extract as field path
        if isinstance(field_spec, (int, float, bool)):
            return field_spec
        
        # Try to convert to string and extract
        field_spec_str = str(field_spec)
        values = self._extract_field_values(row, field_spec_str)
        if values:
            return values[0] if len(values) == 1 else " ".join(str(v) for v in values if v)
        return None

    def _build_meta_dict(
        self, 
        row: Dict[str, Any], 
        annotation_result: Dict[str, Any], 
        file_path: str,
        column_names: List[str]
    ) -> Dict[str, Any]:
        """Build metadata dictionary from annotation result and row data.
        
        Args:
            row: The data row
            annotation_result: LLM mapping result containing meta field specifications
            file_path: Source file path (used as fallback for source)
            column_names: List of column names for validation
            
        Returns:
            Metadata dictionary
        """
        meta_spec = annotation_result.get('meta', {}) if annotation_result else {}
        
        # Extract each metadata field
        source = self._extract_meta_field(row, meta_spec.get('source'), column_names)
        if source is None:
            # Fallback to file path
            source = os.path.basename(file_path) if file_path else None
        
        language = self._extract_meta_field(row, meta_spec.get('language'), column_names)
        timestamp = self._extract_meta_field(row, meta_spec.get('timestamp'), column_names)
        token_count = self._extract_meta_field(row, meta_spec.get('token_count'), column_names)
        quality_score = self._extract_meta_field(row, meta_spec.get('quality_score'), column_names)
        original_id = self._extract_meta_field(row, meta_spec.get('original_id'), column_names)
        
        meta = {
            "source": source,
            "language": language,
            "timestamp": timestamp,
            "token_count": token_count,
            "quality_score": quality_score,
            "original_id": original_id
        }
        
        # Remove None values to keep the output clean
        return {k: v for k, v in meta.items() if v is not None}

    def _generate_record_id(self, file_path: str, record_index: int) -> str:
        """Generate a unique record ID.
        
        Args:
            file_path: Source file path
            record_index: Index of the record in the dataset
            
        Returns:
            Unique record ID string
        """
        import hashlib
        import time
        
        # Create a hash from file path and record index
        base_str = f"{file_path}:{record_index}:{time.time()}"
        hash_obj = hashlib.md5(base_str.encode('utf-8'))
        return hash_obj.hexdigest()[:16]

    def _build_intermediate_format_pt(
        self,
        row: Dict[str, Any],
        annotation_result: Dict[str, Any],
        file_path: str,
        record_index: int,
        column_names: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Build intermediate format record for PT (Pre-training) mode.
        
        Args:
            row: The data row
            annotation_result: LLM mapping result
            file_path: Source file path
            record_index: Index of the record
            column_names: List of column names
            
        Returns:
            Intermediate format record dict, or None if invalid
        """
        if not annotation_result or annotation_result.get('text') is None:
            return None
        
        text_field = annotation_result.get('text')
        
        # Handle both single field and list of fields
        if isinstance(text_field, list):
            text_fields = [self._sanitize_field_spec(field, column_names) for field in text_field]
            text_fields = [f for f in text_fields if f is not None]
        else:
            text_field = self._sanitize_field_spec(text_field, column_names)
            text_fields = [text_field] if text_field else []
        
        if not text_fields:
            return None
        
        # Extract text values from all specified fields
        all_text_parts = []
        for field in text_fields:
            values = self._extract_text_values(row, field)
            all_text_parts.extend(values)
        
        if not all_text_parts:
            return None
        
        # Merge text parts into continuous text
        # For conversation-like data, try to merge naturally
        merged_text = " ".join(str(part).strip() for part in all_text_parts if str(part).strip())
        
        if not merged_text:
            return None
        
        # Build metadata
        meta = self._build_meta_dict(row, annotation_result, file_path, column_names)
        
        # Generate unique ID
        record_id = self._generate_record_id(file_path, record_index)
        
        return {
            "id": record_id,
            "dataset_type": "pretrain",
            "text": merged_text,
            "meta": meta
        }

    def _build_intermediate_format_sft(
        self,
        row: Dict[str, Any],
        annotation_result: Dict[str, Any],
        file_path: str,
        record_index: int,
        column_names: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Build intermediate format record for SFT (Supervised Fine-Tuning) mode.
        
        Args:
            row: The data row
            annotation_result: LLM mapping result
            file_path: Source file path
            record_index: Index of the record
            column_names: List of column names
            
        Returns:
            Intermediate format record dict, or None if invalid
        """
        if not annotation_result or not annotation_result.get('messages'):
            return None
        
        messages_spec = annotation_result.get('messages', [])
        if not isinstance(messages_spec, list) or len(messages_spec) == 0:
            return None
        
        # Build messages array
        messages = []
        for msg_spec in messages_spec:
            if not isinstance(msg_spec, dict):
                continue
            
            role = msg_spec.get('role')
            content_spec = msg_spec.get('content')
            loss_mask = msg_spec.get('loss_mask')
            
            if not role or not content_spec:
                continue
            
            # Extract content value(s)
            if isinstance(content_spec, list):
                # Multiple fields to concatenate
                content_parts = []
                for field in content_spec:
                    field_sanitized = self._sanitize_field_spec(field, column_names)
                    if field_sanitized:
                        values = self._extract_text_values(row, field_sanitized)
                        content_parts.extend(values)
                content = " ".join(str(part).strip() for part in content_parts if str(part).strip())
            else:
                # Single field
                field_sanitized = self._sanitize_field_spec(content_spec, column_names)
                if field_sanitized:
                    values = self._extract_text_values(row, field_sanitized)
                    content = " ".join(str(v).strip() for v in values if str(v).strip()) if values else None
                else:
                    content = None
            
            if not content:
                continue
            
            # Default loss_mask based on role
            if loss_mask is None:
                loss_mask = (role == "assistant")
            
            messages.append({
                "role": role,
                "content": content,
                "loss_mask": loss_mask
            })
        
        if not messages:
            return None
        
        # Extract system prompt if available
        system_spec = annotation_result.get('system')
        system = None
        if system_spec:
            system = self._extract_meta_field(row, system_spec, column_names)
        
        # Build metadata
        meta = self._build_meta_dict(row, annotation_result, file_path, column_names)
        
        # Generate unique ID
        record_id = self._generate_record_id(file_path, record_index)
        
        result = {
            "id": record_id,
            "dataset_type": "sft",
            "messages": messages,
            "meta": meta
        }
        
        if system:
            result["system"] = system
        
        return result

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
        if not self.prompt_loader:
            raise RuntimeError("prompt_loader is required for data mapping but is missing.")

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
            # 回退到默认提示，避免因模板缺失或格式错误导致崩溃
            logger.warning(f"Failed to load data conversion prompts, using default. Error: {e}")
            system_prompt = self._get_default_system_prompt(category)
            human_prompt = self._get_default_task_prompt(
                column_names=column_names,
                sample_record=sample_record,
                user_target=user_target,
                category=category,
                dataset=dataset
            )
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]
        
        # Retry logic with timeout protection
        last_exception = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Calling LLM for data mapping (attempt {attempt}/{self.max_retries})...")
                
                # Add timeout protection
                answer_msg = await asyncio.wait_for(
                    self.llm.ainvoke(messages),
                    timeout=self.timeout
                )
                answer_text = answer_msg.content.strip()
                logger.info(f'LLM data mapping response received (attempt {attempt}): {answer_text[:200]}...')

                pattern = r'```json([\s\S]*?)```'
                match = re.search(pattern, answer_text)
                if match:
                    match_text = match.group(1).strip()
                else:
                    match_text = answer_text

                annotation_result = json.loads(match_text)
                logger.info(f"Data mapping result parsed successfully: {annotation_result}")
                return annotation_result
                
            except asyncio.TimeoutError:
                last_exception = asyncio.TimeoutError(f"LLM call timed out after {self.timeout}s (attempt {attempt}/{self.max_retries})")
                logger.warning(f"LLM call timeout on attempt {attempt}/{self.max_retries}")
                if attempt < self.max_retries:
                    wait_time = min(2 ** attempt, 10)  # Exponential backoff, max 10s
                    logger.info(f"Retrying after {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"All {self.max_retries} attempts timed out")
                    
            except json.JSONDecodeError as e:
                last_exception = ValueError(f"Failed to parse LLM response as JSON: {e}")
                logger.error(f"JSON parsing failed on attempt {attempt}/{self.max_retries}: {e}")
                if attempt < self.max_retries:
                    wait_time = min(2 ** attempt, 10)
                    logger.info(f"Retrying after {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise last_exception
                    
            except Exception as e:
                last_exception = e
                logger.error(f"LLM call failed on attempt {attempt}/{self.max_retries}: {e}")
                if attempt < self.max_retries:
                    wait_time = min(2 ** attempt, 10)
                    logger.info(f"Retrying after {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ValueError(f"Failed to get LLM response after {self.max_retries} attempts: {e}")
        
        # If we get here, all retries failed
        raise ValueError(f"Failed to get valid LLM response after {self.max_retries} attempts: {last_exception}")

    def _get_default_system_prompt(self, category: str) -> str:
        """Get default system prompt"""
        if category.upper() == "PT":
            return """You are an expert in dataset classification and analysis.

Your task is to identify field mappings for language model pretraining.

You need to identify:
1. Text field(s): Which field(s) contain the main text content for pretraining
2. Metadata fields (optional): Source, language, original ID, etc.

The output format should be a JSON object with "text" field (string or array) and optional "meta" object."""
        else:  # SFT
            return """You are an expert in dataset classification and analysis.

Your task is to identify field mappings for supervised fine-tuning (SFT).

You need to identify:
1. Message fields: Which fields contain user questions/inputs and assistant responses
2. System prompt: Which field (if any) contains the system prompt
3. Metadata fields: Source, language, original ID, etc.

The output format should be a JSON object with "messages" array containing role and content field mappings."""

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

Please analyze the dataset and identify the field mappings for pre-training.

Return a JSON object with:
- "text": A single field name or a list of field names that should be merged into continuous text.
  For example: "content" for single-field text, or ["title", "body"] for multi-field text.
  
- "meta": (optional) Object with field mappings for metadata:
  - "source": Field name for data source
  - "language": Field name or direct value for language (e.g., "zh", "en")
  - "original_id": Field name for original record ID

Example response:
```json
{{
    "text": "content",
    "meta": {{"source": "url", "language": "zh"}}
}}
```

Or for multi-field data:
```json
{{
    "text": ["title", "abstract", "body"],
    "meta": {{"source": "dataset_name"}}
}}
```"""
        else:  # SFT
            return f"""Column names: {column_names}

Sample records:
{sample_rows_str}

User target: {user_target}

Please analyze the dataset and identify the field mappings for SFT (Supervised Fine-Tuning).

Return a JSON object with:
- "messages": An array of message specifications. Each message should have:
  - "role": The role of the speaker ("user", "assistant", or "system")
  - "content": The field name/path that contains the message content
  - "loss_mask": (optional) Whether to compute loss on this message (default: true for assistant, false for others)
  
- "system": (optional) Field name that contains the system prompt, or a direct string value
  
- "meta": (optional) Object with field mappings for metadata:
  - "source": Field name for data source
  - "language": Field name or direct value for language (e.g., "zh", "en")
  - "original_id": Field name for original record ID

Example response for a Q&A dataset:
```json
{{
    "messages": [
        {{"role": "user", "content": "question"}},
        {{"role": "assistant", "content": "answer"}}
    ],
    "meta": {{"source": "source_field"}}
}}
```

Example response for a conversation dataset:
```json
{{
    "messages": [
        {{"role": "user", "content": "conversations[0].content"}},
        {{"role": "assistant", "content": "conversations[1].content"}}
    ],
    "system": "system_prompt"
}}
```"""

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
        """Process single dataset and extract data"""
        total_count = 0
        file_name = os.path.basename(file_path)
        
        for split_name, data_content in data.items():
            logger.info(f"--- Processing Split: '{split_name}' (from {file_name}) ---")
            
            if len(data_content) == 0:
                logger.info(f"Split '{split_name}' is empty, skipping.")
                continue
                
            column_names = data_content.column_names
            sample_record = data_content[0]
            
            try:
                annotation_result = await self.invoke_data_mapping(
                    column_names=column_names,
                    sample_record=sample_record,
                    dataset=data_content,
                    user_target=user_target,
                    category=category
                )
                logger.info(f"LLM mapping result: {annotation_result}")
            except Exception as e:
                logger.error(f"LLM data mapping failed, skipping Split '{split_name}': {e}")
                continue
            
            split_record_count = 0
            chunk_size = 10000
            current_chunk_index = 1
            current_chunk_count = 0

            def _open_chunk_file(index: int):
                chunk_path = f"{output_jsonl_prefix}_{index:05d}.jsonl"
                return open(chunk_path, 'a', encoding='utf-8')

            f_out = _open_chunk_file(current_chunk_index)
            try:
                if category.upper() == 'PT':
                    # Use new intermediate format builder
                    for record_index, row in enumerate(data_content):
                        intermediate_record = self._build_intermediate_format_pt(
                            row, annotation_result, file_path, record_index, column_names
                        )
                        if intermediate_record:
                            json.dump(intermediate_record, f_out, ensure_ascii=False)
                            f_out.write('\n')
                            split_record_count += 1
                            current_chunk_count += 1
                            if current_chunk_count >= chunk_size:
                                f_out.close()
                                current_chunk_index += 1
                                current_chunk_count = 0
                                f_out = _open_chunk_file(current_chunk_index)
                            
                elif category.upper() == 'SFT':
                    # Use new intermediate format builder
                    for record_index, row in enumerate(data_content):
                        intermediate_record = self._build_intermediate_format_sft(
                            row, annotation_result, file_path, record_index, column_names
                        )
                        if intermediate_record:
                            json.dump(intermediate_record, f_out, ensure_ascii=False)
                            f_out.write('\n')
                            split_record_count += 1
                            current_chunk_count += 1
                            if current_chunk_count >= chunk_size:
                                f_out.close()
                                current_chunk_index += 1
                                current_chunk_count = 0
                                f_out = _open_chunk_file(current_chunk_index)
            finally:
                try:
                    f_out.close()
                except Exception:
                    pass
            
            if split_record_count > 0:
                logger.info(f"Extracted {split_record_count} records from {file_name} ({split_name}).")
                processed_sources_list.append((f"{file_name}_({split_name})", split_record_count))
                total_count += split_record_count
        
        return total_count
    
    async def _process_dataset_with_mapping(
        self,
        data_content: Any,
        file_path: str,
        file_name: str,
        split_name: str,
        annotation_result: Dict[str, Any],
        category: str,
        output_jsonl_prefix: str,
        processed_sources_list: List[Tuple[str, int]]
    ) -> int:
        """Process a single split with pre-computed mapping result"""
        logger.info(f"--- Processing Split: '{split_name}' (from {file_name}) ---")
        
        if len(data_content) == 0:
            logger.info(f"Split '{split_name}' is empty, skipping.")
            return 0
        
        column_names = data_content.column_names
        
        split_record_count = 0
        chunk_size = 10000
        current_chunk_index = 1
        current_chunk_count = 0

        def _open_chunk_file(index: int):
            chunk_path = f"{output_jsonl_prefix}_{index:05d}.jsonl"
            return open(chunk_path, 'a', encoding='utf-8')

        f_out = _open_chunk_file(current_chunk_index)
        try:
            if category.upper() == 'PT':
                # Use new intermediate format builder
                for record_index, row in enumerate(data_content):
                    intermediate_record = self._build_intermediate_format_pt(
                        row, annotation_result, file_path, record_index, column_names
                    )
                    if intermediate_record:
                        json.dump(intermediate_record, f_out, ensure_ascii=False)
                        f_out.write('\n')
                        split_record_count += 1
                        current_chunk_count += 1
                        if current_chunk_count >= chunk_size:
                            f_out.close()
                            current_chunk_index += 1
                            current_chunk_count = 0
                            f_out = _open_chunk_file(current_chunk_index)
                        
            elif category.upper() == 'SFT':
                # Use new intermediate format builder
                for record_index, row in enumerate(data_content):
                    intermediate_record = self._build_intermediate_format_sft(
                        row, annotation_result, file_path, record_index, column_names
                    )
                    if intermediate_record:
                        json.dump(intermediate_record, f_out, ensure_ascii=False)
                        f_out.write('\n')
                        split_record_count += 1
                        current_chunk_count += 1
                        if current_chunk_count >= chunk_size:
                            f_out.close()
                            current_chunk_index += 1
                            current_chunk_count = 0
                            f_out = _open_chunk_file(current_chunk_index)
        finally:
            try:
                f_out.close()
            except Exception:
                pass
        
        if split_record_count > 0:
            logger.info(f"Extracted {split_record_count} records from {file_name} ({split_name}).")
            processed_sources_list.append((f"{file_name}_({split_name})", split_record_count))
        
        return split_record_count

