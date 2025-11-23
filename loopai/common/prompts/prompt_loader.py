import os
import json
from loopai.logger import get_logger

logger = get_logger()

class PromptLoader:
    """
    PromptLoader —— 统一管理并加载所有 prompt 配置文件的工具类。

    特点：
    - 自动扫描指定目录下所有以 `_prompt.json` 结尾的文件
    - 自动按文件名前缀创建 prompt 分组（如 system_prompt.json → system）
    - 提供类似字典的索引方式：loader(prompt_type="system", prompt_name="default_prompt")
    - 若缺少必要的 prompt（如 system/default_prompt）会直接报错，避免隐藏问题
    """

    def __init__(self,
                 prompt_template_dir: str = None):
        """
        初始化 PromptLoader

        Args:
            prompt_template_dir:
                prompt 模板所在目录。
                若为 None，则默认使用当前文件所在目录。
                注意：会自动加载所有以 `_prompt.json` 结尾的文件。
        """
        default_dir = os.path.dirname(os.path.abspath(__file__))

        self.prompt_template_dir = prompt_template_dir or default_dir
        logger.info(f'Set prompt template dir as: {self.prompt_template_dir}')
        
        self.prompt_dict = {}
        self.load_prompts()
    
    def load_prompts(self):
        """
        扫描并加载所有 `_prompt.json` 文件，将其内容缓存到 self.prompt_dict 中。

        加载规则：
        - 文件名去掉 `_prompt.json` 后作为 prompt_type（如 system_prompt.json → system）
        - JSON 文件内部的每个 key 对应 prompt_name
        - 同名 prompt_type 会合并（后加载的覆盖前一个）

        文件结构示例：
            system_prompt.json:
            {
                "default_prompt": "...",
                "strict_prompt": "..."
            }

        加载后 self.prompt_dict 结构示例：
            {
                "system": {
                    "default_prompt": "...",
                    "strict_prompt": "..."
                }
            }

        若 JSON 文件损坏或路径错误，将直接报错并终止。
        """
        list_files = os.listdir(self.prompt_template_dir)
        matched_files = []
        for filename in list_files:
            if filename.endswith("_prompt.json"):
                matched_files.append(filename)

        for match in matched_files:
            match_path = os.path.join(self.prompt_template_dir, match)
            prefix = match.replace('_prompt.json', '')
            try:
                with open(match_path, encoding='utf-8') as f:
                    prompt_dict = json.load(f)

                # 若 prefix 不存在，则直接创建
                if prefix not in self.prompt_dict:
                    self.prompt_dict[prefix] = prompt_dict
                else:
                    # 若 prefix 已存在，执行合并（后覆盖前）
                    for key in prompt_dict:
                        self.prompt_dict[prefix][key] = prompt_dict[key]

            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load prompt file '{match_path}': {e}")
                raise RuntimeError(f"Error loading prompt file '{match_path}': {e}")
                
        self.check()
        
    def check(self):
        """
        检查是否存在必要的 prompt 类型。

        当前要求：
        - 必须至少存在 'system' 这个 prompt 类型

        若未找到会直接抛 AssertionError，避免运行时报未知错误。
        """
        required_keys = ['system']
        for key in required_keys:
            if key not in self.prompt_dict:
                logger.error(
                    f"Missing required prompt dict key: '{key}' "
                    f"in prompt_dict = {self.prompt_dict}"
                )
                raise AssertionError(f"Missing required prompt dict key: '{key}'")
    
    def __call__(self, prompt_type: str = 'system', prompt_name: str = 'default_prompt'):
        """
        获取指定类型与名称的 prompt 文本。

        使用方式示例：
            loader = PromptLoader("./prompts")
            prompt = loader(prompt_type="system", prompt_name="default_prompt")

        Args:
            prompt_type: prompt 文件名前缀，如 "system"
            prompt_name: JSON 文件中的某个键，如 "default_prompt"

        Returns:
            对应的 prompt 字符串

        Raises:
            AssertionError：当 prompt_type 或 prompt_name 不存在时
        """
        if prompt_type not in self.prompt_dict:
            logger.error(
                f"Missing required prompt dict key: '{prompt_type}' "
                f"in prompt_dict = {self.prompt_dict}"
            )
            raise AssertionError(f"Missing required prompt dict key: '{prompt_type}'")

        if prompt_name not in self.prompt_dict[prompt_type]:
            logger.error(
                f"Missing required prompt key: '{prompt_name}' "
                f"in prompt_dict['prompt_type'] = {self.prompt_dict[prompt_type]}"
            )
            raise AssertionError(f"Missing required prompt key: '{prompt_name}'")
        
        return self.prompt_dict[prompt_type][prompt_name]