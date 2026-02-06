"""
训练日志解析工具
用于解析训练日志中的进度信息
"""

import os
import re
from typing import Optional, Tuple, Dict


class TrainingLogParser:
    """训练日志解析器"""
    
    def __init__(self):
        # 匹配训练进度的正则表达式
        # 格式：| 101/339 [01:48<21:21,  5.39s/it]
        self.progress_pattern = re.compile(
            r'\|\s*(\d+)/(\d+)\s*\[(\d{2}:\d{2})<(\d{2}:\d{2}),\s*[\d.]+s/it\]'
        )
        self.total_steps = None  # 用于区分训练和评估进度
        
    def parse_training_progress(self, log_path: str) -> Optional[Dict[str, str]]:
        """
        解析训练日志中的进度信息
        
        Args:
            log_path: 日志文件路径
            
        Returns:
            包含进度信息的字典，格式：
            {
                'current_step': '101',
                'total_steps': '339', 
                'elapsed_time': '01:48',
                'remaining_time': '21:21',
                'progress_text': '101/339',
                'time_text': '01:48<21:21'
            }
            如果没有找到进度信息返回 None
        """
        
        if not os.path.exists(log_path):
            return None
            
        try:
            # 逆序读取文件，获取最新的进度信息
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            # 从后往前查找进度信息
            for line in reversed(lines):
                match = self.progress_pattern.search(line)
                if match:
                    current_step = match.group(1)
                    total_steps = match.group(2)
                    elapsed_time = match.group(3)
                    remaining_time = match.group(4)
                    
                    # 如果是第一次解析，记录总步数用于区分训练和评估
                    if self.total_steps is None:
                        self.total_steps = total_steps
                    
                    # 只返回与训练总步数匹配的进度（忽略评估进度）
                    if total_steps == self.total_steps:
                        return {
                            'current_step': current_step,
                            'total_steps': total_steps,
                            'elapsed_time': elapsed_time,
                            'remaining_time': remaining_time,
                            'progress_text': f"{current_step}/{total_steps}",
                            'time_text': f"{elapsed_time}<{remaining_time}"
                        }
                        
        except Exception as e:
            print(f"解析训练日志失败: {e}")
            return None
            
        return None
        
    def get_progress_percentage(self, progress_info: Dict[str, str]) -> float:
        """
        根据进度信息计算百分比
        
        Args:
            progress_info: parse_training_progress 返回的进度信息
            
        Returns:
            进度百分比 (0.0 - 1.0)
        """
        
        if not progress_info:
            return 0.0
            
        try:
            current = int(progress_info['current_step'])
            total = int(progress_info['total_steps'])
            return min(current / total, 1.0) if total > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    
    def reset(self):
        """重置解析器状态，用于新的训练任务"""
        self.total_steps = None


def parse_task_training_progress(task_id: str, output_dir: str = "./api/logs") -> Optional[Dict[str, str]]:
    """
    解析指定任务的训练进度
    
    Args:
        task_id: 任务ID
        output_dir: 输出目录，默认为 ./api/logs

    Returns:
        进度信息字典，如果没有找到则返回 None
    """
    
    log_path = os.path.join(output_dir, f"{task_id}.log")
    parser = TrainingLogParser()
    return parser.parse_training_progress(log_path)


def get_task_progress_percentage(task_id: str, output_dir: str = "./output") -> float:
    """
    获取指定任务的训练进度百分比
    
    Args:
        task_id: 任务ID
        output_dir: 输出目录，默认为 ./output
        
    Returns:
        进度百分比 (0.0 - 1.0)
    """
    
    progress_info = parse_task_training_progress(task_id, output_dir)
    if progress_info:
        parser = TrainingLogParser()
        return parser.get_progress_percentage(progress_info)
    return 0.0


# 示例用法
if __name__ == "__main__":
    # 测试用例
    parser = TrainingLogParser()
    
    # 假设有一个测试日志文件
    test_log_content = """
[INFO|2026-01-27 18:30:06] llamafactory.launcher:143 >> Initializing 4 distributed tasks at: 127.0.0.1:40807
W0127 18:30:14.158000 378262 torch/distributed/run.py:774] 
W0127 18:30:14.158000 378262 torch/distributed/run.py:774] *****************************************
W0127 18:30:14.158000 378262 torch/distributed/run.py:774] Setting OMP_NUM_THREADS environment variable for each process to be 1 in default, to avoid your system being overloaded, please further tune the variable for optimal performance in your application as needed. 
W0127 18:30:14.158000 378262 torch/distributed/run.py:774] *****************************************
[2026-01-27 18:32:03,005] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[2026-01-27 18:32:03,005] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[2026-01-27 18:32:03,005] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
[2026-01-27 18:32:03,005] [INFO] [real_accelerator.py:254:get_accelerator] Setting ds_accelerator to cuda (auto detect)
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/jieba/_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
  import pkg_resources
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/jieba/_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
  import pkg_resources
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/jieba/_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
  import pkg_resources
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/jieba/_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
  import pkg_resources
[2026-01-27 18:33:23,642] [INFO] [comm.py:669:init_distributed] cdb=None
[2026-01-27 18:33:23,651] [INFO] [comm.py:669:init_distributed] cdb=None
[2026-01-27 18:33:23,657] [INFO] [comm.py:669:init_distributed] cdb=None
[2026-01-27 18:33:23,659] [INFO] [comm.py:669:init_distributed] cdb=None
[2026-01-27 18:33:23,660] [INFO] [comm.py:700:init_distributed] Initializing TorchBackend in DeepSpeed with backend nccl
[W127 18:33:23.970348032 ProcessGroupNCCL.cpp:981] Warning: TORCH_NCCL_AVOID_RECORD_STREAMS is the default now, this environment variable is thus deprecated. (function operator())
[W127 18:33:23.980831752 ProcessGroupNCCL.cpp:981] Warning: TORCH_NCCL_AVOID_RECORD_STREAMS is the default now, this environment variable is thus deprecated. (function operator())
[W127 18:33:23.997511676 ProcessGroupNCCL.cpp:981] Warning: TORCH_NCCL_AVOID_RECORD_STREAMS is the default now, this environment variable is thus deprecated. (function operator())
[W127 18:33:23.997523636 ProcessGroupNCCL.cpp:981] Warning: TORCH_NCCL_AVOID_RECORD_STREAMS is the default now, this environment variable is thus deprecated. (function operator())
[INFO|2026-01-27 18:33:24] llamafactory.hparams.parser:423 >> Process rank: 2, world size: 4, device: cuda:2, distributed training: True, compute dtype: torch.bfloat16
[INFO|2026-01-27 18:33:24] llamafactory.hparams.parser:423 >> Process rank: 0, world size: 4, device: cuda:0, distributed training: True, compute dtype: torch.bfloat16
[INFO|2026-01-27 18:33:24] llamafactory.hparams.parser:423 >> Process rank: 3, world size: 4, device: cuda:3, distributed training: True, compute dtype: torch.bfloat16
[INFO|2026-01-27 18:33:24] llamafactory.hparams.parser:423 >> Process rank: 1, world size: 4, device: cuda:1, distributed training: True, compute dtype: torch.bfloat16
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,372 >> loading file vocab.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,372 >> loading file merges.txt
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,372 >> loading file tokenizer.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,372 >> loading file added_tokens.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,373 >> loading file special_tokens_map.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,373 >> loading file tokenizer_config.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,373 >> loading file chat_template.jinja
[INFO|tokenization_utils_base.py:2364] 2026-01-27 18:33:26,803 >> Special tokens have been added in the vocabulary, make sure the associated word embeddings are fine-tuned or trained.
[INFO|configuration_utils.py:763] 2026-01-27 18:33:26,812 >> loading configuration file /jizhicfs/hymiezhao/models/Qwen2.5-1.5B/config.json
[INFO|configuration_utils.py:839] 2026-01-27 18:33:26,827 >> Model config Qwen2Config {
  "architectures": [
    "Qwen2ForCausalLM"
  ],
  "attention_dropout": 0.0,
  "bos_token_id": 151643,
  "dtype": "bfloat16",
  "eos_token_id": 151643,
  "hidden_act": "silu",
  "hidden_size": 1536,
  "initializer_range": 0.02,
  "intermediate_size": 8960,
  "layer_types": [
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention"
  ],
  "max_position_embeddings": 131072,
  "max_window_layers": 28,
  "model_type": "qwen2",
  "num_attention_heads": 12,
  "num_hidden_layers": 28,
  "num_key_value_heads": 2,
  "rms_norm_eps": 1e-06,
  "rope_scaling": null,
  "rope_theta": 1000000.0,
  "sliding_window": null,
  "tie_word_embeddings": true,
  "transformers_version": "4.57.0",
  "use_cache": true,
  "use_mrope": false,
  "use_sliding_window": false,
  "vocab_size": 151936
}

[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,836 >> loading file vocab.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,836 >> loading file merges.txt
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,837 >> loading file tokenizer.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,837 >> loading file added_tokens.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,837 >> loading file special_tokens_map.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,837 >> loading file tokenizer_config.json
[INFO|tokenization_utils_base.py:2093] 2026-01-27 18:33:26,837 >> loading file chat_template.jinja
[INFO|tokenization_utils_base.py:2364] 2026-01-27 18:33:27,015 >> Special tokens have been added in the vocabulary, make sure the associated word embeddings are fine-tuned or trained.
[INFO|2026-01-27 18:33:27] llamafactory.data.template:143 >> Replace eos token: <|im_end|>.
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/torch/distributed/distributed_c10d.py:4807: UserWarning: No device id is provided via `init_process_group` or `barrier `. Using the current device set by the user. 
  warnings.warn(  # warn only once
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/torch/distributed/distributed_c10d.py:4807: UserWarning: No device id is provided via `init_process_group` or `barrier `. Using the current device set by the user. 
  warnings.warn(  # warn only once
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/torch/distributed/distributed_c10d.py:4807: UserWarning: No device id is provided via `init_process_group` or `barrier `. Using the current device set by the user. 
  warnings.warn(  # warn only once
[INFO|2026-01-27 18:33:27] llamafactory.data.loader:143 >> Loading dataset alpaca_en_demo.json...
`trust_remote_code` is not supported anymore.
Please check that the Hugging Face dataset 'json' isn't based on a loading script and remove `trust_remote_code`.
If the dataset is based on a loading script, please ask the dataset author to remove it and convert it to a standard format like Parquet.
[rank2]:[W127 18:33:27.353727618 ProcessGroupNCCL.cpp:5023] [PG ID 0 PG GUID 0 Rank 2]  using GPU 2 as device used by this process is currently unknown. This can potentially cause a hang if this rank to GPU mapping is incorrect. You can specify device_id in init_process_group() to force use of a particular device.
[rank1]:[W127 18:33:27.353732878 ProcessGroupNCCL.cpp:5023] [PG ID 0 PG GUID 0 Rank 1]  using GPU 1 as device used by this process is currently unknown. This can potentially cause a hang if this rank to GPU mapping is incorrect. You can specify device_id in init_process_group() to force use of a particular device.
[rank3]:[W127 18:33:27.353741598 ProcessGroupNCCL.cpp:5023] [PG ID 0 PG GUID 0 Rank 3]  using GPU 3 as device used by this process is currently unknown. This can potentially cause a hang if this rank to GPU mapping is incorrect. You can specify device_id in init_process_group() to force use of a particular device.

Converting format of dataset (num_proc=16): 100%|██████████████████████████████████████████████████████████| 999/999 [00:00<?, ? examples/s]
Converting format of dataset (num_proc=16): 1062 examples [00:00, 321.65 examples/s]                                                        
Converting format of dataset (num_proc=16): 1998 examples [00:00, 2560.70 examples/s]
/jizhicfs/hymiezhao/miniconda3/envs/llamafactory/lib/python3.10/site-packages/torch/distributed/distributed_c10d.py:4807: UserWarning: No device id is provided via `init_process_group` or `barrier `. Using the current device set by the user. 
  warnings.warn(  # warn only once
[rank0]:[W127 18:33:29.651218484 ProcessGroupNCCL.cpp:5023] [PG ID 0 PG GUID 0 Rank 0]  using GPU 0 as device used by this process is currently unknown. This can potentially cause a hang if this rank to GPU mapping is incorrect. You can specify device_id in init_process_group() to force use of a particular device.
`trust_remote_code` is not supported anymore.
Please check that the Hugging Face dataset 'json' isn't based on a loading script and remove `trust_remote_code`.
If the dataset is based on a loading script, please ask the dataset author to remove it and convert it to a standard format like Parquet.
`trust_remote_code` is not supported anymore.
Please check that the Hugging Face dataset 'json' isn't based on a loading script and remove `trust_remote_code`.
If the dataset is based on a loading script, please ask the dataset author to remove it and convert it to a standard format like Parquet.
`trust_remote_code` is not supported anymore.
Please check that the Hugging Face dataset 'json' isn't based on a loading script and remove `trust_remote_code`.
If the dataset is based on a loading script, please ask the dataset author to remove it and convert it to a standard format like Parquet.

Running tokenizer on dataset (num_proc=16): 100%|██████████████████████████████████████████████████████████| 999/999 [00:00<?, ? examples/s]
Running tokenizer on dataset (num_proc=16): 1062 examples [00:00, 93.09 examples/s]                                                         
Running tokenizer on dataset (num_proc=16): 1377 examples [00:00, 578.59 examples/s]
Running tokenizer on dataset (num_proc=16): 1564 examples [00:00, 748.81 examples/s]
Running tokenizer on dataset (num_proc=16): 1750 examples [00:01, 908.92 examples/s]
Running tokenizer on dataset (num_proc=16): 1936 examples [00:01, 996.68 examples/s]
Running tokenizer on dataset (num_proc=16): 1998 examples [00:01, 666.86 examples/s]
training example:
input_ids:
[151644, 8948, 198, 2610, 525, 1207, 16948, 11, 3465, 553, 54364, 14817, 13, 1446, 525, 264, 10950, 17847, 13, 151645, 198, 151644, 872, 198, 74785, 264, 1882, 315, 3259, 1884, 20352, 13, 151645, 198, 151644, 77091, 198, 42246, 1884, 20352, 374, 458, 4135, 323, 17923, 1882, 0, 5692, 525, 3019, 14319, 29208, 11221, 389, 1246, 311, 1281, 1105, 1447, 16, 13, 1634, 15790, 697, 13966, 13, 1752, 6770, 1884, 20352, 11, 498, 3278, 1184, 25, 220, 16, 10525, 678, 58238, 19828, 11, 220, 17, 18805, 11, 220, 16, 14, 17, 10525, 14074, 11, 220, 16, 14, 17, 10525, 3015, 11, 220, 16, 14, 19, 41284, 12021, 11, 323, 220, 17, 55488, 49359, 14100, 382, 17, 13, 19219, 279, 8745, 25, 758, 264, 3460, 26792, 19212, 11, 40659, 3786, 279, 19828, 323, 279, 18805, 13, 21794, 1832, 912, 279, 14074, 323, 3015, 11, 53954, 14971, 311, 5978, 429, 1052, 525, 902, 326, 11793, 13, 2691, 12021, 323, 49359, 14100, 11, 323, 6514, 1632, 382, 18, 13, 6771, 279, 8745, 2732, 25, 1416, 498, 646, 11, 1077, 279, 8745, 2444, 369, 458, 6460, 476, 773, 13, 1096, 686, 1492, 279, 19828, 311, 34306, 279, 14473, 323, 1281, 279, 1884, 20352, 803, 27582, 382, 19, 13, 26070, 697, 7215, 25, 4968, 19963, 264, 2477, 5477, 865, 7215, 916, 11051, 8628, 13, 8658, 398, 14100, 279, 7215, 476, 990, 17233, 22899, 311, 5358, 279, 1884, 20352, 504, 36972, 382, 20, 13, 25968, 279, 8745, 25, 12091, 264, 57625, 273, 476, 264, 28990, 10525, 11, 4914, 264, 2613, 3311, 315, 8745, 320, 9096, 220, 16, 14, 19, 10525, 8, 8630, 279, 4126, 315, 279, 7215, 13, 68252, 36200, 279, 7215, 304, 264, 26931, 11379, 311, 8865, 279, 8745, 41047, 323, 98431, 916, 279, 5622, 315, 279, 7215, 382, 21, 13, 12514, 279, 1884, 375, 25, 12514, 279, 1884, 375, 369, 220, 16, 12, 17, 4420, 3080, 279, 5622, 374, 33404, 20748, 13, 10627, 3641, 82742, 279, 12822, 448, 264, 62883, 5607, 323, 18287, 279, 1884, 375, 916, 311, 4296, 279, 1008, 3108, 369, 2441, 9383, 382, 22, 13, 10783, 323, 13153, 25, 479, 4402, 14983, 279, 1884, 375, 8630, 264, 11968, 11, 323, 1221, 13153, 279, 1882, 448, 279, 9664, 8745, 13, 19881, 311, 312, 1455, 6207, 279, 7215, 1948, 1817, 1884, 375, 421, 5871, 382, 23, 13, 21979, 323, 8683, 25, 21979, 697, 29105, 1884, 20352, 448, 697, 12685, 21274, 11, 1741, 438, 7722, 13779, 11, 63257, 12644, 11, 18372, 6842, 11, 476, 13515, 323, 17163, 13, 14686, 476, 11555, 11, 323, 8683, 7069, 13, 22656, 0, 151645, 198]
inputs:
<|im_start|>system
You are Qwen, created by Alibaba Cloud. You are a helpful assistant.<|im_end|>
<|im_start|>user
Describe a process of making crepes.<|im_end|>
<|im_start|>assistant
Making crepes is an easy and delicious process! Here are step-by-step instructions on how to make them:

1. Assemble your ingredients. For basic crepes, you'll need: 1 cup all-purpose flour, 2 eggs, 1/2 cup milk, 1/2 cup water, 1/4 teaspoon salt, and 2 tablespoons melted butter.

2. Mix the batter: In a large mixing bowl, whisk together the flour and the eggs. Gradually add the milk and water, stirring constantly to ensure that there are no lumps. Add salt and melted butter, and mix well.

3. Let the batter rest: If you can, let the batter sit for an hour or so. This will help the flour to absorb the liquid and make the crepes more tender.

4. Heat your pan: Preheat a non-stick pan over medium heat. Lightly butter the pan or use cooking spray to prevent the crepes from sticking.

5. Pour the batter: Using a ladle or a measuring cup, pour a small amount of batter (about 1/4 cup) onto the center of the pan. Immediately tilt the pan in a circular motion to spread the batter evenly and thinly over the bottom of the pan.

6. Cook the crepe: Cook the crepe for 1-2 minutes until the bottom is lightly golden. Carefully loosen the edges with a spatula and flip the crepe over to cook the other side for another minute.

7. Remove and repeat: Gently slide the crepe onto a plate, and then repeat the process with the remaining batter. Remember to re-butter the pan between each crepe if necessary.

8. Fill and serve: Fill your cooked crepes with your desired filling, such as fresh fruit, whipped cream, Nutella, or ham and cheese. Roll or fold, and serve immediately. Enjoy!<|im_end|>

label_ids:
[-100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, -100, 42246, 1884, 20352, 374, 458, 4135, 323, 17923, 1882, 0, 5692, 525, 3019, 14319, 29208, 11221, 389, 1246, 311, 1281, 1105, 1447, 16, 13, 1634, 15790, 697, 13966, 13, 1752, 6770, 1884, 20352, 11, 498, 3278, 1184, 25, 220, 16, 10525, 678, 58238, 19828, 11, 220, 17, 18805, 11, 220, 16, 14, 17, 10525, 14074, 11, 220, 16, 14, 17, 10525, 3015, 11, 220, 16, 14, 19, 41284, 12021, 11, 323, 220, 17, 55488, 49359, 14100, 382, 17, 13, 19219, 279, 8745, 25, 758, 264, 3460, 26792, 19212, 11, 40659, 3786, 279, 19828, 323, 279, 18805, 13, 21794, 1832, 912, 279, 14074, 323, 3015, 11, 53954, 14971, 311, 5978, 429, 1052, 525, 902, 326, 11793, 13, 2691, 12021, 323, 49359, 14100, 11, 323, 6514, 1632, 382, 18, 13, 6771, 279, 8745, 2732, 25, 1416, 498, 646, 11, 1077, 279, 8745, 2444, 369, 458, 6460, 476, 773, 13, 1096, 686, 1492, 279, 19828, 311, 34306, 279, 14473, 323, 1281, 279, 1884, 20352, 803, 27582, 382, 19, 13, 26070, 697, 7215, 25, 4968, 19963, 264, 2477, 5477, 865, 7215, 916, 11051, 8628, 13, 8658, 398, 14100, 279, 7215, 476, 990, 17233, 22899, 311, 5358, 279, 1884, 20352, 504, 36972, 382, 20, 13, 25968, 279, 8745, 25, 12091, 264, 57625, 273, 476, 264, 28990, 10525, 11, 4914, 264, 2613, 3311, 315, 8745, 320, 9096, 220, 16, 14, 19, 10525, 8, 8630, 279, 4126, 315, 279, 7215, 13, 68252, 36200, 279, 7215, 304, 264, 26931, 11379, 311, 8865, 279, 8745, 41047, 323, 98431, 916, 279, 5622, 315, 279, 7215, 382, 21, 13, 12514, 279, 1884, 375, 25, 12514, 279, 1884, 375, 369, 220, 16, 12, 17, 4420, 3080, 279, 5622, 374, 33404, 20748, 13, 10627, 3641, 82742, 279, 12822, 448, 264, 62883, 5607, 323, 18287, 279, 1884, 375, 916, 311, 4296, 279, 1008, 3108, 369, 2441, 9383, 382, 22, 13, 10783, 323, 13153, 25, 479, 4402, 14983, 279, 1884, 375, 8630, 264, 11968, 11, 323, 1221, 13153, 279, 1882, 448, 279, 9664, 8745, 13, 19881, 311, 312, 1455, 6207, 279, 7215, 1948, 1817, 1884, 375, 421, 5871, 382, 23, 13, 21979, 323, 8683, 25, 21979, 697, 29105, 1884, 20352, 448, 697, 12685, 21274, 11, 1741, 438, 7722, 13779, 11, 63257, 12644, 11, 18372, 6842, 11, 476, 13515, 323, 17163, 13, 14686, 476, 11555, 11, 323, 8683, 7069, 13, 22656, 0, 151645, 198]
labels:
Making crepes is an easy and delicious process! Here are step-by-step instructions on how to make them:

1. Assemble your ingredients. For basic crepes, you'll need: 1 cup all-purpose flour, 2 eggs, 1/2 cup milk, 1/2 cup water, 1/4 teaspoon salt, and 2 tablespoons melted butter.

2. Mix the batter: In a large mixing bowl, whisk together the flour and the eggs. Gradually add the milk and water, stirring constantly to ensure that there are no lumps. Add salt and melted butter, and mix well.

3. Let the batter rest: If you can, let the batter sit for an hour or so. This will help the flour to absorb the liquid and make the crepes more tender.

4. Heat your pan: Preheat a non-stick pan over medium heat. Lightly butter the pan or use cooking spray to prevent the crepes from sticking.

5. Pour the batter: Using a ladle or a measuring cup, pour a small amount of batter (about 1/4 cup) onto the center of the pan. Immediately tilt the pan in a circular motion to spread the batter evenly and thinly over the bottom of the pan.

6. Cook the crepe: Cook the crepe for 1-2 minutes until the bottom is lightly golden. Carefully loosen the edges with a spatula and flip the crepe over to cook the other side for another minute.

7. Remove and repeat: Gently slide the crepe onto a plate, and then repeat the process with the remaining batter. Remember to re-butter the pan between each crepe if necessary.

8. Fill and serve: Fill your cooked crepes with your desired filling, such as fresh fruit, whipped cream, Nutella, or ham and cheese. Roll or fold, and serve immediately. Enjoy!<|im_end|>

[INFO|configuration_utils.py:763] 2026-01-27 18:33:35,797 >> loading configuration file /jizhicfs/hymiezhao/models/Qwen2.5-1.5B/config.json
[INFO|configuration_utils.py:839] 2026-01-27 18:33:35,799 >> Model config Qwen2Config {
  "architectures": [
    "Qwen2ForCausalLM"
  ],
  "attention_dropout": 0.0,
  "bos_token_id": 151643,
  "dtype": "bfloat16",
  "eos_token_id": 151643,
  "hidden_act": "silu",
  "hidden_size": 1536,
  "initializer_range": 0.02,
  "intermediate_size": 8960,
  "layer_types": [
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention",
    "full_attention"
  ],
  "max_position_embeddings": 131072,
  "max_window_layers": 28,
  "model_type": "qwen2",
  "num_attention_heads": 12,
  "num_hidden_layers": 28,
  "num_key_value_heads": 2,
  "rms_norm_eps": 1e-06,
  "rope_scaling": null,
  "rope_theta": 1000000.0,
  "sliding_window": null,
  "tie_word_embeddings": true,
  "transformers_version": "4.57.0",
  "use_cache": true,
  "use_mrope": false,
  "use_sliding_window": false,
  "vocab_size": 151936
}

[INFO|2026-01-27 18:33:35] llamafactory.model.model_utils.kv_cache:143 >> KV cache is disabled during training.
[INFO|modeling_utils.py:1173] 2026-01-27 18:33:37,803 >> loading weights file /jizhicfs/hymiezhao/models/Qwen2.5-1.5B/model.safetensors
[2026-01-27 18:33:37,811] [INFO] [config.py:735:__init__] Config mesh_device None world_size = 4
[2026-01-27 18:33:37,811] [INFO] [config.py:735:__init__] Config mesh_device None world_size = 4
[INFO|modeling_utils.py:4377] 2026-01-27 18:33:37,812 >> Detected DeepSpeed ZeRO-3: activating zero.init() for this model
[2026-01-27 18:33:37,812] [INFO] [config.py:735:__init__] Config mesh_device None world_size = 4
[2026-01-27 18:33:37,812] [INFO] [config.py:735:__init__] Config mesh_device None world_size = 4
[INFO|configuration_utils.py:986] 2026-01-27 18:33:37,818 >> Generate config GenerationConfig {
  "bos_token_id": 151643,
  "eos_token_id": 151643,
  "use_cache": false
}

[2026-01-27 18:33:38,417] [INFO] [partition_parameters.py:348:__exit__] finished initializing model - num_params = 339, num_elems = 1.78B
The tokenizer has new PAD/BOS/EOS tokens that differ from the model config and generation config. The model config and generation config were aligned accordingly, being updated with the tokenizer's values. Updated tokens: {'eos_token_id': 151645, 'bos_token_id': None, 'pad_token_id': 151643}.
The tokenizer has new PAD/BOS/EOS tokens that differ from the model config and generation config. The model config and generation config were aligned accordingly, being updated with the tokenizer's values. Updated tokens: {'eos_token_id': 151645, 'bos_token_id': None, 'pad_token_id': 151643}.
The tokenizer has new PAD/BOS/EOS tokens that differ from the model config and generation config. The model config and generation config were aligned accordingly, being updated with the tokenizer's values. Updated tokens: {'eos_token_id': 151645, 'bos_token_id': None, 'pad_token_id': 151643}.
[INFO|configuration_utils.py:939] 2026-01-27 18:34:09,745 >> loading configuration file /jizhicfs/hymiezhao/models/Qwen2.5-1.5B/generation_config.json
[INFO|configuration_utils.py:986] 2026-01-27 18:34:09,745 >> Generate config GenerationConfig {
  "bos_token_id": 151643,
  "eos_token_id": 151643,
  "max_new_tokens": 2048
}

[INFO|dynamic_module_utils.py:423] 2026-01-27 18:34:09,746 >> Could not locate the custom_generate/generate.py inside /jizhicfs/hymiezhao/models/Qwen2.5-1.5B.
[INFO|2026-01-27 18:34:09] llamafactory.model.model_utils.checkpointing:143 >> Gradient checkpointing enabled.
[INFO|2026-01-27 18:34:09] llamafactory.model.model_utils.attention:143 >> Using torch SDPA for faster training and inference.
[INFO|2026-01-27 18:34:09] llamafactory.model.adapter:143 >> DeepSpeed ZeRO3 detected, remaining trainable params in float32.
[INFO|2026-01-27 18:34:09] llamafactory.model.adapter:143 >> Fine-tuning method: Full
[INFO|2026-01-27 18:34:09] llamafactory.model.loader:143 >> trainable params: 1,543,714,304 || all params: 1,543,714,304 || trainable%: 100.0000
Detected kernel version 5.4.241, which is below the recommended minimum of 5.5.0; this can cause the process to hang. It is recommended to upgrade the kernel to the minimum version or higher.
[INFO|trainer.py:749] 2026-01-27 18:34:09,769 >> Using auto half precision backend
[WARNING|2026-01-27 18:34:09] llamafactory.train.callbacks:154 >> Previous trainer log in this folder will be deleted.
[WARNING|trainer.py:982] 2026-01-27 18:34:09,822 >> The tokenizer has new PAD/BOS/EOS tokens that differ from the model config and generation config. The model config and generation config were aligned accordingly, being updated with the tokenizer's values. Updated tokens: {'eos_token_id': 151645, 'bos_token_id': None, 'pad_token_id': 151643}.
Gradient accumulation steps mismatch: GradientAccumulationPlugin has 1, DeepSpeed config has 2. Using DeepSpeed's value.
[2026-01-27 18:34:10,099] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed info: version=0.16.9, git-hash=unknown, git-branch=unknown
[2026-01-27 18:34:10,099] [INFO] [config.py:735:__init__] Config mesh_device None world_size = 4
[2026-01-27 18:34:10,106] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed Flops Profiler Enabled: False
[2026-01-27 18:34:10,107] [INFO] [logging.py:107:log_dist] [Rank 0] Using client Optimizer as basic optimizer
[2026-01-27 18:34:10,107] [INFO] [logging.py:107:log_dist] [Rank 0] Removing param_group that has no 'params' in the basic Optimizer
[2026-01-27 18:34:10,116] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed Basic Optimizer = AdamW
[2026-01-27 18:34:10,116] [INFO] [utils.py:59:is_zero_supported_optimizer] Checking ZeRO support for optimizer=AdamW type=<class 'torch.optim.adamw.AdamW'>
[2026-01-27 18:34:10,116] [INFO] [logging.py:107:log_dist] [Rank 0] Creating fp16 ZeRO stage 3 optimizer, MiCS is enabled False, Hierarchical params gather False
[2026-01-27 18:34:10,116] [INFO] [logging.py:107:log_dist] [Rank 0] Creating torch.bfloat16 ZeRO stage 3 optimizer
[2026-01-27 18:34:10,355] [INFO] [utils.py:781:see_memory_usage] Stage 3 initialize beginning
[2026-01-27 18:34:10,356] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 2.02 GB         CA 0.73 GB         Max_CA 2 GB 
[2026-01-27 18:34:10,356] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:10,357] [INFO] [stage3.py:170:__init__] Reduce bucket size 2359296
[2026-01-27 18:34:10,358] [INFO] [stage3.py:171:__init__] Prefetch bucket size 2123366
[2026-01-27 18:34:10,590] [INFO] [utils.py:781:see_memory_usage] DeepSpeedZeRoOffload initialize [begin]
[2026-01-27 18:34:10,590] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 0.72 GB         CA 0.73 GB         Max_CA 1 GB 
[2026-01-27 18:34:10,591] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.66 GB, percent = 2.8%
Parameter Offload: Total persistent parameters: 144896 in 141 params
[2026-01-27 18:34:10,871] [INFO] [utils.py:781:see_memory_usage] DeepSpeedZeRoOffload initialize [end]
[2026-01-27 18:34:10,872] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 0.72 GB         CA 0.73 GB         Max_CA 1 GB 
[2026-01-27 18:34:10,872] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:11,117] [INFO] [utils.py:781:see_memory_usage] Before creating fp16 partitions
[2026-01-27 18:34:11,118] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 0.72 GB         CA 0.73 GB         Max_CA 1 GB 
[2026-01-27 18:34:11,118] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:11,991] [INFO] [utils.py:781:see_memory_usage] After creating fp16 partitions: 2
[2026-01-27 18:34:11,992] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 0.72 GB         CA 0.72 GB         Max_CA 1 GB 
[2026-01-27 18:34:11,992] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.69 GB, percent = 2.8%
[2026-01-27 18:34:12,237] [INFO] [utils.py:781:see_memory_usage] Before creating fp32 partitions
[2026-01-27 18:34:12,238] [INFO] [utils.py:782:see_memory_usage] MA 0.72 GB         Max_MA 0.72 GB         CA 0.72 GB         Max_CA 1 GB 
[2026-01-27 18:34:12,238] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:12,493] [INFO] [utils.py:781:see_memory_usage] After creating fp32 partitions
[2026-01-27 18:34:12,494] [INFO] [utils.py:782:see_memory_usage] MA 2.16 GB         Max_MA 2.88 GB         CA 2.89 GB         Max_CA 3 GB 
[2026-01-27 18:34:12,494] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:12,739] [INFO] [utils.py:781:see_memory_usage] Before initializing optimizer states
[2026-01-27 18:34:12,740] [INFO] [utils.py:782:see_memory_usage] MA 2.16 GB         Max_MA 2.16 GB         CA 2.89 GB         Max_CA 3 GB 
[2026-01-27 18:34:12,740] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:12,991] [INFO] [utils.py:781:see_memory_usage] After initializing optimizer states
[2026-01-27 18:34:12,992] [INFO] [utils.py:782:see_memory_usage] MA 2.16 GB         Max_MA 3.59 GB         CA 4.32 GB         Max_CA 4 GB 
[2026-01-27 18:34:12,992] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.67 GB, percent = 2.8%
[2026-01-27 18:34:12,992] [INFO] [stage3.py:534:_setup_for_real_optimizer] optimizer state initialized
[2026-01-27 18:34:13,328] [INFO] [utils.py:781:see_memory_usage] After initializing ZeRO optimizer
[2026-01-27 18:34:13,329] [INFO] [utils.py:782:see_memory_usage] MA 2.88 GB         Max_MA 3.75 GB         CA 4.32 GB         Max_CA 4 GB 
[2026-01-27 18:34:13,329] [INFO] [utils.py:789:see_memory_usage] CPU Virtual Memory:  used = 63.9 GB, percent = 2.8%
[2026-01-27 18:34:13,329] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed Final Optimizer = DeepSpeedZeroOptimizer_Stage3
[2026-01-27 18:34:13,329] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed using configured LR scheduler = None
[2026-01-27 18:34:13,329] [INFO] [logging.py:107:log_dist] [Rank 0] DeepSpeed LR Scheduler = None
[2026-01-27 18:34:13,329] [INFO] [logging.py:107:log_dist] [Rank 0] step=0, skipped=0, lr=[0.0, 0.0], mom=[(0.9, 0.999), (0.9, 0.999)]
[2026-01-27 18:34:13,330] [INFO] [config.py:1003:print] DeepSpeedEngine configuration:
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   activation_checkpointing_config  {
    "partition_activations": false, 
    "contiguous_memory_optimization": false, 
    "cpu_checkpointing": false, 
    "number_checkpoints": null, 
    "synchronize_checkpoint_boundary": false, 
    "profile": false
}
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   aio_config ................... {'block_size': 1048576, 'queue_depth': 8, 'intra_op_parallelism': 1, 'single_submit': False, 'overlap_events': True, 'use_gds': False}
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   amp_enabled .................. False
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   amp_params ................... False
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   autotuning_config ............ {
    "enabled": false, 
    "start_step": null, 
    "end_step": null, 
    "metric_path": null, 
    "arg_mappings": null, 
    "metric": "throughput", 
    "model_info": null, 
    "results_dir": "autotuning_results", 
    "exps_dir": "autotuning_exps", 
    "overwrite": true, 
    "fast": true, 
    "start_profile_step": 3, 
    "end_profile_step": 5, 
    "tuner_type": "gridsearch", 
    "tuner_early_stopping": 5, 
    "tuner_num_trials": 50, 
    "model_info_path": null, 
    "mp_size": 1, 
    "max_train_batch_size": null, 
    "min_train_batch_size": 1, 
    "max_train_micro_batch_size_per_gpu": 1.024000e+03, 
    "min_train_micro_batch_size_per_gpu": 1, 
    "num_tuning_micro_batch_sizes": 3
}
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   bfloat16_enabled ............. True
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   bfloat16_immediate_grad_update  True
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   checkpoint_parallel_write_pipeline  False
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   checkpoint_tag_validation_enabled  True
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   checkpoint_tag_validation_fail  False
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   comms_config ................. <deepspeed.comm.config.DeepSpeedCommsConfig object at 0x7f3a303881c0>
[2026-01-27 18:34:13,331] [INFO] [config.py:1007:print]   communication_data_type ...... None
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   compile_config ............... deepcompile=False free_activation=False offload_activation=False offload_opt_states=False double_buffer=True symmetric_memory=False debug_log=False offload_parameters=False sync_before_reduce=False sync_after_reduce=False sync_before_allgather=False sync_after_allgather=False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   compression_config ........... {'weight_quantization': {'shared_parameters': {'enabled': False, 'quantizer_kernel': False, 'schedule_offset': 0, 'quantize_groups': 1, 'quantize_verbose': False, 'quantization_type': 'symmetric', 'quantize_weight_in_forward': False, 'rounding': 'nearest', 'fp16_mixed_quantize': False, 'quantize_change_ratio': 0.001}, 'different_groups': {}}, 'activation_quantization': {'shared_parameters': {'enabled': False, 'quantization_type': 'symmetric', 'range_calibration': 'dynamic', 'schedule_offset': 1000}, 'different_groups': {}}, 'sparse_pruning': {'shared_parameters': {'enabled': False, 'method': 'l1', 'schedule_offset': 1000}, 'different_groups': {}}, 'row_pruning': {'shared_parameters': {'enabled': False, 'method': 'l1', 'schedule_offset': 1000}, 'different_groups': {}}, 'head_pruning': {'shared_parameters': {'enabled': False, 'method': 'topk', 'schedule_offset': 1000}, 'different_groups': {}}, 'channel_pruning': {'shared_parameters': {'enabled': False, 'method': 'l1', 'schedule_offset': 1000}, 'different_groups': {}}, 'layer_reduction': {'enabled': False}}
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   curriculum_enabled_legacy .... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   curriculum_params_legacy ..... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   data_efficiency_config ....... {'enabled': False, 'seed': 1234, 'data_sampling': {'enabled': False, 'num_epochs': 1000, 'num_workers': 0, 'pin_memory': False, 'curriculum_learning': {'enabled': False}, 'dynamic_batching': {'enabled': False, 'lr_scaling_method': 'linear', 'min_batch_size': 1, 'max_batch_size': None, 'sequence_picking_order': 'dataloader', 'verbose': False}}, 'data_routing': {'enabled': False, 'random_ltd': {'enabled': False, 'layer_token_lr_schedule': {'enabled': False}}}}
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   data_efficiency_enabled ...... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   dataloader_drop_last ......... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   disable_allgather ............ False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   dump_state ................... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   dynamic_loss_scale_args ...... None
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_enabled ........... False
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_gas_boundary_resolution  1
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_layer_name ........ bert.encoder.layer
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_layer_num ......... 0
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_max_iter .......... 100
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_stability ......... 1e-06
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_tol ............... 0.01
[2026-01-27 18:34:13,332] [INFO] [config.py:1007:print]   eigenvalue_verbose ........... False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   elasticity_enabled ........... False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   flops_profiler_config ........ {
    "enabled": false, 
    "recompute_fwd_factor": 0.0, 
    "profile_step": 1, 
    "module_depth": -1, 
    "top_modules": 1, 
    "detailed": true, 
    "output_file": null
}
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   fp16_auto_cast ............... None
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   fp16_enabled ................. False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   fp16_master_weights_and_gradients  False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   global_rank .................. 0
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   grad_accum_dtype ............. None
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   gradient_accumulation_steps .. 2
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   gradient_clipping ............ 1.0
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   gradient_predivide_factor .... 1.0
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   graph_harvesting ............. False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   hybrid_engine ................ enabled=False max_out_tokens=512 inference_tp_size=1 release_inference_cache=False pin_parameters=True tp_gather_partition_size=8
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   initial_dynamic_scale ........ 1
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   load_universal_checkpoint .... False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   loss_scale ................... 1.0
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   memory_breakdown ............. False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   mics_hierarchial_params_gather  False
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   mics_shard_size .............. -1
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   monitor_config ............... tensorboard=TensorBoardConfig(enabled=False, output_path='', job_name='DeepSpeedJobName') comet=CometConfig(enabled=False, samples_log_interval=100, project=None, workspace=None, api_key=None, experiment_name=None, experiment_key=None, online=None, mode=None) wandb=WandbConfig(enabled=False, group=None, team=None, project='deepspeed') csv_monitor=CSVConfig(enabled=False, output_path='', job_name='DeepSpeedJobName')
[2026-01-27 18:34:13,333] [INFO] [config.py:1007:print]   nebula_config ................ {
    "enabled": false, 
    "persistent_storage_path": null, 
    "persistent_time_interval": 100, 
    "num_of_version_in_retention": 2, 
    "enable_nebula_load": true, 
    "load_path": null
}
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   optimizer_legacy_fusion ...... False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   optimizer_name ............... None
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   optimizer_params ............. None
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   pipeline ..................... {'stages': 'auto', 'partition': 'best', 'seed_layers': False, 'activation_checkpoint_interval': 0, 'pipe_partitioned': True, 'grad_partitioned': True}
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   pld_enabled .................. False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   pld_params ................... False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   prescale_gradients ........... False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   scheduler_name ............... None
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   scheduler_params ............. None
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   seq_parallel_communication_data_type  torch.float32
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   sparse_attention ............. None
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   sparse_gradients_enabled ..... False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   steps_per_print .............. inf
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   tensor_parallel_config ....... dtype=torch.float16 autotp_size=0 tp_overlap_comm=False tensor_parallel=TPConfig(tp_size=1, tp_grain_size=1, mpu=None, tp_group=None) injection_policy_tuple=None keep_module_on_host=False replace_with_kernel_inject=False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   timers_config ................ enabled=True synchronized=True
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   train_batch_size ............. 8
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   train_micro_batch_size_per_gpu  1
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   use_data_before_expert_parallel_  False
[2026-01-27 18:34:13,334] [INFO] [config.py:1007:print]   use_node_local_storage ....... False
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   wall_clock_breakdown ......... False
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   weight_quantization_config ... None
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   world_size ................... 4
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   zero_allow_untested_optimizer  True
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   zero_config .................. stage=3 contiguous_gradients=True reduce_scatter=True reduce_bucket_size=2359296 use_multi_rank_bucket_allreduce=True allgather_partitions=True allgather_bucket_size=500000000 overlap_comm=False load_from_fp32_weights=True elastic_checkpoint=False offload_param=None offload_optimizer=None sub_group_size=1000000000 cpu_offload_param=None cpu_offload_use_pin_memory=None cpu_offload=None prefetch_bucket_size=2123366 param_persistence_threshold=15360 model_persistence_threshold=9223372036854775807 max_live_parameters=1000000000 max_reuse_distance=1000000000 gather_16bit_weights_on_model_save=True module_granularity_threshold=0 use_all_reduce_for_fetch_params=False stage3_gather_fp16_weights_on_model_save=False ignore_unused_parameters=True legacy_stage1=False round_robin_gradients=False zero_hpz_partition_size=1 zero_quantized_weights=False zero_quantized_nontrainable_weights=False zero_quantized_gradients=False zeropp_loco_param=None mics_shard_size=-1 mics_hierarchical_params_gather=False memory_efficient_linear=True pipeline_loading_checkpoint=False override_module_apply=True log_trace_cache_warnings=False
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   zero_enabled ................. True
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   zero_force_ds_cpu_optimizer .. True
[2026-01-27 18:34:13,335] [INFO] [config.py:1007:print]   zero_optimization_stage ...... 3
[2026-01-27 18:34:13,335] [INFO] [config.py:993:print_user_config]   json = {
    "train_batch_size": 8, 
    "train_micro_batch_size_per_gpu": 1, 
    "gradient_accumulation_steps": 2, 
    "gradient_clipping": 1.0, 
    "zero_allow_untested_optimizer": true, 
    "fp16": {
        "enabled": false, 
        "loss_scale": 0, 
        "loss_scale_window": 1000, 
        "initial_scale_power": 16, 
        "hysteresis": 2, 
        "min_loss_scale": 1
    }, 
    "bf16": {
        "enabled": true
    }, 
    "zero_optimization": {
        "stage": 3, 
        "overlap_comm": false, 
        "contiguous_gradients": true, 
        "sub_group_size": 1.000000e+09, 
        "reduce_bucket_size": 2.359296e+06, 
        "stage3_prefetch_bucket_size": 2.123366e+06, 
        "stage3_param_persistence_threshold": 1.536000e+04, 
        "stage3_max_live_parameters": 1.000000e+09, 
        "stage3_max_reuse_distance": 1.000000e+09, 
        "stage3_gather_16bit_weights_on_model_save": true
    }, 
    "steps_per_print": inf
}
[INFO|trainer.py:2519] 2026-01-27 18:34:13,337 >> ***** Running training *****
[INFO|trainer.py:2520] 2026-01-27 18:34:13,337 >>   Num examples = 899
[INFO|trainer.py:2521] 2026-01-27 18:34:13,337 >>   Num Epochs = 3
[INFO|trainer.py:2522] 2026-01-27 18:34:13,337 >>   Instantaneous batch size per device = 1
[INFO|trainer.py:2525] 2026-01-27 18:34:13,337 >>   Total train batch size (w. parallel, distributed & accumulation) = 8
[INFO|trainer.py:2526] 2026-01-27 18:34:13,337 >>   Gradient Accumulation steps = 2
[INFO|trainer.py:2527] 2026-01-27 18:34:13,337 >>   Total optimization steps = 339
[INFO|trainer.py:2528] 2026-01-27 18:34:13,338 >>   Number of trainable parameters = 1,543,714,304
swanlab: Tracking run with swanlab version 0.7.3
swanlab: Run data will be saved locally in /jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory/swanlog/run-20260127_183414-aji0y6dpbhsahxthv3a3z
swanlab: 🌟 Run `swanlab watch /jizhicfs/hymiezhao/lpc/repos/LLaMA-Factory/swanlog` to view SwanLab Experiment Dashboard locally

  0%|                                                                                                               | 0/339 [00:00<?, ?it/s]
  0%|▎                                                                                                      | 1/339 [00:06<34:39,  6.15s/it]
  1%|▌                                                                                                      | 2/339 [00:07<17:28,  3.11s/it]
  1%|▉                                                                                                      | 3/339 [00:07<11:40,  2.09s/it]
  1%|█▏                                                                                                     | 4/339 [00:08<08:56,  1.60s/it]
  1%|█▌                                                                                                     | 5/339 [00:09<07:26,  1.34s/it]
  2%|█▊                                                                                                     | 6/339 [00:10<06:30,  1.17s/it]
  2%|██▏                                                                                                    | 7/339 [00:11<05:55,  1.07s/it]
  2%|██▍                                                                                                    | 8/339 [00:12<05:31,  1.00s/it]
  3%|██▋                                                                                                    | 9/339 [00:13<05:16,  1.04it/s]
  3%|███                                                                                                   | 10/339 [00:14<05:06,  1.07it/s]
                                                                                                                                            
{'loss': 1.2447, 'grad_norm': 4.12263353760479, 'learning_rate': 1.323529411764706e-05, 'epoch': 0.09}

  3%|███                                                                                                   | 10/339 [00:15<05:06,  1.07it/s]
  3%|███▎                                                                                                  | 11/339 [00:16<07:35,  1.39s/it]
  4%|███▌                                                                                                  | 12/339 [00:17<06:41,  1.23s/it]
  4%|███▉                                                                                                  | 13/339 [00:18<06:07,  1.13s/it]
  4%|████▏                                                                                                 | 14/339 [00:19<05:39,  1.04s/it]
  4%|████▌                                                                                                 | 15/339 [00:19<05:20,  1.01it/s]
  5%|████▊                                                                                                 | 16/339 [00:20<05:07,  1.05it/s]
  5%|█████                                                                                                 | 17/339 [00:21<04:58,  1.08it/s]
  5%|█████▍                                                                                                | 18/339 [00:22<04:49,  1.11it/s]
  6%|█████▋                                                                                                | 19/339 [00:23<04:44,  1.13it/s]
  6%|██████                                                                                                | 20/339 [00:24<04:39,  1.14it/s]
                                                                                                                                            
{'loss': 1.1635, 'grad_norm': 10.782591273111295, 'learning_rate': 2.7941176470588236e-05, 'epoch': 0.18}

  6%|██████                                                                                                | 20/339 [00:24<04:39,  1.14it/s]
  6%|██████▎                                                                                               | 21/339 [00:25<05:10,  1.02it/s]
  6%|██████▌                                                                                               | 22/339 [00:26<04:57,  1.07it/s]
  7%|██████▉                                                                                               | 23/339 [00:27<04:46,  1.10it/s]
  7%|███████▏                                                                                              | 24/339 [00:27<04:39,  1.13it/s]
  7%|███████▌                                                                                              | 25/339 [00:28<04:36,  1.14it/s]
  8%|███████▊                                                                                              | 26/339 [00:29<04:33,  1.14it/s]
  8%|████████                                                                                              | 27/339 [00:30<04:31,  1.15it/s]
  8%|████████▍                                                                                             | 28/339 [00:31<04:28,  1.16it/s]
  9%|████████▋                                                                                             | 29/339 [00:32<04:26,  1.16it/s]
  9%|█████████                                                                                             | 30/339 [00:33<04:25,  1.17it/s]
                                                                                                                                            
{'loss': 1.2151, 'grad_norm': 6.162417979188719, 'learning_rate': 4.2647058823529415e-05, 'epoch': 0.27}

  9%|█████████                                                                                             | 30/339 [00:33<04:25,  1.17it/s]
  9%|█████████▎                                                                                            | 31/339 [00:33<04:29,  1.14it/s]
  9%|█████████▋                                                                                            | 32/339 [00:34<04:24,  1.16it/s]
 10%|█████████▉                                                                                            | 33/339 [00:35<04:23,  1.16it/s]
 10%|██████████▏                                                                                           | 34/339 [00:36<04:21,  1.16it/s]
 10%|██████████▌                                                                                           | 35/339 [00:37<04:20,  1.17it/s]
 11%|██████████▊                                                                                           | 36/339 [00:38<04:19,  1.17it/s]
 11%|███████████▏                                                                                          | 37/339 [00:39<04:16,  1.18it/s]
 11%|███████████▍                                                                                          | 38/339 [00:39<04:16,  1.18it/s]
 12%|███████████▋                                                                                          | 39/339 [00:40<04:14,  1.18it/s]
 12%|████████████                                                                                          | 40/339 [00:41<04:14,  1.18it/s]
                                                                                                                                            
{'loss': 1.27, 'grad_norm': 4.742420869494634, 'learning_rate': 4.9966852247120764e-05, 'epoch': 0.36}

 12%|████████████                                                                                          | 40/339 [00:41<04:14,  1.18it/s]
 12%|████████████▎                                                                                         | 41/339 [00:42<04:17,  1.16it/s]
 12%|████████████▋                                                                                         | 42/339 [00:43<04:16,  1.16it/s]
 13%|████████████▉                                                                                         | 43/339 [00:44<04:13,  1.17it/s]
 13%|█████████████▏                                                                                        | 44/339 [00:45<04:20,  1.13it/s]
 13%|█████████████▌                                                                                        | 45/339 [00:46<04:14,  1.15it/s]
 14%|█████████████▊                                                                                        | 46/339 [00:46<04:10,  1.17it/s]
 14%|██████████████▏                                                                                       | 47/339 [00:47<04:15,  1.14it/s]
 14%|██████████████▍                                                                                       | 48/339 [00:48<04:12,  1.15it/s]
 14%|██████████████▋                                                                                       | 49/339 [00:49<04:11,  1.15it/s]
 15%|███████████████                                                                                       | 50/339 [00:50<04:10,  1.16it/s]
                                                                                                                                            
{'loss': 1.2416, 'grad_norm': 6.836748069721696, 'learning_rate': 4.970219740227693e-05, 'epoch': 0.44}

 15%|███████████████                                                                                       | 50/339 [00:50<04:10,  1.16it/s]
"""
    
    # 创建测试文件
    test_file = "D:\\MyProject\\Dataflow-LoopAI\\dec18de7-f32a-457e-8ab3-bd85abd450e7.txt"
    # with open(test_file, 'w', encoding='utf-8') as f:
    #     f.write(test_log_content)
    
    # 解析进度
    progress = parser.parse_training_progress(test_file)
    if progress:
        print("解析结果:")
        print(f"当前步数: {progress['current_step']}")
        print(f"总步数: {progress['total_steps']}")
        print(f"已用时间: {progress['elapsed_time']}")
        print(f"剩余时间: {progress['remaining_time']}")
        print(f"进度文本: {progress['progress_text']}")
        print(f"时间文本: {progress['time_text']}")
        print(f"进度百分比: {parser.get_progress_percentage(progress):.2%}")
    else:
        print("未找到进度信息")
    
    # 清理测试文件
    if os.path.exists(test_file):
        os.remove(test_file)
