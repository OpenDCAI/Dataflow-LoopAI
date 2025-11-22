"""
统一中间格式数据模型和转换工具

该模块定义了用于LLM训练的统一数据中间格式，支持预训练(PT)和微调(SFT)两种模式。
"""

import json
import hashlib
import uuid
from typing import Dict, List, Any, Optional, Literal
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime

from loopai.logger import get_logger

logger = get_logger()


class DatasetType(str, Enum):
    """数据集类型枚举"""
    PRETRAIN = "pretrain"
    SFT = "sft"
    DPO = "dpo"


class MessageRole(str, Enum):
    """消息角色枚举"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Message:
    """消息结构（用于SFT模式）"""
    role: str  # user, assistant, system, tool
    content: str
    loss_mask: Optional[bool] = None  # 默认True（如果是assistant）
    
    def __post_init__(self):
        """验证role值"""
        if self.role not in [r.value for r in MessageRole]:
            raise ValueError(f"Invalid role: {self.role}. Must be one of {[r.value for r in MessageRole]}")
        
        # 如果是assistant且loss_mask未设置，默认为True
        if self.role == MessageRole.ASSISTANT.value and self.loss_mask is None:
            self.loss_mask = True
        elif self.loss_mask is None:
            self.loss_mask = False
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "role": self.role,
            "content": self.content
        }
        if self.loss_mask is not None:
            result["loss_mask"] = self.loss_mask
        return result


@dataclass
class Meta:
    """元数据结构"""
    source: str  # 必须：数据来源标识
    language: Optional[str] = None  # 推荐：语言代码 (ISO 639-1)
    timestamp: Optional[Any] = None  # 可选：时间戳或日期字符串
    token_count: Optional[int] = None  # 可选：预计算的Token数量
    quality_score: Optional[float] = None  # 可选：质量打分 (0.0-1.0)
    original_id: Optional[str] = None  # 可选：原始数据集中的ID
    
    def __post_init__(self):
        """验证字段值"""
        if self.quality_score is not None:
            if not (0.0 <= self.quality_score <= 1.0):
                raise ValueError(f"quality_score must be between 0.0 and 1.0, got {self.quality_score}")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典，只包含非None字段"""
        result = {"source": self.source}
        if self.language is not None:
            result["language"] = self.language
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        if self.token_count is not None:
            result["token_count"] = self.token_count
        if self.quality_score is not None:
            result["quality_score"] = self.quality_score
        if self.original_id is not None:
            result["original_id"] = self.original_id
        return result


@dataclass
class UnifiedDataFormat:
    """统一中间格式数据结构"""
    id: str  # 必须：全局唯一标识符
    dataset_type: str  # 必须：pretrain, sft, dpo
    text: Optional[str] = None  # 条件必须：仅当dataset_type="pretrain"时必须存在
    messages: Optional[List[Dict[str, Any]]] = None  # 条件必须：仅当dataset_type="sft"时必须存在
    system: Optional[str] = None  # 可选：全局系统提示词
    meta: Optional[Dict[str, Any]] = None  # 推荐：元数据
    
    def __post_init__(self):
        """验证数据完整性"""
        # 验证dataset_type
        if self.dataset_type not in [dt.value for dt in DatasetType]:
            raise ValueError(f"Invalid dataset_type: {self.dataset_type}. Must be one of {[dt.value for dt in DatasetType]}")
        
        # 验证条件必须字段
        if self.dataset_type == DatasetType.PRETRAIN.value:
            if not self.text or not self.text.strip():
                raise ValueError("text field is required when dataset_type='pretrain'")
            if self.messages is not None:
                logger.warning("messages field should be None for pretrain dataset_type")
        
        elif self.dataset_type == DatasetType.SFT.value:
            if not self.messages or len(self.messages) == 0:
                raise ValueError("messages field is required and cannot be empty when dataset_type='sft'")
            if self.text is not None:
                logger.warning("text field should be None for sft dataset_type")
        
        # 验证messages结构
        if self.messages is not None:
            for msg in self.messages:
                if not isinstance(msg, dict):
                    raise ValueError(f"Each message must be a dict, got {type(msg)}")
                if "role" not in msg:
                    raise ValueError("Each message must have 'role' field")
                if "content" not in msg:
                    raise ValueError("Each message must have 'content' field")
                if msg["role"] not in [r.value for r in MessageRole]:
                    raise ValueError(f"Invalid role in message: {msg['role']}")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "id": self.id,
            "dataset_type": self.dataset_type
        }
        
        if self.text is not None:
            result["text"] = self.text
        
        if self.messages is not None:
            result["messages"] = self.messages
        
        if self.system is not None:
            result["system"] = self.system
        
        if self.meta is not None:
            result["meta"] = self.meta
        
        return result
    
    def to_json(self, ensure_ascii: bool = False) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=ensure_ascii)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UnifiedDataFormat':
        """从字典创建实例"""
        return cls(**data)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'UnifiedDataFormat':
        """从JSON字符串创建实例"""
        data = json.loads(json_str)
        return cls.from_dict(data)


def generate_id(file_path: Optional[str] = None, line_number: Optional[int] = None, content: Optional[str] = None) -> str:
    """
    生成全局唯一标识符
    
    Args:
        file_path: 文件路径（可选）
        line_number: 行号（可选）
        content: 内容（可选）
    
    Returns:
        唯一标识符（UUID或Hash）
    """
    # 如果有足够的信息，使用Hash生成确定性ID
    if file_path and line_number is not None and content:
        # 使用文件路径、行号和内容生成Hash
        combined = f"{file_path}:{line_number}:{content[:100]}"  # 只取前100个字符避免过长
        hash_obj = hashlib.sha256(combined.encode('utf-8'))
        return hash_obj.hexdigest()[:32]  # 使用32位hex作为ID
    else:
        # 否则使用UUID
        return str(uuid.uuid4())


def extract_meta_from_context(
    file_path: Optional[str] = None,
    original_data: Optional[Dict[str, Any]] = None,
    user_query: Optional[str] = None,
    source_hint: Optional[str] = None,
    language_hint: Optional[str] = None,
    original_id: Optional[str] = None
) -> Meta:
    """
    从上下文信息中提取元数据
    
    Args:
        file_path: 文件路径
        original_data: 原始数据行
        user_query: 用户查询
        source_hint: 来源提示（如 "wikipedia", "sharegpt"）
        language_hint: 语言提示（如 "zh", "en"）
        original_id: 原始ID
    
    Returns:
        Meta对象
    """
    # 提取source
    source = source_hint
    if not source and file_path:
        # 从文件路径推断来源
        file_name = file_path.split('/')[-1].split('\\')[-1].lower()
        if 'wikipedia' in file_name:
            source = "wikipedia"
        elif 'sharegpt' in file_name or 'share' in file_name:
            source = "sharegpt"
        elif 'alpaca' in file_name:
            source = "alpaca_clean"
        else:
            # 使用文件名（去除扩展名）作为source
            source = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name
            if not source:
                source = "unknown"
    if not source:
        source = "unknown"
    
    # 提取language
    language = language_hint
    if not language and original_data:
        # 尝试从原始数据中提取language字段
        if isinstance(original_data, dict):
            language = original_data.get("language") or original_data.get("lang")
    
    # 提取timestamp
    timestamp = None
    if original_data and isinstance(original_data, dict):
        timestamp = original_data.get("timestamp") or original_data.get("time") or original_data.get("date")
    
    # 提取token_count
    token_count = None
    if original_data and isinstance(original_data, dict):
        token_count = original_data.get("token_count") or original_data.get("tokens")
    
    # 提取quality_score
    quality_score = None
    if original_data and isinstance(original_data, dict):
        quality_score = original_data.get("quality_score") or original_data.get("quality")
    
    # 提取original_id
    if not original_id and original_data and isinstance(original_data, dict):
        original_id = original_data.get("id") or original_data.get("_id") or original_data.get("original_id")
    
    return Meta(
        source=source,
        language=language,
        timestamp=timestamp,
        token_count=token_count,
        quality_score=quality_score,
        original_id=original_id
    )


def validate_unified_format(data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    验证数据是否符合统一中间格式规范
    
    Args:
        data: 待验证的数据字典
    
    Returns:
        (is_valid, error_message) 元组
    """
    try:
        # 尝试创建UnifiedDataFormat实例，如果验证失败会抛出异常
        UnifiedDataFormat.from_dict(data)
        return True, None
    except Exception as e:
        return False, str(e)


def convert_to_unified_format(
    row: Dict[str, Any],
    dataset_type: str,
    mapping_result: Dict[str, Any],
    file_path: Optional[str] = None,
    line_number: Optional[int] = None,
    user_query: Optional[str] = None,
    source_hint: Optional[str] = None,
    language_hint: Optional[str] = None,
    system_prompt: Optional[str] = None
) -> Optional[UnifiedDataFormat]:
    """
    将原始数据行转换为统一中间格式
    
    Args:
        row: 原始数据行（字典）
        dataset_type: 数据集类型 ("pretrain" 或 "sft")
        mapping_result: LLM返回的字段映射结果
        file_path: 文件路径（用于生成ID和提取meta）
        line_number: 行号（用于生成ID）
        user_query: 用户查询（用于上下文）
        source_hint: 来源提示
        language_hint: 语言提示
        system_prompt: 系统提示词
    
    Returns:
        UnifiedDataFormat对象，如果转换失败则返回None
    """
    try:
        # 生成ID
        record_id = generate_id(file_path, line_number, json.dumps(row, ensure_ascii=False))
        
        # 提取meta信息
        meta = extract_meta_from_context(
            file_path=file_path,
            original_data=row,
            user_query=user_query,
            source_hint=source_hint,
            language_hint=language_hint,
            original_id=row.get("id") or row.get("_id")
        )
        
        # 根据dataset_type进行转换
        if dataset_type.lower() == DatasetType.PRETRAIN.value:
            # PT模式：提取text字段
            text_field = mapping_result.get('text')
            if not text_field:
                logger.warning(f"No text field found in mapping result for pretrain: {mapping_result}")
                return None
            
            # 处理text字段（可能是单个字段名或字段名列表）
            text_parts = []
            if isinstance(text_field, list):
                # 多个字段需要合并
                for field_name in text_field:
                    if field_name in row:
                        value = row[field_name]
                        if value is not None:
                            text_parts.append(str(value).strip())
            else:
                # 单个字段
                if text_field in row:
                    value = row[text_field]
                    if value is not None:
                        text_parts.append(str(value).strip())
            
            # 合并文本
            text = "\n".join(text_parts).strip()
            if not text:
                logger.warning(f"Empty text after extraction for pretrain record")
                return None
            
            return UnifiedDataFormat(
                id=record_id,
                dataset_type=DatasetType.PRETRAIN.value,
                text=text,
                system=system_prompt,
                meta=meta.to_dict()
            )
        
        elif dataset_type.lower() == DatasetType.SFT.value:
            # SFT模式：提取question和answer，构建messages
            question_field = mapping_result.get('question')
            answer_field = mapping_result.get('answer')
            
            if not question_field and not answer_field:
                logger.warning(f"No question or answer field found in mapping result for sft: {mapping_result}")
                return None
            
            messages = []
            
            # 提取system prompt（如果有）
            system_in_messages = None
            if system_prompt:
                system_in_messages = system_prompt
            
            # 检查原始数据是否已经是messages格式
            if 'messages' in row and isinstance(row['messages'], list):
                # 已经是messages格式，直接使用
                for msg in row['messages']:
                    if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                        messages.append({
                            "role": msg['role'],
                            "content": msg['content'],
                            "loss_mask": msg.get('loss_mask', msg['role'] == MessageRole.ASSISTANT.value)
                        })
            else:
                # 从question/answer构建messages
                # 提取question
                questions = []
                if question_field:
                    if isinstance(question_field, list):
                        for field_name in question_field:
                            if field_name in row and row[field_name] is not None:
                                questions.append(str(row[field_name]).strip())
                    else:
                        if question_field in row and row[question_field] is not None:
                            questions.append(str(row[question_field]).strip())
                
                # 提取answer
                answers = []
                if answer_field:
                    if isinstance(answer_field, list):
                        for field_name in answer_field:
                            if field_name in row and row[field_name] is not None:
                                answers.append(str(row[field_name]).strip())
                    else:
                        if answer_field in row and row[answer_field] is not None:
                            answers.append(str(row[answer_field]).strip())
                
                # 构建messages（支持多轮对话）
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
            
            if not messages:
                logger.warning(f"No valid messages constructed for sft record")
                return None
            
            # 如果有system prompt且messages中没有system role，添加到开头
            if system_in_messages:
                has_system = any(msg.get("role") == MessageRole.SYSTEM.value for msg in messages)
                if not has_system:
                    messages.insert(0, {
                        "role": MessageRole.SYSTEM.value,
                        "content": system_in_messages,
                        "loss_mask": False
                    })
            
            return UnifiedDataFormat(
                id=record_id,
                dataset_type=DatasetType.SFT.value,
                messages=messages,
                system=system_prompt if not system_in_messages else None,  # 如果已经在messages中，顶层system可以为None
                meta=meta.to_dict()
            )
        
        else:
            logger.error(f"Unsupported dataset_type: {dataset_type}")
            return None
    
    except Exception as e:
        logger.error(f"Error converting to unified format: {e}", exc_info=True)
        return None

