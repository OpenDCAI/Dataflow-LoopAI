import os
import json
from loopai.logger import get_logger

logger = get_logger()

class PromptLoader:
    def __init__(self,
                 prompt_template_dir:str=None):
        """
        init the prompt_loader

        Args:
            prompt_template_dir: the directory of prompt config file: endswith `_prompt.json`.
        """
        
        default_dir = os.path.dirname(os.path.abspath(__file__))

        self.prompt_template_dir = prompt_template_dir or default_dir
        logger.info(f'Set prompt template dir as: {self.prompt_template_dir}')
        
        self.prompt_dict = {}
        self.load_prompts()
    
    def load_prompts(self):
        """
        Scan all prompt files that endswith `_prompt.json` and load them as prompt dicts.
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
                if prefix not in self.prompt_dict:
                    self.prompt_dict[prefix] = prompt_dict
                else:
                    for key in prompt_dict:
                        self.prompt_dict[prefix][key] = prompt_dict[key]
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load prompt file '{match_path}': {e}")
                raise RuntimeError(f"Error loading prompt file '{match_path}': {e}")
                
        self.check()
        
    def check(self):
        """
        Check whether the necessary prompt key required exists
        """
        required_keys = ['system']
        for key in required_keys:
            if key not in self.prompt_dict:
                logger.error(f"Missing required prompt dict key: '{key}' in prompt_dict = {self.prompt_dict}")
                raise AssertionError(f"Missing required prompt dict key: '{key}'")
    
    def __call__(self, prompt_type: str='system', prompt_name: str = 'default_prompt'):
        if prompt_type not in self.prompt_dict:
            logger.error(f"Missing required prompt dict key: '{prompt_type}' in prompt_dict = {self.prompt_dict}")
            raise AssertionError(f"Missing required prompt dict key: '{prompt_type}'")
        if prompt_name not in self.prompt_dict[prompt_type]:
            logger.error(f"Missing required prompt key: '{prompt_name}' in prompt_dict['prompt_type'] = {self.prompt_dict[prompt_type]}")
            raise AssertionError(f"Missing required prompt key: '{prompt_name}'")
        return self.prompt_dict[prompt_type][prompt_name]
