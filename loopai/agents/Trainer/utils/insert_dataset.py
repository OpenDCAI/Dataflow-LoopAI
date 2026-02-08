"""
数据集信息插入工具
用于管理 LlamaFactory dataset_info.json 中的数据集信息
"""

import os
import json
from pathlib import Path
from typing import Tuple, Optional
from loopai.logger import get_logger

logger = get_logger()


def insert_dataset_to_llamafactory(dataset_path: str, llamafactory_dir: str) -> Tuple[bool, str, str]:
    """
    将数据集信息插入到 LlamaFactory 的 dataset_info.json 中
    
    Args:
        dataset_path: 数据集文件路径
        llamafactory_dir: LlamaFactory 目录路径
        
    Returns:
        Tuple[bool, str, str]: (成功标志, 数据集名称, 错误信息)
    """
    try:
        # 验证输入参数
        if not dataset_path or not os.path.exists(dataset_path):
            error_msg = f"数据集文件不存在: {dataset_path}"
            logger.error(error_msg)
            return False, "", error_msg
            
        if not llamafactory_dir or not os.path.exists(llamafactory_dir):
            error_msg = f"LlamaFactory目录不存在: {llamafactory_dir}"
            logger.error(error_msg)
            return False, "", error_msg
        
        # 构建 dataset_info.json 路径
        dataset_info_path = os.path.join(llamafactory_dir, "data", "dataset_info.json")
        
        # 确保data目录存在
        data_dir = os.path.dirname(dataset_info_path)
        os.makedirs(data_dir, exist_ok=True)
        
        # 生成数据集名称（基于文件名，去掉扩展名）
        dataset_name = Path(dataset_path).stem
        
        # 转换为绝对路径
        dataset_abs_path = os.path.abspath(dataset_path)
        
        logger.info(f"准备插入数据集: {dataset_name}")
        logger.info(f"数据集路径: {dataset_abs_path}")
        logger.info(f"dataset_info.json路径: {dataset_info_path}")
        
        # 读取现有的 dataset_info.json
        dataset_info = {}
        if os.path.exists(dataset_info_path):
            try:
                with open(dataset_info_path, 'r', encoding='utf-8') as f:
                    dataset_info = json.load(f)
                logger.info(f"成功读取现有dataset_info.json，包含 {len(dataset_info)} 个数据集")
            except json.JSONDecodeError as e:
                logger.warning(f"dataset_info.json格式错误: {e}，将创建新文件")
                dataset_info = {}
            except Exception as e:
                error_msg = f"读取dataset_info.json失败: {e}"
                logger.error(error_msg)
                return False, dataset_name, error_msg
        else:
            logger.info("dataset_info.json不存在，将创建新文件")
        
        # 检查数据集是否已存在
        if dataset_name in dataset_info:
            existing_path = dataset_info[dataset_name].get("file_name", "")
            if existing_path == dataset_abs_path:
                logger.info(f"数据集 '{dataset_name}' 已存在且路径相同，跳过插入")
                return True, dataset_name, ""
            else:
                logger.warning(f"数据集 '{dataset_name}' 已存在但路径不同:")
                logger.warning(f"  现有路径: {existing_path}")
                logger.warning(f"  新路径: {dataset_abs_path}")
                logger.warning("将更新为新路径")
        
        # 插入或更新数据集信息
        dataset_info[dataset_name] = {
            "file_name": dataset_abs_path
        }
        
        # 备份原文件（如果存在）
        if os.path.exists(dataset_info_path):
            backup_path = f"{dataset_info_path}.backup"
            try:
                import shutil
                shutil.copy2(dataset_info_path, backup_path)
                logger.info(f"已备份原文件到: {backup_path}")
            except Exception as e:
                logger.warning(f"备份文件失败: {e}")
        
        # 写入更新后的 dataset_info.json
        try:
            with open(dataset_info_path, 'w', encoding='utf-8') as f:
                json.dump(dataset_info, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ 成功将数据集 '{dataset_name}' 插入到dataset_info.json")
            logger.info(f"数据集总数: {len(dataset_info)}")
            
            return True, dataset_name, ""
            
        except Exception as e:
            error_msg = f"写入dataset_info.json失败: {e}"
            logger.error(error_msg)
            return False, dataset_name, error_msg
            
    except Exception as e:
        error_msg = f"插入数据集信息时发生未知错误: {e}"
        logger.error(error_msg)
        return False, "", error_msg


def get_dataset_name_from_path(dataset_path: str) -> str:
    """
    从数据集路径获取数据集名称
    
    Args:
        dataset_path: 数据集文件路径
        
    Returns:
        数据集名称
    """
    return Path(dataset_path).stem


def check_dataset_exists_in_llamafactory(dataset_name: str, llamafactory_dir: str) -> Tuple[bool, Optional[str]]:
    """
    检查数据集是否已在 LlamaFactory 的 dataset_info.json 中存在
    
    Args:
        dataset_name: 数据集名称
        llamafactory_dir: LlamaFactory 目录路径
        
    Returns:
        Tuple[bool, Optional[str]]: (是否存在, 现有路径)
    """
    try:
        dataset_info_path = os.path.join(llamafactory_dir, "data", "dataset_info.json")
        
        if not os.path.exists(dataset_info_path):
            return False, None
            
        with open(dataset_info_path, 'r', encoding='utf-8') as f:
            dataset_info = json.load(f)
            
        if dataset_name in dataset_info:
            existing_path = dataset_info[dataset_name].get("file_name", "")
            return True, existing_path
        else:
            return False, None
            
    except Exception as e:
        logger.error(f"检查数据集存在性时出错: {e}")
        return False, None


def list_datasets_in_llamafactory(llamafactory_dir: str) -> Tuple[bool, dict, str]:
    """
    列出 LlamaFactory 中已注册的所有数据集
    
    Args:
        llamafactory_dir: LlamaFactory 目录路径
        
    Returns:
        Tuple[bool, dict, str]: (成功标志, 数据集信息字典, 错误信息)
    """
    try:
        dataset_info_path = os.path.join(llamafactory_dir, "data", "dataset_info.json")
        
        if not os.path.exists(dataset_info_path):
            return True, {}, ""
            
        with open(dataset_info_path, 'r', encoding='utf-8') as f:
            dataset_info = json.load(f)
            
        return True, dataset_info, ""
        
    except Exception as e:
        error_msg = f"读取数据集列表失败: {e}"
        logger.error(error_msg)
        return False, {}, error_msg
