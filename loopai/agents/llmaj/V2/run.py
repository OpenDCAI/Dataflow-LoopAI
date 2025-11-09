import sys
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
import data
import generate
import evaluate

# ---------------------- 可配置参数 ----------------------
CONFIG = {
    # 代码生成模型
    "MODEL_PATH" : "/jizhicfs/hymiezhao/models/Qwen2.5-Math-7B",
    # 每个任务样本数
    "NUM_SAMPLES_PER_TASK" : 10,
    # 最大输入长度
    "MAX_INPUT_LENGTH" : 512,
    # 最大TOKEN长度
    "MAX_NEW_TOKENS" : 200,
    "TEMPERATURE" : 0.7,
    "TOP_P" : 0.95,
    # 样例的文件路径
    "SAMPLE_FILE" : "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/sample/test_samples_all.jsonl",
    # 问题文件路径
    "PROBLEM_FILE" : "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/data/mbpp.jsonl",
    # 结果文件路径
    "RESULT_FILE" : "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/result/mbpp_result.jsonl",
    #####适配部分#####
    # 适配模式 text为文本函数名，name为非文本函数名，lambda为匿名函数
    "FUNCTION_MODE" : "text",
    # 问题提示词适配函数名--传入参数 data_line为问题集的一条数据
    "PROMPT_FUNCTION_NAME" : "prompt_example",
    # 测试代码拼接函数名--传入参数 data_line为问题集的一条数据，completion为生成内容
    "TEST_CODE_FUNCTION_NAME" : "test_code_example",
    # 评测代码拼接函数名--传入参数 data_line为问题集的一条数据
    "TEST_FUNCTION_NAME" : "test_example",
    # 进入点函数名
    "ENTRY_POINT_FUNCTION_NAME" : "entry_point_example",
    #pass@k
    "K" : "1,10,100",
    #n_workers
    "N_WORKERS" : 1,
    #超时限制
    "TIMEOUT" : 3.0,
}

# gen：是否进行生成，eval：是否进行评测, config配置项
def gen2eval( gen:bool, eval:bool, config = CONFIG):
    if gen:
        generate.generate_sample(config)
    if eval:
        evaluate.evaluate_sample(config)

if __name__ == "__main__":
    gen2eval(True, True, CONFIG) 