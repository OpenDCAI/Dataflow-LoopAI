from tqdm import tqdm
from langchain_openai import ChatOpenAI
import json
from .data import read_problems, write_jsonl

def filter_code(completion: str) -> str:
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


def generate_sample(model_path, base_url, api_key, temperature, top_p, test_case_path, problem_path, batch_size=20, num_samples_per_task=1):
    # logger.info(f"进入生成样本")

    model = init_model(
        model_path=model_path,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        top_p=top_p
    )
    batch_size = batch_size
    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    print(f"\n===== 开始生成样本 =====")
    print(f"任务总数：{total_tasks}")
    print(f"每个任务样本数：{num_samples_per_task}")
    print(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0
    with tqdm(total=total_samples, desc="生成进度") as pbar:
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
                pbar.update(1)

    write_jsonl(test_case_path, samples)

    print(f"\n===== 生成完成 =====")
    print(f"实际生成样本数：{len(samples)}")
    print(f"保存路径：{test_case_path}")

def generate_sample_sql(model_path, base_url, api_key, temperature, top_p, test_case_path, problem_path, batch_size=20, num_samples_per_task=20):
    #logger.info(f"进入生成样本")

    model = init_model(
        model_path=model_path,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        top_p=top_p
    )
    batch_size = batch_size
    problems = read_problems(problem_path)
    all_task_ids = list(problems.keys())
    total_tasks = len(all_task_ids)
    total_samples = total_tasks * num_samples_per_task

    print(f"\n===== 开始生成样本 =====")
    print(f"任务总数：{total_tasks}")
    print(f"每个任务样本数：{num_samples_per_task}")
    print(f"总样本数：{total_samples}")

    samples = []
    response_ptr = 0
    with tqdm(total=total_samples, desc="生成进度") as pbar:
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
                pbar.update(1)

    write_jsonl(test_case_path, samples)

    print(f"\n===== 生成完成 =====")
    print(f"实际生成样本数：{len(samples)}")
    print(f"保存路径：{test_case_path}")
