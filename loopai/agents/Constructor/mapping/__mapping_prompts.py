"""
Helper module for loading prompts in mapping nodes

Since mapping nodes are standalone functions (not class methods),
this module provides utility functions to access prompts from obtainer_prompt.json
"""
from typing import Optional
from loopai.common.prompts import PromptLoader
from loopai.logger import get_logger

logger = get_logger()

_prompt_loader: Optional[PromptLoader] = None


def get_prompt_loader(prompt_template_dir: Optional[str] = None) -> PromptLoader:
    """Get or create prompt loader instance"""
    global _prompt_loader
    if _prompt_loader is None:
        _prompt_loader = PromptLoader(prompt_template_dir)
    return _prompt_loader


def get_prompt(prompt_type: str, prompt_name: str, prompt_template_dir: Optional[str] = None) -> str:
    """
    Get prompt from obtainer_prompt.json
    
    Args:
        prompt_type: Type of prompt (e.g., "system", "task")
        prompt_name: Name of the prompt
        prompt_template_dir: Optional directory path
    
    Returns:
        Prompt string
    """
    loader = get_prompt_loader(prompt_template_dir)
    try:
        return loader(prompt_type, prompt_name)
    except Exception as e:
        logger.error(f"Failed to load prompt {prompt_type}/{prompt_name}: {e}")
        raise

