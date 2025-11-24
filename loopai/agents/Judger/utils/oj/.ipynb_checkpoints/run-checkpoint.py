from .generate import generate_sample, generate_sample_sql
from .evaluate_sql import evaluate_sample

# ---------------------- 可配置参数 ----------------------
CONFIG = {
    # 代码生成模型
    "MODEL_PATH": "/jizhicfs/hymiezhao/models/Qwen2.5-Math-7B",
    # 每个任务样本数
    "NUM_SAMPLES_PER_TASK": 10,
    # 最大输入长度
    "MAX_INPUT_LENGTH": 512,
    # 最大TOKEN长度
    "MAX_NEW_TOKENS": 200,
    "TEMPERATURE": 0,
    "TOP_P": 0.95,
    # 样例的文件路径
    "SAMPLE_FILE": "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/sample/test_samples_all.jsonl",
    # 问题文件路径
    "PROBLEM_FILE": "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/data/mbpp.jsonl",
    # 结果文件路径
    "RESULT_FILE": "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/result/mbpp_result.jsonl",
    ##### 适配部分#####
    # 适配模式 text为文本函数名，name为非文本函数名，lambda为匿名函数
    "FUNCTION_MODE": "text",
    # 问题提示词适配函数名--传入参数 data_line为问题集的一条数据
    "PROMPT_FUNCTION_NAME": "prompt_example",
    # 测试代码拼接函数名--传入参数 data_line为问题集的一条数据，completion为生成内容
    "TEST_CODE_FUNCTION_NAME": "test_code_example",
    # 评测代码拼接函数名--传入参数 data_line为问题集的一条数据
    "TEST_FUNCTION_NAME": "test_example",
    # 进入点函数名
    "ENTRY_POINT_FUNCTION_NAME": "entry_point_example",
    # pass@k
    "K": "1,10,100",
    # n_workers
    "N_WORKERS": 1,
    # 超时限制
    "TIMEOUT": 3.0,
}

# gen：是否进行生成，eval：是否进行评测, config配置项


CONFIG_TEST = {
    # 代码生成模型
    "MODEL_PATH": "/jizhicfs/hymiezhao/models/Qwen2.5-Math-7B",
    # 每个任务样本数
    "NUM_SAMPLES_PER_TASK": 1,
    # 最大输入长度
    "MAX_INPUT_LENGTH": 4096,
    # 最大TOKEN长度
    "MAX_NEW_TOKENS": 2048,
    "TEMPERATURE": 0,
    "TOP_P": 0.95,
    # 样例的文件路径
    "SAMPLE_FILE": "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/sample/dev_bird_for_oj_result.jsonl",
    # 问题文件路径
    "PROBLEM_FILE": "/jizhicfs/hymiezhao/lpc/repos/lr/OmniSQL/data/dev_bird_for_oj_20.jsonl",
    # 结果文件路径
    "RESULT_FILE": "/jizhicfs/hymiezhao/lpc/repos/yjh/llmaj/V2/result/dev_bird_for_oj_result.jsonl",
    ##### 适配部分#####
    # 适配模式 text为文本函数名，name为非文本函数名，lambda为匿名函数
    "FUNCTION_MODE": "text",
    # 问题提示词适配函数名--传入参数 data_line为问题集的一条数据
    "PROMPT_FUNCTION_NAME": "prompt_example",
    # 测试代码拼接函数名--传入参数 data_line为问题集的一条数据，completion为生成内容
    "TEST_CODE_FUNCTION_NAME": "test_code_example",
    # 评测代码拼接函数名--传入参数 data_line为问题集的一条数据
    "TEST_FUNCTION_NAME": "test_example",
    # 进入点函数名
    "ENTRY_POINT_FUNCTION_NAME": "entry_point_example",
    # pass@k
    "K": "1",
    # n_workers
    "N_WORKERS": 1,
    # 超时限制
    "TIMEOUT": 30.0,
}

def gen2eval(gen: bool, eval: bool, config=CONFIG):
    if gen:
        generate_sample(config)
    if eval:
        evaluate_sample(config)


if __name__ == "__main__":
    gen2eval(True, True, CONFIG_TEST)
