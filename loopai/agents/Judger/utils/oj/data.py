import os
import json
from pathlib import Path
from typing import List, Dict, Tuple, Iterable
import gzip

def read_problems(evalset_file) -> Dict[str, Dict]:
    return {task["task_id"]: task for task in stream_jsonl(evalset_file)}

import json
from typing import List

def check_jsonl_fields(filepath: str, require_fields: List[str]) -> bool:
    """
    检查 JSONL 文件中的每一行是否都包含指定的字段。

    参数:
        filepath: JSONL 文件路径
        require_fields: 必需的字段列表

    返回:
        如果所有行都包含所有字段，返回 True；否则返回 False
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:  # 跳过空行
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"第 {line_num} 行 JSON 解析错误: {e}")
                    return False
                
                # 检查每个必需字段
                for field in require_fields:
                    if field not in data:
                        print(f"第 {line_num} 行缺少字段 '{field}'")
                        return False
        return True
    except FileNotFoundError:
        print(f"文件不存在: {filepath}")
        return False
    except Exception as e:
        print(f"读取文件时发生错误: {e}")
        return False
    
def stream_jsonl(filename: str) -> Iterable[Dict]:
    """
    Parses each jsonl line and yields it as a dictionary
    """
    if filename.endswith(".gz"):
        with open(filename, "rb") as gzfp:
            with gzip.open(gzfp, 'rt') as fp:
                for line in fp:
                    if any(not x.isspace() for x in line):
                        yield json.loads(line)
    else:
        with open(filename, "r") as fp:
            for line in fp:
                if any(not x.isspace() for x in line):
                    yield json.loads(line)


def write_jsonl(filename: str, data: Iterable[Dict], append: bool = False):
    dir_path = Path(filename)
    (dir_path.parent).mkdir(parents=True, exist_ok=True)
    """
    Writes an iterable of dictionaries to jsonl
    """
    if append:
        mode = 'ab'
    else:
        mode = 'wb'
    filename = os.path.expanduser(filename)
    if filename.endswith(".gz"):
        with open(filename, mode) as fp:
            with gzip.GzipFile(fileobj=fp, mode='wb') as gzfp:
                for x in data:
                    gzfp.write((json.dumps(x) + "\n").encode('utf-8'))
    else:
        with open(filename, mode) as fp:
            for x in data:
                fp.write((json.dumps(x) + "\n").encode('utf-8'))

def log(message: str, log_file):
    print(message)
    log_file.write(message + "\n")

def is_valid_jsonl_file_path(file_path: str, allow_empty: bool = False) -> bool:
    """
    检测路径是否为合法的jsonl文件路径（核心判断后缀为.jsonl）
    :param file_path: 待检测的文件路径
    :param allow_empty: 是否允许路径为空（仅针对problem_format_path）
    :return: 合法返回True，不合法返回False
    """
    # 处理允许为空的场景（仅用于problem_format_path）
    if allow_empty:
        if file_path is None or (isinstance(file_path, str) and len(file_path.strip()) == 0):
            return True
    
    # 非空场景的基础校验
    if not isinstance(file_path, str) or len(file_path.strip()) == 0:
        #print("错误：文件路径不能为空或非字符串类型")
        return False
    
    # 判断文件后缀是否为.jsonl（忽略大小写，兼容.JSONL、.JsonL等格式）
    file_suffix = os.path.splitext(file_path)[1].lower()
    if file_suffix != '.jsonl':
        #print(f"错误：文件路径 '{file_path}' 不是jsonl文件（后缀需为.jsonl）")
        return False
    
    #print(f"成功：文件路径 '{file_path}' 是合法的jsonl文件路径")
    return True
