from tqdm import tqdm
from langchain_openai import ChatOpenAI
import json
import os
from pathlib import Path
from .data import read_problems, write_jsonl

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger

logger = get_logger()

def init_model(model_path: str, base_url: str, api_key: str, temperature: float = 0, top_p: float = 0.95):
    model = ChatOpenAI(
        model=model_path,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        max_tokens=16384
    )
    return model


def generate_sample_code(state):
    """
    样本生成函数
    """
    #logger.info(f"进入生成样本")
    state_task_id =  state.get("task_id")

    judger_state = state.get("judger", {})
    model = init_model(
        model_path=judger_state['eval_model_path'],
        base_url=judger_state['eval_base_url'],
        api_key=judger_state['eval_api_key'],
        temperature=judger_state['eval_temperature'],
        top_p=judger_state['eval_top_p']
    )

    output_dir = Path(judger_state['output_dir'])
    problem_path = judger_state['eval_problem_path']
    problem_file_name = str(Path(problem_path).stem)
    test_case_path = str(output_dir / str(state_task_id) / "judger" / (problem_file_name + "_sample.jsonl"))

    batch_size = judger_state['eval_batch_size']
    num_samples_per_task = judger_state['eval_case_num']
    task_type = judger_state['eval_task_type']

    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=0.0,
            message=f"{task_type}任务样本合成开始",
            data={"msg": f"任务总数：{total_tasks}|每个任务样本数：{num_samples_per_task}|总样本数：{total_samples}"}
        ).json())
    logger.info(f"\n===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}")
    logger.info(f"每个任务样本数：{num_samples_per_task}")
    logger.info(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0
    cnt = 0

    with tqdm(total=total_samples, desc="生成进度") as pbar:
        for case_id in range(0, total_samples, batch_size):
            prompts = []
            batch_task_id_list = []
            for case_i in range(case_id, min(case_id + batch_size, total_samples), 1):
                prompts.append(problems[all_task_ids[case_i // num_samples_per_task]]['prompt'])
                batch_task_id_list.append(all_task_ids[case_i // num_samples_per_task])
            responses = model.batch(prompts)
            for task_id, response in zip(batch_task_id_list, responses):
                completion = response.content
                samples.append({
                    "task_id": task_id,
                    "completion": completion
                })
                cnt = cnt + 1
                writer = get_stream_writer()
                pbar.update(1)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=round(cnt/total_samples, 1),
                    message=f"{task_type}任务样本合成进度",
                    data={"progress_detail": f"{cnt}/{total_samples}"}
                ).json())
    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")

    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=1.0,
            message=f"{task_type}任务样本合成完成",
            data={
                "msg": f"结果保存为[{test_case_path}]",
                "sample_num": len(samples),
                "sample_save_path": test_case_path
            }
        ).json())
    return test_case_path

def generate_sample_text2sql(state):
    judger_state = state.get("judger", {})
    state_task_id = state.get("task_id")
    logger.info(f"进入生成样本{state_task_id}")
    model = init_model(
        model_path=judger_state['eval_model_path'],
        base_url=judger_state['eval_base_url'],
        api_key=judger_state['eval_api_key'],
        temperature=judger_state['eval_temperature'],
        top_p=judger_state['eval_top_p']
    )
    output_dir = Path(judger_state['output_dir'])
    problem_path = judger_state['eval_problem_path']
    problem_file_name = str(Path(problem_path).stem)
    test_case_path = str(output_dir / str(state_task_id) / "judger" / (problem_file_name + "_sample.jsonl"))

    task_type = judger_state['eval_task_type']
    num_samples_per_task = judger_state['eval_case_num']
    batch_size = judger_state['eval_batch_size']
    text2sql_dir = Path(judger_state['eval_text2sql_dir'])

    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    
    total_samples = total_tasks * num_samples_per_task

    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=0.0,
            message=f"{task_type}任务样本合成开始",
            data={"msg": f"任务总数：{total_tasks}|每个任务样本数：{num_samples_per_task}|总样本数：{total_samples}"}
        ).json())
    logger.info(f"\n===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}")
    logger.info(f"每个任务样本数：{num_samples_per_task}")
    logger.info(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0

    cnt = 0
    with tqdm(total=total_samples, desc="生成进度") as pbar:
        for case_id in range(0, total_samples, batch_size):
            prompts = []
            batch_task_id_list = []
            for case_i in range(case_id, min(case_id + batch_size, total_samples), 1):
                prompts.append(problems[all_task_ids[case_i // num_samples_per_task]]['prompt'])
                batch_task_id_list.append(all_task_ids[case_i // num_samples_per_task])
            responses = model.batch(prompts)
            for task_id, response in zip(batch_task_id_list, responses):
                completion = response.content
                samples.append({
                    "task_id": task_id,
                    "completion": completion,
                    "db_file": str(text2sql_dir / problems[task_id]['db_id'] / f"{problems[task_id]['db_id']}.sqlite"),
                    "question": problems[task_id]["question"],
                    "ground_truth": problems[task_id]["ground_truth"],
                })
                cnt = cnt + 1
                writer = get_stream_writer()
                pbar.update(1)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=round(cnt/total_samples, 1),
                    message=f"{task_type}任务样本合成进度",
                    data={"progress_detail": f"{cnt}/{total_samples}"}
                ).json())

    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")

    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=1.0,
            message=f"{task_type}任务样本合成完成",
            data={
                "msg": f"结果保存为[{test_case_path}]",
                "sample_num": len(samples),
                "sample_save_path": test_case_path
            }
        ).json())
    return test_case_path
