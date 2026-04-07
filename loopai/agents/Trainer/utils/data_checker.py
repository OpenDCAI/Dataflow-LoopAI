"""
数据检查工具
验证数据集是否符合 LlamaFactory 的要求
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from loopai.logger import get_logger

logger = get_logger()


def check_data_format(dataset_path: str) -> Dict[str, Any]:
    """
    检查数据集格式是否符合 LlamaFactory 要求
    
    Args:
        dataset_path: 数据集文件路径 (json 或 jsonl 格式)
    
    Returns:
        检查结果字典，包含：
        - is_valid: bool 是否有效
        - format_type: str 数据格式类型 (json/jsonl)
        - total_samples: int 样本总数
        - errors: List[str] 错误列表
        - warnings: List[str] 警告列表
        - sample_preview: List[Dict] 前几个样本预览
    """
    result = {
        "is_valid": False,
        "format_type": None,
        "total_samples": 0,
        "errors": [],
        "warnings": [],
        "sample_preview": []
    }
    
    # 检查文件是否存在
    if not os.path.exists(dataset_path):
        result["errors"].append(f"数据文件不存在: {dataset_path}")
        return result
    
    file_path = Path(dataset_path)
    file_ext = file_path.suffix.lower()
    
    # 检查文件扩展名
    if file_ext not in ['.json', '.jsonl']:
        result["errors"].append(f"不支持的文件格式: {file_ext}，仅支持 .json 和 .jsonl")
        return result
    
    result["format_type"] = "json" if file_ext == ".json" else "jsonl"
    
    try:
        # 读取数据
        data = _load_dataset(dataset_path, result["format_type"])
        result["total_samples"] = len(data)
        
        if result["total_samples"] == 0:
            result["errors"].append("数据文件为空")
            return result
            
        # 验证数据结构
        validation_result = _validate_llamafactory_format(data)
        result["errors"].extend(validation_result["errors"])
        result["warnings"].extend(validation_result["warnings"])
        
        # 设置预览
        result["sample_preview"] = data[:3] if len(data) >= 3 else data
        
        # 判断是否有效
        result["is_valid"] = len(result["errors"]) == 0
        
    except Exception as e:
        result["errors"].append(f"读取数据文件时发生错误: {str(e)}")
    
    return result


def _load_dataset(dataset_path: str, format_type: str) -> List[Dict[str, Any]]:
    """加载数据集"""
    data = []
    
    if format_type == "json":
        with open(dataset_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
            if isinstance(content, list):
                data = content
            else:
                raise ValueError("JSON 文件应该包含一个列表")
    else:  # jsonl
        with open(dataset_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        data.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        raise ValueError(f"第 {line_num} 行 JSON 格式错误: {str(e)}")
    
    return data


def _validate_llamafactory_format(data: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    验证数据是否符合 LlamaFactory 格式要求
    
    LlamaFactory 支持的格式:
    1. 指令格式: {"instruction": "...", "input": "...", "output": "..."}
    2. 对话格式: {"conversations": [{"from": "human/gpt", "value": "..."}]}
    3. Alpaca格式: {"instruction": "...", "input": "...", "output": "..."}
    """
    errors = []
    warnings = []
    
    if not isinstance(data, list):
        errors.append("数据应该是一个列表")
        return {"errors": errors, "warnings": warnings}
    
    # 分析数据格式类型
    format_types = set()
    for i, sample in enumerate(data[:10]):  # 检查前10个样本来确定格式
        if not isinstance(sample, dict):
            errors.append(f"样本 {i+1} 不是字典格式")
            continue
            
        if "conversations" in sample:
            format_types.add("conversation")
        elif all(key in sample for key in ["instruction", "output"]):
            format_types.add("alpaca")
        else:
            format_types.add("unknown")
    
    if len(format_types) > 1:
        warnings.append("数据集包含多种格式，建议统一格式")
    
    if "unknown" in format_types:
        errors.append("检测到不支持的数据格式")
    
    # 详细验证每个样本
    for i, sample in enumerate(data):
        sample_errors = _validate_single_sample(sample, i + 1)
        errors.extend(sample_errors)
        
        # 只报告前10个错误样本，避免输出过多
        if len([e for e in errors if f"样本 {i+1}" in e]) >= 10:
            warnings.append(f"发现过多错误样本，仅显示前10个，总样本数: {len(data)}")
            break
    
    return {"errors": errors, "warnings": warnings}


def _validate_single_sample(sample: Dict[str, Any], index: int) -> List[str]:
    """验证单个样本"""
    errors = []
    
    if not isinstance(sample, dict):
        errors.append(f"样本 {index} 不是字典格式")
        return errors
    
    # 检查对话格式
    if "conversations" in sample:
        conversations = sample["conversations"]
        if not isinstance(conversations, list):
            errors.append(f"样本 {index} conversations 应该是列表")
        else:
            for j, conv in enumerate(conversations):
                if not isinstance(conv, dict):
                    errors.append(f"样本 {index} conversations[{j}] 不是字典格式")
                    continue
                if "from" not in conv or "value" not in conv:
                    errors.append(f"样本 {index} conversations[{j}] 缺少 'from' 或 'value' 字段")
                if conv.get("from") not in ["human", "gpt", "system"]:
                    errors.append(f"样本 {index} conversations[{j}] 'from' 字段值无效: {conv.get('from')}")
    
    # 检查指令格式
    elif "instruction" in sample:
        if "output" not in sample:
            errors.append(f"样本 {index} 缺少 'output' 字段")
        if not isinstance(sample.get("instruction"), str):
            errors.append(f"样本 {index} 'instruction' 应该是字符串")
        if not isinstance(sample.get("output"), str):
            errors.append(f"样本 {index} 'output' 应该是字符串")
    
    else:
        errors.append(f"样本 {index} 格式不符合要求，需要包含 'conversations' 或 'instruction' 字段")
    
    return errors


def generate_format_report(check_result: Dict[str, Any]) -> str:
    """生成格式检查报告"""
    report = []
    report.append("="*50)
    report.append("数据集格式检查报告")
    report.append("="*50)
    
    # 基本信息
    report.append(f"文件格式: {check_result.get('format_type', 'unknown')}")
    report.append(f"样本总数: {check_result.get('total_samples', 0)}")
    report.append(f"验证结果: {'通过' if check_result.get('is_valid', False) else '未通过'}")
    report.append("")
    
    # 错误信息
    if check_result.get("errors"):
        report.append("发现的错误:")
        for error in check_result["errors"]:
            report.append(f"  ❌ {error}")
        report.append("")
    
    # 警告信息
    if check_result.get("warnings"):
        report.append("警告信息:")
        for warning in check_result["warnings"]:
            report.append(f"  ⚠️  {warning}")
        report.append("")
    
    # 样本预览
    if check_result.get("sample_preview"):
        report.append("数据样本预览:")
        for i, sample in enumerate(check_result["sample_preview"]):
            report.append(f"  样本 {i+1}:")
            report.append(f"    {json.dumps(sample, ensure_ascii=False, indent=2)}")
        report.append("")
    
    # 建议
    if not check_result.get("is_valid", False):
        report.append("修改建议:")
        report.append("  1. 确保数据格式为 JSON 或 JSONL")
        report.append("  2. 使用支持的数据结构:")
        report.append("     - 对话格式: {\"conversations\": [{\"from\": \"human\", \"value\": \"...\"}, {\"from\": \"gpt\", \"value\": \"...\"}]}")
        report.append("     - 指令格式: {\"instruction\": \"...\", \"input\": \"...\", \"output\": \"...\"}")
        report.append("  3. 检查每个样本的字段完整性")
    
    return "\n".join(report)


# ==================== verl 数据格式检查 ====================

def check_verl_data_format(dataset_path: str, train_mode: str = "grpo") -> Dict[str, Any]:
    """
    检查数据集格式是否符合 verl 要求

    Args:
        dataset_path: 数据集文件路径
        train_mode: 训练模式 ("grpo" / "ppo" / "sft")
    Returns:
        检查结果字典
    """
    result = {
        "is_valid": False, "format_type": None, "total_samples": 0,
        "errors": [], "warnings": [], "sample_preview": [], "verl_mode": train_mode
    }
    if not os.path.exists(dataset_path):
        result["errors"].append(f"数据文件不存在: {dataset_path}")
        return result

    file_ext = Path(dataset_path).suffix.lower()
    if file_ext not in ['.parquet', '.json', '.jsonl']:
        result["errors"].append(f"不支持的文件格式: {file_ext}，verl 支持 .parquet/.json/.jsonl")
        return result
    result["format_type"] = file_ext.lstrip('.')

    try:
        if file_ext == '.parquet':
            data = _load_parquet_samples(dataset_path, max_samples=50)
        else:
            data = _load_dataset(dataset_path, "json" if file_ext == ".json" else "jsonl")
        result["total_samples"] = len(data)
        if result["total_samples"] == 0:
            result["errors"].append("数据文件为空")
            return result

        if train_mode in ("grpo", "ppo"):
            validation = _validate_verl_rl_format(data)
        elif train_mode == "sft":
            validation = _validate_verl_sft_format(data)
        else:
            result["errors"].append(f"未知的 verl 训练模式: {train_mode}")
            return result
        result["errors"].extend(validation["errors"])
        result["warnings"].extend(validation["warnings"])
        result["sample_preview"] = [_safe_preview(s) for s in data[:3]]
        result["is_valid"] = len(result["errors"]) == 0
    except Exception as e:
        result["errors"].append(f"读取数据文件时发生错误: {str(e)}")
    return result


def _load_parquet_samples(dataset_path: str, max_samples: int = 50) -> List[Dict[str, Any]]:
    """从 parquet 文件加载样本"""
    try:
        import pyarrow.parquet as pq
        table = pq.read_table(dataset_path)
        if max_samples > 0 and table.num_rows > max_samples:
            table = table.slice(0, max_samples)
        return table.to_pylist()
    except ImportError:
        import pandas as pd
        df = pd.read_parquet(dataset_path)
        return df.head(max_samples).to_dict('records') if max_samples > 0 else df.to_dict('records')


def _validate_verl_rl_format(data: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """验证 verl RL 数据格式 (GRPO/PPO): 需要 prompt 字段 (list[{role, content}])"""
    errors, warnings = [], []
    fields = set()
    for s in data[:10]:
        if isinstance(s, dict):
            fields.update(s.keys())
    if "prompt" not in fields:
        errors.append("缺少必需字段 'prompt'（verl RL 需要 [{role, content}] 格式的对话列表）")
    if "data_source" not in fields:
        warnings.append("缺少 'data_source' 字段，建议添加用于日志分组")

    err_n = 0
    for i, sample in enumerate(data):
        if not isinstance(sample, dict):
            continue
        prompt = sample.get("prompt")
        if prompt is None:
            err_n += 1
            if err_n <= 5: errors.append(f"样本 {i+1} 缺少 'prompt' 字段")
            continue
        if not isinstance(prompt, list):
            err_n += 1
            if err_n <= 5: errors.append(f"样本 {i+1} 'prompt' 应为列表，实际: {type(prompt).__name__}")
            continue
        for j, msg in enumerate(prompt):
            if not isinstance(msg, dict):
                err_n += 1
                if err_n <= 5: errors.append(f"样本 {i+1} prompt[{j}] 应为字典")
                continue
            if "role" not in msg or "content" not in msg:
                err_n += 1
                if err_n <= 5: errors.append(f"样本 {i+1} prompt[{j}] 缺少 'role' 或 'content'")
        if err_n >= 10:
            warnings.append(f"错误过多（{err_n}+），仅显示前5个")
            break
    return {"errors": errors, "warnings": warnings}


def _validate_verl_sft_format(data: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """验证 verl SFT 数据格式: 需要 messages 字段 (list[{role, content}])"""
    errors, warnings = [], []
    fields = set()
    for s in data[:10]:
        if isinstance(s, dict):
            fields.update(s.keys())
    if "messages" not in fields:
        errors.append("缺少必需字段 'messages'（verl SFT 需要 [{role, content}] 格式的多轮对话列表）")

    err_n = 0
    for i, sample in enumerate(data):
        if not isinstance(sample, dict):
            continue
        messages = sample.get("messages")
        if messages is None:
            err_n += 1
            if err_n <= 5: errors.append(f"样本 {i+1} 缺少 'messages' 字段")
            continue
        if not isinstance(messages, list):
            err_n += 1
            if err_n <= 5: errors.append(f"样本 {i+1} 'messages' 应为列表，实际: {type(messages).__name__}")
            continue
        if len(messages) < 2:
            if err_n <= 5: warnings.append(f"样本 {i+1} messages 仅 {len(messages)} 条，建议至少一轮对话")
        for j, msg in enumerate(messages):
            if not isinstance(msg, dict):
                err_n += 1
                if err_n <= 5: errors.append(f"样本 {i+1} messages[{j}] 应为字典")
                continue
            if "role" not in msg or "content" not in msg:
                err_n += 1
                if err_n <= 5: errors.append(f"样本 {i+1} messages[{j}] 缺少 'role' 或 'content'")
        if err_n >= 10:
            warnings.append(f"错误过多（{err_n}+），仅显示前5个")
            break
    return {"errors": errors, "warnings": warnings}


def _safe_preview(sample: Any) -> Any:
    """将样本转换为可 JSON 序列化的格式"""
    if isinstance(sample, dict):
        out = {}
        for k, v in sample.items():
            try:
                json.dumps(v, ensure_ascii=False)
                out[k] = v
            except (TypeError, ValueError):
                out[k] = str(v)[:200]
        return out
    return str(sample)


def generate_verl_format_report(check_result: Dict[str, Any]) -> str:
    """生成 verl 数据格式检查报告"""
    report = ["=" * 50, "verl 数据集格式检查报告", "=" * 50]
    report.append(f"训练模式: {check_result.get('verl_mode', 'unknown')}")
    report.append(f"文件格式: {check_result.get('format_type', 'unknown')}")
    report.append(f"样本总数: {check_result.get('total_samples', 0)}")
    report.append(f"验证结果: {'通过' if check_result.get('is_valid', False) else '未通过'}")
    report.append("")
    if check_result.get("errors"):
        report.append("错误:")
        for e in check_result["errors"]:
            report.append(f"  ❌ {e}")
        report.append("")
    if check_result.get("warnings"):
        report.append("警告:")
        for w in check_result["warnings"]:
            report.append(f"  ⚠️  {w}")
        report.append("")
    mode = check_result.get("verl_mode", "grpo")
    if not check_result.get("is_valid", False):
        report.append("修改建议:")
        report.append("  1. 确保数据格式为 parquet / JSON / JSONL")
        if mode in ("grpo", "ppo"):
            report.append('  2. RL 数据需含 prompt 字段: [{"role":"user","content":"..."}]')
            report.append("  3. 建议添加 data_source 字段标识数据来源")
        else:
            report.append('  2. SFT 数据需含 messages 字段: [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]')
    return "\n".join(report)
