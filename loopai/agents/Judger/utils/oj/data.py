import os
import json
from typing import List, Dict, Tuple, Iterable
import gzip

def read_problems(evalset_file) -> Dict[str, Dict]:
    return {task["task_id"]: task for task in stream_jsonl(evalset_file)}


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

def check_problem_path_accessible(problem_path: str) -> bool:
    """
    检测problem_path是否可正常打开（具备可读权限）
    :param problem_path: 待检测的problem路径
    :return: 可打开返回True，不可打开返回False
    """
    if not isinstance(problem_path, str) or len(problem_path.strip()) == 0:
        # print("错误：problem_path不能为空或非字符串类型")
        return False
    
    try:
        # 尝试以只读模式打开文件，验证可访问性（存在且具备读权限）
        with open(problem_path, 'r', encoding='utf-8') as f:
            pass
        # print(f"成功：problem_path '{problem_path}' 可正常打开")
        return True
    except FileNotFoundError:
        # print(f"错误：problem_path '{problem_path}' 文件不存在")
        return False
    except PermissionError:
        # print(f"错误：problem_path '{problem_path}' 没有可读权限")
        return False
    except Exception as e:
        # print(f"错误：打开problem_path '{problem_path}' 时发生未知异常：{str(e)}")
        return False

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

def check_file(state):
    problem_format_path = state.get("judger", {}).get("eval_problem_format_path", "")
    problem_path = state.get("judger", {}).get("eval_problem_path", "")
    test_case_path = state.get("judger", {}).get("eval_test_case_path", "")
    result_path = state.get("judger", {}).get("eval_result_path", "")

    # 检测problem_path是否可打开
    problem_path_check_res = check_problem_path_accessible(problem_path)
    problem_format_path_check_res = is_valid_jsonl_file_path(problem_format_path, allow_empty=True)
    test_case_path_check_res = is_valid_jsonl_file_path(test_case_path, allow_empty=False)
    # 检测problem_format_path、test_case_path、result_path是否为jsonl文件的路径，其中problem_format_path可为空
    result_path_check_res = is_valid_jsonl_file_path(result_path, allow_empty=False)
    
    return {
        "eval_problem_path":problem_path_check_res,
        "eval_problem_format_path":problem_format_path_check_res,
        "eval_test_case_path":test_case_path_check_res,
        "eval_result_path":result_path_check_res
    }
    
