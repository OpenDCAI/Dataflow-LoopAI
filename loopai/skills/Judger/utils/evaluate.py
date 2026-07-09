# -*- coding: utf-8 -*-
"""Standalone code/text2sql evaluation — no LangGraph dependency.

Extracted from ``loopai.agents.Judger.utils.oj.evaluate``,
replaced ``get_stream_writer()`` with a passed-in ``writer`` parameter.
"""

import itertools
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union

import numpy as np
import tqdm

from loopai.skills.Judger.utils.data import write_jsonl, stream_jsonl, read_problems
from loopai.skills.Judger.utils.execution import check_correctness
from loopai.skills.Judger.utils.execution_sql import compare_sql_wrapper
from loopai.common.event_tool import StreamEvent
from loopai.logger import get_logger

logger = get_logger()

K = "1,10,100"
N_WORKERS = 1
TIMEOUT = 3.0


def estimate_pass_at_k(
    num_samples: Union[int, List[int], np.ndarray],
    num_correct: Union[List[int], np.ndarray],
    k: int,
) -> np.ndarray:
    def estimator(n: int, c: int, k: int) -> float:
        if n - c < k:
            return 1.0
        return 1.0 - np.prod(1.0 - k / np.arange(n - c + 1, n + 1))

    if isinstance(num_samples, int):
        num_samples_it = itertools.repeat(num_samples, len(num_correct))
    else:
        assert len(num_samples) == len(num_correct)
        num_samples_it = iter(num_samples)
    return np.array([estimator(int(n), int(c), k) for n, c in zip(num_samples_it, num_correct)])


def _calculate_pass_at_k(k, results):
    total, correct = [], []
    for result in results.values():
        result.sort()
        passed = [r[1]["passed"] for r in result]
        total.append(len(passed))
        correct.append(sum(passed))
    total = np.array(total)
    correct = np.array(correct)
    return {f"pass@{_k}": estimate_pass_at_k(total, correct, _k).mean()
            for _k in k if (total >= _k).all()}


def _write_evaluate_log(pass_at_k, result_path, test_case_path, problem_path):
    file_path = Path(result_path).parent / "log.txt"
    lines = [f"{key}: {value:.4f}" for key, value in pass_at_k.items()]
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"\n\n--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        f.write(f"\nSample:{test_case_path}")
        f.write(f"\nProblem:{problem_path}")
        f.write(f"\nResult:{result_path}")
        f.write("\n" + "\n".join(lines))


def run_evaluate_code(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """评测代码样本，返回 pass@k 和 result_path。"""
    state_task_id = state.get("task_id")
    judger_state = state.get("judger", {})
    output_dir = Path(state.get("output_dir"))
    problem_path = judger_state["eval_problem_path"]
    problem_file_name = Path(problem_path).stem
    test_case_path = str(
        output_dir / str(state_task_id) / "judger" / writer.version_id / f"{problem_file_name}_sample.jsonl"
    )
    result_path = str(
        output_dir / str(state_task_id) / "judger" / writer.version_id / f"{problem_file_name}_result.jsonl"
    )
    case_num = judger_state.get("eval_case_num", 10)
    task_type = judger_state["eval_task_type"]

    k = list(map(int, K.split(",")))
    problems = read_problems(problem_path)
    total_samples = len(problems) * case_num

    with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = []
        completion_id = Counter()
        n_samples = 0
        results = defaultdict(list)

        logger.info("Reading samples...")
        for sample in tqdm.tqdm(stream_jsonl(test_case_path)):
            task_id = sample["task_id"]
            completion = sample["completion"]
            args = (problems[task_id], completion, TIMEOUT, completion_id[task_id])
            futures.append(executor.submit(check_correctness, *args))
            completion_id[task_id] += 1
            n_samples += 1
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(n_samples / total_samples, 1),
                message=f"{task_type}任务样本提交进度",
                data={"progress_detail": f"{n_samples}/{total_samples}"}))

        assert len(completion_id) == len(problems), "Some problems are not attempted."

        n_samples2 = 0
        logger.info("Running test suites...")
        for future in tqdm.tqdm(as_completed(futures), total=len(futures)):
            result = future.result()
            results[result["task_id"]].append((result["completion_id"], result))
            n_samples2 += 1
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(n_samples2 / total_samples, 1),
                message=f"{task_type}任务样本评测进度",
                data={"progress_detail": f"{n_samples2}/{total_samples}"}))

    pass_at_k = _calculate_pass_at_k(k, results)

    def combine_results():
        for sample in stream_jsonl(test_case_path):
            task_id = sample["task_id"]
            r = results[task_id].pop(0)
            sample["result"] = r[1]["result"]
            sample["passed"] = r[1]["passed"]
            yield sample

    logger.info(f"Writing results to {result_path}...")
    write_jsonl(result_path, tqdm.tqdm(combine_results(), total=n_samples))
    _write_evaluate_log(pass_at_k, result_path, test_case_path, problem_path)

    return {"pass_at_k": pass_at_k, "result_path": result_path}


def run_evaluate_text2sql(state: Dict[str, Any], writer) -> Dict[str, Any]:
    """评测 text2sql 样本，返回 pass@k 和 result_path。"""
    state_task_id = state.get("task_id")
    judger_state = state.get("judger", {})
    output_dir = Path(state.get("output_dir"))
    problem_path = judger_state["eval_problem_path"]
    problem_file_name = Path(problem_path).stem
    test_case_path = str(
        output_dir / str(state_task_id) / "judger" / writer.version_id / f"{problem_file_name}_sample.jsonl"
    )
    result_path = str(
        output_dir / str(state_task_id) / "judger" / writer.version_id / f"{problem_file_name}_result.jsonl"
    )
    case_num = judger_state.get("eval_case_num", 10)
    task_type = judger_state["eval_task_type"]

    k = list(map(int, K.split(",")))
    problems = read_problems(problem_path)
    total_samples = len(problems) * case_num

    with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
        futures = []
        completion_id = Counter()
        n_samples = 0
        results = defaultdict(list)

        logger.info("Reading samples...")
        for sample in tqdm.tqdm(stream_jsonl(test_case_path)):
            task_id = sample["task_id"]
            db_file = sample["db_file"]
            question = sample["question"]
            ground_truth = sample["ground_truth"]
            pred_sql = sample["completion"]
            args = (
                (task_id, db_file, question, ground_truth, pred_sql),
                TIMEOUT,
                completion_id[task_id],
            )
            futures.append(executor.submit(compare_sql_wrapper, *args))
            completion_id[task_id] += 1
            n_samples += 1
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(n_samples / total_samples, 1),
                message=f"{task_type}任务样本提交进度",
                data={"progress_detail": f"{n_samples}/{total_samples}"}))

        assert len(completion_id) == len(problems), "Some problems are not attempted."

        n_samples2 = 0
        logger.info("Running test suites...")
        for future in tqdm.tqdm(as_completed(futures), total=len(futures)):
            result = future.result()
            results[result["task_id"]].append((result["completion_id"], result))
            n_samples2 += 1
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(n_samples2 / total_samples, 1),
                message=f"{task_type}任务样本评测进度",
                data={"progress_detail": f"{n_samples2}/{total_samples}"}))

    pass_at_k = _calculate_pass_at_k(k, results)

    def combine_results():
        for sample in stream_jsonl(test_case_path):
            task_id = sample["task_id"]
            r = results[task_id].pop(0)
            sample["result"] = r[1]["result"]
            sample["passed"] = r[1]["passed"]
            yield sample

    logger.info(f"Writing results to {result_path}...")
    write_jsonl(result_path, tqdm.tqdm(combine_results(), total=n_samples))
    _write_evaluate_log(pass_at_k, result_path, test_case_path, problem_path)

    return {"pass_at_k": pass_at_k, "result_path": result_path}
