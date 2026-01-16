from tqdm import tqdm
from langchain_openai import ChatOpenAI
import json
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


def generate_sample(state):
    """
    样本生成函数
    关键Args:
        - model_path: 模型路径
        - base_url: 对应eval_base_url
        - test_case_path: 样例结果保存路径
        - problem_path: 问题集文件路径
        - num_samples_per_task: 每个生成的样本数
    """
    logger.info(f"进入生成样本")
    judger_state = state.get("judger", {})
    model = init_model(
        model_path=judger_state['eval_model_path'],
        base_url=judger_state['eval_base_url'],
        api_key=judger_state['eval_api_key'],
        temperature=judger_state['eval_temperature'],
        top_p=judger_state['eval_top_p']
    )
    test_case_path = judger_state['eval_test_case_path']
    problem_path = judger_state['eval_problem_path']
    batch_size = judger_state['eval_batch_size']
    num_samples_per_task = judger_state['eval_case_num']
    
    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=0.0,
            message="code任务样本合成开始",
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
                    message="code任务样本合成进度",
                    data={"progress_detail": f"{cnt}/{total_samples}"}
                ).json())
    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")

    return {"sample_num":len(samples),"sample_save_path":test_case_path}

def generate_sample_sql(state):
    #logger.info(f"进入生成样本")
    judger_state = state.get("judger", {})

    model = init_model(
        model_path=judger_state['eval_model_path'],
        base_url=judger_state['eval_base_url'],
        api_key=judger_state['eval_api_key'],
        temperature=judger_state['eval_temperature'],
        top_p=judger_state['eval_top_p']
    )
    test_case_path = judger_state['eval_test_case_path']
    problem_path = judger_state['eval_problem_path']
    num_samples_per_task = judger_state['eval_case_num']
    batch_size = judger_state['eval_batch_size']
    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    
    total_samples = total_tasks * num_samples_per_task

    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=state['current'],
            progress=0.0,
            message="code任务样本合成开始",
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
                    "db_file": "/root/brjverl/dataflow/examples/scripts/database/"+ problems[task_id]['db_id'] + f"/{problems[task_id]['db_id']}.sqlite",
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
                    message="text2sql任务样本合成进度",
                    data={"progress_detail": f"{cnt}/{total_samples}"}
                ).json())

    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")
    return {"sample_num":len(samples),"sample_save_path":test_case_path}   
