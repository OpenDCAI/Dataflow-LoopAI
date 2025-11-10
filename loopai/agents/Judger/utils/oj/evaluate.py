from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Union, Iterable, Dict
import itertools
import os
from datetime import datetime, date, time
import numpy as np
import tqdm
from .data import write_jsonl, stream_jsonl, read_problems
from .execution import check_correctness

def estimate_pass_at_k(
    num_samples: Union[int, List[int], np.ndarray],
    num_correct: Union[List[int], np.ndarray],
    k: int
) -> np.ndarray:
    """
    Estimates pass@k of each problem and returns them in an array.
    """

    def estimator(n: int, c: int, k: int) -> float:
        """
        Calculates 1 - comb(n - c, k) / comb(n, k).
        """
        if n - c < k:
            return 1.0
        return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))

    if isinstance(num_samples, int):
        num_samples_it = itertools.repeat(num_samples, len(num_correct))
    else:
        assert len(num_samples) == len(num_correct)
        num_samples_it = iter(num_samples)

    return np.array([estimator(int(n), int(c), k) for n, c in zip(num_samples_it, num_correct)])

def evaluate_functional_correctness(K, n_workers, timeout, test_case_path, problem_path, result_path, test_code_function_name, entry_point_function_name, test_function_name):
    """
    Evaluates the functional correctness of generated samples, and writes
    results to f"{sample_file}_results.jsonl.gz"
    """
    sample_file = test_case_path
    problem_file = problem_path
    k = list(map(int, K.split(",")))
    n_workers = n_workers
    timeout = timeout

    problems = read_problems(problem_file)

    # Check the generated samples against test suites.
    with ThreadPoolExecutor(max_workers=n_workers) as executor:

        futures = []
        completion_id = Counter()
        n_samples = 0
        results = defaultdict(list)

        print("Reading samples...")
        for sample in tqdm.tqdm(stream_jsonl(sample_file)):
            task_id = sample["task_id"]
            completion = sample["completion"]
            args = (problems[task_id], completion, timeout, test_code_function_name, entry_point_function_name, test_function_name, completion_id[task_id])
            future = executor.submit(check_correctness, *args)
            futures.append(future)
            completion_id[task_id] += 1
            n_samples += 1

        assert len(completion_id) == len(problems), "Some problems are not attempted."

        print("Running test suites...")
        for future in tqdm.tqdm(as_completed(futures), total=len(futures)):
            result = future.result()
            results[result["task_id"]].append((result["completion_id"], result))

    # Calculate pass@k.
    total, correct = [], []
    for result in results.values():
        result.sort()
        passed = [r[1]["passed"] for r in result]
        total.append(len(passed))
        correct.append(sum(passed))
    total = np.array(total)
    correct = np.array(correct)

    ks = k
    pass_at_k = {f"pass@{k}": estimate_pass_at_k(total, correct, k).mean()
                 for k in ks if (total >= k).all()}

    # Finally, save the results in one file:
    def combine_results():
        for sample in stream_jsonl(sample_file):
            task_id = sample["task_id"]
            result = results[task_id].pop(0)
            sample["result"] = result[1]["result"]
            sample["passed"] = result[1]["passed"]
            yield sample

    out_file = result_path
    print(f"Writing results to {out_file}...")
    write_jsonl(out_file, tqdm.tqdm(combine_results(), total=n_samples))

    file_path = os.path.join(os.path.dirname(out_file),"log.txt")
    pass_at_k_text = "\n".join([
        f"{key}: {value:.4f}"  # 控制小数位数，按需修改
        for key, value in pass_at_k.items()
    ])
    with open(file_path, mode='a', encoding='utf-8') as f:
        f.write("\n\n--- " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ---")
        f.write("\nSample:" + test_case_path)
        f.write("\nProblem:" + problem_path)
        f.write("\nResult:" + result_path)
        f.write("\n" + pass_at_k_text)

    return pass_at_k

def evaluate_sample(K, n_workers, timeout, test_case_path, problem_path, result_path, test_code_function_name, entry_point_function_name, test_function_name):
    """
    Evaluates the functional correctness of generated samples, and writes
    results to f"{sample_file}_results.jsonl.gz"
    """
    results = evaluate_functional_correctness(K, n_workers, timeout, test_case_path, problem_path, result_path, test_code_function_name, entry_point_function_name, test_function_name)
    print(results)
    return results
