# -*- coding: utf-8 -*-
"""Standalone code/text2sql sample generation — no LangGraph dependency.

Extracted from ``loopai.agents.Judger.utils.oj.generate``,
replaced ``get_stream_writer()`` with a passed-in ``writer`` parameter.
"""

from pathlib import Path
from typing import Any, Dict

from tqdm import tqdm
from langchain_openai import ChatOpenAI

from loopai.agents.Judger.utils.oj.data import read_problems, write_jsonl
from loopai.common.event_tool import StreamEvent
from loopai.logger import get_logger

logger = get_logger()


def _init_model(model_path: str, base_url: str, api_key: str,
                temperature: float = 0, top_p: float = 0.95):
    return ChatOpenAI(
        model=model_path,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        max_tokens=16384,
    )


def run_generate_code(state: Dict[str, Any], writer) -> str:
    """生成代码样本，进度事件通过 ``writer`` 发送。"""
    state_task_id = state.get("task_id")
    judger_state = state.get("judger", {})

    model = _init_model(
        model_path=judger_state["eval_model_path"],
        base_url=judger_state["eval_base_url"],
        api_key=judger_state.get("eval_api_key", "EMPTY"),
        temperature=judger_state["eval_temperature"],
        top_p=judger_state["eval_top_p"],
    )
    logger.info(f"模型路径:-> base_url: {judger_state['eval_base_url']}")

    output_dir = Path(state.get("output_dir"))
    problem_path = judger_state["eval_problem_path"]
    problem_file_name = Path(problem_path).stem
    test_case_path = str(
        output_dir / str(state_task_id) / "judger" / f"{problem_file_name}_sample.jsonl"
    )

    batch_size = judger_state["eval_batch_size"]
    num_samples_per_task = judger_state["eval_case_num"]
    task_type = judger_state["eval_task_type"]

    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    writer(StreamEvent(
        current=state.get("current", "judger"), progress=0.0,
        message=f"{task_type}任务样本合成开始",
        data={"total_tasks": total_tasks, "num_samples_per_task": num_samples_per_task,
              "total_samples": total_samples}))

    logger.info(f"===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}  每个任务样本数：{num_samples_per_task}  总样本数：{total_samples}")

    samples = []
    cnt = 0
    with tqdm(total=total_samples, desc="生成进度") as pbar:
        for case_id in range(0, total_samples, batch_size):
            prompts = []
            batch_task_id_list = []
            for case_i in range(case_id, min(case_id + batch_size, total_samples), 1):
                prompts.append(
                    problems[all_task_ids[case_i // num_samples_per_task]]["prompt"]
                )
                batch_task_id_list.append(
                    all_task_ids[case_i // num_samples_per_task]
                )
            responses = model.batch(prompts)
            for task_id, response in zip(batch_task_id_list, responses):
                samples.append({"task_id": task_id, "completion": response.content})
                cnt += 1
                pbar.update(1)
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(cnt / total_samples, 1),
                message=f"{task_type}任务样本合成进度",
                data={"progress_detail": f"{cnt}/{total_samples}"}))

    write_jsonl(test_case_path, samples)
    logger.info(f"===== 生成完成 ===== 样本数：{len(samples)}  路径：{test_case_path}")

    writer(StreamEvent(
        current=state.get("current", "judger"), progress=1.0, message=f"{task_type}任务样本合成完成",
        data={"sample_num": len(samples), "sample_save_path": test_case_path}))
    return test_case_path


def run_generate_text2sql(state: Dict[str, Any], writer) -> str:
    """生成 text2sql 样本，进度事件通过 ``writer`` 发送。"""
    judger_state = state.get("judger", {})
    state_task_id = state.get("task_id")

    model = _init_model(
        model_path=judger_state["eval_model_path"],
        base_url=judger_state["eval_base_url"],
        api_key="EMPTY",
        temperature=judger_state["eval_temperature"],
        top_p=judger_state["eval_top_p"],
    )

    output_dir = Path(state.get("output_dir"))
    problem_path = judger_state["eval_problem_path"]
    problem_file_name = Path(problem_path).stem
    test_case_path = str(
        output_dir / str(state_task_id) / "judger" / f"{problem_file_name}_sample.jsonl"
    )

    task_type = judger_state["eval_task_type"]
    num_samples_per_task = judger_state["eval_case_num"]
    batch_size = judger_state["eval_batch_size"]
    text2sql_dir = Path(judger_state["eval_text2sql_dir"])

    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    writer(StreamEvent(
        current=state.get("current", "judger"), progress=0.0,
        message=f"{task_type}任务样本合成开始",
        data={"total_tasks": total_tasks, "num_samples_per_task": num_samples_per_task,
              "total_samples": total_samples}))

    logger.info(f"===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}  每个任务样本数：{num_samples_per_task}  总样本数：{total_samples}")

    samples = []
    cnt = 0
    with tqdm(total=total_samples, desc="生成进度") as pbar:
        for case_id in range(0, total_samples, batch_size):
            prompts = []
            batch_task_id_list = []
            for case_i in range(case_id, min(case_id + batch_size, total_samples), 1):
                prompts.append(
                    problems[all_task_ids[case_i // num_samples_per_task]]["prompt"]
                )
                batch_task_id_list.append(
                    all_task_ids[case_i // num_samples_per_task]
                )
            responses = model.batch(prompts)
            for task_id, response in zip(batch_task_id_list, responses):
                samples.append({
                    "task_id": task_id,
                    "completion": response.content,
                    "db_file": str(text2sql_dir / problems[task_id]["db_id"]
                                   / f"{problems[task_id]['db_id']}.sqlite"),
                    "question": problems[task_id]["question"],
                    "ground_truth": problems[task_id]["ground_truth"],
                })
                cnt += 1
                pbar.update(1)
            writer(StreamEvent(
                current=state.get("current", "judger"),
                progress=round(cnt / total_samples, 1),
                message=f"{task_type}任务样本合成进度",
                data={"progress_detail": f"{cnt}/{total_samples}"}))

    write_jsonl(test_case_path, samples)
    logger.info(f"===== 生成完成 ===== 样本数：{len(samples)}  路径：{test_case_path}")

    writer(StreamEvent(
        current=state.get("current", "judger"), progress=1.0, message=f"{task_type}任务样本合成完成",
        data={"sample_num": len(samples), "sample_save_path": test_case_path}))
    return test_case_path
