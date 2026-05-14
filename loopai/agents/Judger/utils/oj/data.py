import os
import json
from pathlib import Path
from typing import List, Dict, Tuple, Iterable
import gzip

def read_problems(evalset_file) -> Dict[str, Dict]:
    return {task["task_id"]: task for task in stream_jsonl(evalset_file)}

def check_jsonl_fields(filepath: str, require_fields: List[str]) -> Tuple[bool, List[str]]:
    """
    检查 JSONL 文件中的每一行是否都包含指定的字段。

    参数:
        filepath: JSONL 文件路径
        require_fields: 必需的字段列表

    返回:
        如果所有行都包含所有字段，返回 True；否则返回 False
        是否全部通过校验, 缺失字段或错误的详细信息列表
    """
    error_details = []
    try:
        with open(filepath, 'rb') as f:
            for line_num, line_bytes in enumerate(f, start=1):
                try:
                    # 去除首尾空白并解码
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    error_details.append(f"第 {line_num} 行: 无法解码的字符编码")
                    continue

                if not line:  # 跳过空行
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    error_details.append(f"第 {line_num} 行: JSON 解析失败 ({e})")
                    continue  

                # 检查每个必需字段
                for field in require_fields:
                    if field not in data:
                        error_details.append(f"第 {line_num} 行: 缺少必填字段 '{field}'")
                        

    except FileNotFoundError:
        return False, [f"文件未找到: {filepath}"]

    except Exception as e:
        return False, [f"读取文件时发生未知错误: {e}"]

    # 如果错误列表为空，说明校验通过
    is_valid = len(error_details) == 0
    return is_valid, error_details
    
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
