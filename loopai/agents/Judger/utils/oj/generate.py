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

def filter_code(completion: str) -> str:
    """
    过滤代码code格式的函数
    Args:
        - completion: 生成的或者是传入的代码文本
    Returns:
        去除最左边的换行，并选取第一次出现连续两次换行符前的内容
    """
    completion = completion.lstrip("\n")
    return completion.split("\n\n")[0]


def init_model(model_path: str, base_url: str, api_key: str, temperature: float = 0, top_p: float = 0.95):
    model = ChatOpenAI(
        model=model_path,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        max_tokens=8192
    )
    return model


def generate_sample(state, num_samples_per_task=1):
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

    model = init_model(
        model_path=state['eval_model_path'],
        base_url=state['eval_base_url'],
        api_key=state['eval_api_key'],
        temperature=state['eval_temperature'],
        top_p=state['eval_top_p']
    )
    test_case_path = state['eval_test_case_path']
    problem_path = state['eval_problem_path']

    batch_size = state['eval_batch_size']

    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    logger.info(f"\n===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}")
    logger.info(f"每个任务样本数：{num_samples_per_task}")
    logger.info(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0
    cnt = 0
    for batch_idx in range(0, total_tasks, batch_size):
        batch_task_ids = all_task_ids[batch_idx:batch_idx + batch_size]
        
        prompts = []
        batch_task_id_list = []
        t = 0
        for task_id in batch_task_ids:
            prompt = problems[task_id]['prompt']
            for i in range(0,num_samples_per_task,1):
                prompts.append(prompt)
                batch_task_id_list.append(batch_task_ids[t])
            t = t + 1
        responses = model.batch(prompts)
        for task_id, response in zip(batch_task_id_list, responses):
            completion = filter_code(response.content)
            samples.append({
                "task_id": task_id,
                "completion": completion
            })
            cnt = cnt + 1
            writer = get_stream_writer()
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=round(cnt/total_samples, 1),
                    message="常规任务样本合成进度",
                    data={"msg": f"{cnt}/{total_samples}"}
                ).json())

    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")

def generate_sample_sql(state, num_samples_per_task=20):
    #logger.info(f"进入生成样本")

    model = init_model(
        model_path=state['eval_model_path'],
        base_url=state['eval_base_url'],
        api_key=state['eval_api_key'],
        temperature=state['eval_temperature'],
        top_p=state['eval_top_p']
    )
    test_case_path = state['eval_test_case_path']
    problem_path = state['eval_problem_path']

    batch_size = state['eval_batch_size']
    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    logger.info(f"\n===== 开始生成样本 =====")
    logger.info(f"任务总数：{total_tasks}")
    logger.info(f"每个任务样本数：{num_samples_per_task}")
    logger.info(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0

    cnt = 0
    # with tqdm(total=total_samples, desc="生成进度") as pbar:
    for batch_idx in range(0, total_tasks, batch_size):
        batch_task_ids = all_task_ids[batch_idx:batch_idx + batch_size]
        
        prompts = []
        batch_task_id_list = []
        t = 0
        for task_id in batch_task_ids:
            prompt = problems[task_id]['prompt']
            for i in range(0,num_samples_per_task,1):
                prompts.append(prompt)
                batch_task_id_list.append(batch_task_ids[t])
            t = t + 1
        responses = model.batch(prompts,config={"max_tokens": 32768})
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
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=round(cnt/total_samples, 1),
                    message="SQL任务样本合成进度",
                    data={"msg": f"{cnt}/{total_samples}"}
                ).json())
                #pbar.update(1)

    write_jsonl(test_case_path, samples)

    logger.info(f"\n===== 生成完成 =====")
    logger.info(f"实际生成样本数：{len(samples)}")
    logger.info(f"保存路径：{test_case_path}")
