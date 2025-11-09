import os
import re
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForCausalLM
import json
import tempfile
import subprocess
import time
from typing import List, Dict, Tuple, Iterable
import gzip

# 自定义提示函数样例
def prompt_example(data_line):
    # return "We need a function whose functionality is " + data_line["text"] +" and whose opening definition is " + extract_function_definitions(data_line["code"]) + "\n.Please only complete the function content and add the part of the function definition." #mbpp数据集使用
    return data_line["prompt"] #humaneval数据集使用

#自定义测试代码函数样例
def test_example(data_line):
    # mbpp数据集使用
    # deftext = extract_function_definitions(data_line["code"])
    # funcname = extract_func_name(deftext)
    # test_code = "def check(candidate):\n"
    # for test_item in data_line["test_list"]:
    #     test_code = test_code + "    " + test_item.replace(funcname,"candidate") + "\n"
    # return test_code

    return data_line["test"] #humaneval数据集使用

def test_code_example(data_line, completion):
    # return f"{completion}\n\n" # mbpp数据集使用
    return f"{data_line['prompt']}\n{completion}\n\n" #humaneval数据集使用

#自定义进入点
def entry_point_example(data_line):
    # mbpp数据集使用
    # deftext = extract_function_definitions(data_line["code"])
    # return extract_func_name(deftext)

    return data_line["entry_point"] # humaneval数据集使用

# 提取函数定义部分
def extract_function_definitions(code_text):
    """
    从Python代码文本中提取所有函数定义行（def 函数名(参数):）
    
    参数：
        code_text: 包含Python代码的字符串
        
    返回：
        list: 所有函数定义行的列表（按出现顺序），若没有则返回空列表
    """
    # 分割代码文本为行（自动处理\r\n、\n等换行符）
    lines = code_text.splitlines()
    
    # 正则模式：匹配函数定义行
    # 解释：
    # ^\s*        行首可能有0个或多个空白字符（缩进）
    # def\s+      匹配def关键字，后面跟1个或多个空格
    # \w+         匹配函数名（符合Python标识符规则）
    # \s*\(.*\)   匹配参数列表（括号内任意内容，括号前后可能有空格）
    # \s*:        匹配冒号，前面可能有0个或多个空格
    pattern = re.compile(r'^\s*def\s+\w+\s*\(.*\)\s*:')
    
    # 筛选出所有匹配的行
    function_defs = [line for line in lines if pattern.match(line)]
    
    return function_defs[0]

# 提取函数名
def extract_func_name(def_str):
    """从函数定义字符串中提取函数名"""
    # 去除字符串前后空白（避免多余空格干扰）
    def_str = def_str.strip()
    # 正则模式：匹配def开头的函数定义，捕获函数名
    pattern = r'^def\s+(\w+)\s*\('
    # 匹配字符串（从开头开始匹配）
    match = re.match(pattern, def_str)
    if match:
        return match.group(1)  # 返回捕获的函数名
    else:
        return None  # 若不匹配，返回None（表示不是有效的函数定义）