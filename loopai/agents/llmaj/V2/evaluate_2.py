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
import customer
import data
# 测试单样例代码
def run_single_test(test_script: str, timeout: int = 5) -> Tuple[bool, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as temp_file:
        temp_file.write(test_script)
        temp_file_path = temp_file.name

    try:
        result = subprocess.run(
            ["pytest", temp_file_path, "-v", "-x"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )
        passed = "PASSED" in result.stdout
        error_msg = f"stdout: {result.stdout}\nstderr: {result.stderr}" if not passed else "无错误"
        return passed, error_msg
    except subprocess.TimeoutExpired:
        return False, "测试超时"
    except Exception as e:
        return False, f"执行出错：{str(e)}"
    finally:
        os.unlink(temp_file_path)

# 加载生成的样本
def load_generated_samples(sample_path: str, log_file) -> Dict[str, List[str]]:
    task_samples = {}
    with open(sample_path, "r", encoding="utf-8") as f:
        for line in f:
            sample = json.loads(line.strip())
            task_id = sample["task_id"]
            completion = sample["completion"]
            if task_id not in task_samples:
                task_samples[task_id] = []
            task_samples[task_id].append(completion)
    data.log(f"加载完成：共{len(task_samples)}个任务，样本数={sum(len(v) for v in task_samples.values())}", log_file)
    return task_samples

# 评估任务
def evaluate_task(task_id: str, problem: Dict, completions: List[str], log_file, config) -> bool:
    data.log(f"\n===== 开始评估任务 {task_id}（共{len(completions)}个样本） =====", log_file)
    for idx, completion in enumerate(completions, 1):
        data.log(f"----- 测试第{idx}个样本 -----", log_file)
        test_script = generate_test_script(problem, completion, log_file, config)
        passed, error_msg = run_single_test(test_script)
        if passed:
            data.log(f"✅ 第{idx}个样本通过测试！", log_file)
            return True
        else:
            data.log(f"❌ 第{idx}个样本未通过，错误信息：\n{error_msg}", log_file)
    data.log(f"===== 任务{task_id}：所有样本均未通过 =====", log_file)
    return False

#计算pass@k值和每个任务的评估结果
def calculate_pass_at_k(
    task_samples: Dict[str, List[str]],
    problems: Dict[str, Dict],
    config,
    log_file,
    k: List[int] = [1, 10]
) -> Tuple[Dict[int, float], Dict[str, bool]]:
    total_tasks = len(task_samples)
    if total_tasks == 0:
        return {}, {}
    
    passed_tasks = 0
    task_results = {}  # 存储每个任务的评估结果：{task_id: 是否通过}
    for task_id in task_samples:
        if task_id not in problems:
            data.log(f"跳过未知任务：{task_id}", log_file)
            task_results[task_id] = False
            continue
        # 仅评估一次，结果存入task_results
        result = evaluate_task(task_id, problems[task_id], task_samples[task_id], log_file, config)
        task_results[task_id] = result
        if result:
            passed_tasks += 1
    pass_at_k = {}
    for current_k in k:
        pass_at_k[current_k] = round(passed_tasks / total_tasks, 4)
    return pass_at_k, task_results  # 返回pass_at_k和任务结果字典

# 生成拼接测试代码
def generate_test_script(problem: Dict, completion: str, log_file, config) -> str:
    customer_func_1 = getattr(customer, config['TEST_CODE_FUNCTION_NAME'])
    test_script = customer_func_1(problem, completion)

    customer_func_2 = getattr(customer, config['ENTRY_POINT_FUNCTION_NAME'])
    entry_point = customer_func_2(problem)

    customer_func_3 = getattr(customer, config['TEST_FUNCTION_NAME'])
    test_code = customer_func_3(problem)

    if "def check(candidate)" in test_code and entry_point:
        test_code = test_code.replace("def check(candidate):", f"def test_check():\n    candidate = {entry_point}")
        test_script += test_code
    else:
        if "def test():" in test_code:
            test_code = test_code.replace("def test():", "def test_main():")
            test_script += test_code + "\n\ndef test_check():\n    test_main()"
        else:
            test_script += test_code
    if config['LOG_CODE']:
        data.log("\n" + "="*30 + " 拼接的测试脚本 " + "="*30, log_file)
        data.log(test_script, log_file)
    data.log("="*70 + "\n", log_file)
    return test_script

def evaluate_sample(config):
    with open(config['LOG_FILE'], "w", encoding="utf-8") as log_file:
        data.log(" Step 1：加载生成样本和HumanEval问题...", log_file)
        task_samples = load_generated_samples(config['SAMPLE_FILE'], log_file)
        problems = data.read_problems(config['PROBLEM_FILE'])
        
        data.log("\n Step 2：开始评估...", log_file)
        # 调用时获取pass_at_k和任务结果字典
        pass_at_k, task_results = calculate_pass_at_k(task_samples, problems, config, log_file=log_file, k=[1, 10])
        
        data.log("\n" + "="*50, log_file)
        data.log("评估结果汇总", log_file)
        data.log("-"*50, log_file)
        data.log(f"总任务数：{len(task_samples)}", log_file)
        
        # 直接从task_results统计通过任务数，无需再次评估
        passed_count = sum(1 for res in task_results.values() if res)
        data.log(f"通过任务数：{passed_count}", log_file)
        
        data.log("\nPass@k指标：", log_file)
        for k_val, score in pass_at_k.items():
            data.log(f"Pass@{k_val}：{score}（{score*100:.2f}%）", log_file)
        data.log("="*50, log_file)
    
    print(f"\n所有输出已同步保存到日志文件：{config['LOG_FILE']}")