"""
训练服务客户端
用于与远程训练服务进行通信
"""

import os
import time
import requests
import yaml
from typing import Dict, Any, Optional, Tuple, List, List
from loopai.logger import get_logger

logger = get_logger()


class TrainingServiceClient:
    """训练服务客户端"""
    
    def __init__(self, base_url: str = "http://localhost:8000"):
        """
        初始化客户端
        
        Args:
            base_url: 训练服务的基础URL
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        
    def check_service_health(self) -> bool:
        """
        检查服务健康状态
        
        Returns:
            服务是否可用
        """
        try:
            response = self.session.get(f"{self.base_url}/", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"检查服务健康状态失败: {str(e)}")
            return False
    
    def start_training(self, yaml_config_path: str, task_name: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
        """
        启动训练任务
        
        Args:
            yaml_config_path: YAML配置文件路径
            task_name: 任务名称
            
        Returns:
            (成功标志, 任务ID或错误信息, 错误详情)
        """
        try:
            # 读取YAML配置文件
            if not os.path.exists(yaml_config_path):
                return False, f"配置文件不存在: {yaml_config_path}", None
            
            with open(yaml_config_path, 'r', encoding='utf-8') as f:
                yaml_content = f.read()
            
            # 验证YAML格式
            try:
                yaml.safe_load(yaml_content)
            except yaml.YAMLError as e:
                return False, f"YAML格式错误: {str(e)}", None
            
            # 构建请求数据
            request_data = {
                "config": yaml_content
            }
            
            if task_name:
                request_data["task_name"] = task_name
            # 发送训练请求
            logger.info(f"发送训练请求到: {self.base_url}/train/")
            response = self.session.post(
                f"{self.base_url}/train/",
                json=request_data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                task_id = result.get("task_id")
                logger.info(f"训练任务启动成功，任务ID: {task_id}")
                return True, task_id, None
            else:
                error_msg = f"启动训练失败，状态码: {response.status_code}"
                try:
                    error_detail = response.json().get("detail", "未知错误")
                    error_msg = f"{error_msg}, 详情: {error_detail}"
                except:
                    error_msg = f"{error_msg}, 响应: {response.text}"
                
                return False, error_msg, response.text
                
        except Exception as e:
            error_msg = f"启动训练时发生异常: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, str(e)
    
    def get_task_status(self, task_id: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        获取训练任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            (成功标志, 状态信息, 错误信息)
        """
        try:
            response = self.session.get(f"{self.base_url}/train/status/{task_id}", timeout=10)
            
            if response.status_code == 200:
                return True, response.json(), None
            else:
                error_msg = f"获取状态失败，状态码: {response.status_code}"
                return False, None, error_msg
                
        except Exception as e:
            error_msg = f"获取任务状态时发生异常: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
    
    def get_task_logs(self, task_id: str, lines: int = 100) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        获取训练任务日志
        
        Args:
            task_id: 任务ID
            lines: 获取的日志行数
            
        Returns:
            (成功标志, 日志内容, 错误信息)
        """
        try:
            params = {"lines": lines} if lines > 0 else {}
            response = self.session.get(f"{self.base_url}/train/logs/{task_id}", params=params, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                return True, result.get("logs", ""), None
            else:
                error_msg = f"获取日志失败，状态码: {response.status_code}"
                return False, None, error_msg
                
        except Exception as e:
            error_msg = f"获取任务日志时发生异常: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
    
    def wait_for_completion(self, state, 
                          task_id: str, 
                          check_interval: int = 30,
                          max_wait_time: int = 3600,
                          progress_callback: Optional[callable] = None) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        等待训练任务完成
        
        Args:
            task_id: 任务ID
            check_interval: 检查间隔（秒）
            max_wait_time: 最大等待时间（秒）
            progress_callback: 进度回调函数
            
        Returns:
            (成功标志, 最终状态信息, 错误信息)
        """
        start_time = time.time()
        
        logger.info(f"开始等待任务完成，任务ID: {task_id}")
        logger.info(f"检查间隔: {check_interval}秒，最大等待时间: {max_wait_time}秒")
        
        while True:
            # 检查是否超时
            elapsed_time = time.time() - start_time
            if elapsed_time > max_wait_time:
                error_msg = f"等待超时（{max_wait_time}秒），任务可能仍在运行"
                logger.warning(error_msg)
                return False, None, error_msg
            
            # 获取任务状态
            success, status_info, error = self.get_task_status(task_id)
            
            if not success:
                return False, None, f"获取任务状态失败: {error}"
            
            task_status = status_info.get("status")
            state['trainer_current_training_status'] = task_status
            
            # 调用进度回调
            if progress_callback:
                try:
                    progress_callback(task_id, status_info, elapsed_time)
                except Exception as e:
                    logger.warning(f"进度回调函数执行失败: {str(e)}")
            
            # 检查任务是否完成
            if task_status in ["completed", "failed", "cancelled"]:
                logger.info(f"任务完成，最终状态: {task_status}")
                return True, status_info, None
            
            # 记录当前状态
            logger.info(f"任务状态: {task_status}, 已等待: {int(elapsed_time)}秒")
            
            # 等待下次检查
            time.sleep(check_interval)
    
    def get_train_output_swanlab_log_path(self, task_id: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        获取指定任务的SwanLab日志文件夹路径
        
        Args:
            task_id: 任务ID
            
        Returns:
            (成功标志, SwanLab日志路径, 错误信息)
        """
        try:
            response = self.session.get(f"{self.base_url}/train/swanlab-logs/{task_id}", timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                log_path = result.get("log_path")
                if log_path:
                    logger.info(f"获取SwanLab日志路径成功: {log_path}")
                    return True, log_path, None
                else:
                    message = result.get("message", "日志路径未找到")
                    logger.warning(f"SwanLab日志路径未找到: {message}")
                    return True, None, message
            else:
                error_msg = f"获取SwanLab日志路径失败，状态码: {response.status_code}"
                try:
                    error_detail = response.json().get("detail", "未知错误")
                    error_msg = f"{error_msg}, 详情: {error_detail}"
                except:
                    error_msg = f"{error_msg}, 响应: {response.text}"
                return False, None, error_msg
                
        except Exception as e:
            error_msg = f"获取SwanLab日志路径时发生异常: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg
    
    def get_all_swanlab_logs(self) -> Tuple[bool, Optional[List[Dict[str, str]]], Optional[str]]:
        """
        获取所有SwanLab日志文件夹
        
        Returns:
            (成功标志, 日志文件夹列表, 错误信息)
        """
        try:
            response = self.session.get(f"{self.base_url}/train/swanlab-logs", timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                logs = result.get("logs", [])
                logger.info(f"获取所有SwanLab日志成功，共 {len(logs)} 个日志文件夹")
                return True, logs, None
            else:
                error_msg = f"获取所有SwanLab日志失败，状态码: {response.status_code}"
                return False, None, error_msg
                
        except Exception as e:
            error_msg = f"获取所有SwanLab日志时发生异常: {str(e)}"
            logger.error(error_msg)
            return False, None, error_msg


def create_training_client(base_url: Optional[str] = None) -> TrainingServiceClient:
    """
    创建训练服务客户端
    
    Args:
        base_url: 服务基础URL，默认从环境变量获取
        
    Returns:
        训练服务客户端实例
    """
    if not base_url:
        base_url = os.getenv("TRAINING_SERVICE_URL", "http://localhost:8000")
    
    return TrainingServiceClient(base_url)
