"""
训练执行工具
调用 LlamaFactory 进行模型微调并集成 SwanLab 监控
"""

import os
import subprocess
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from loopai.logger import get_logger

logger = get_logger()


class TrainingExecutor:
    """LlamaFactory 训练执行器"""
    
    def __init__(self):
        self.process = None
        self.training_log = []
    
    def execute_training(
        self,
        config_path: str,
        output_dir: str,
        use_swanlab: bool = True,
        swanlab_project: str = "llamafactory_training"
    ) -> Dict[str, Any]:
        """
        执行 LlamaFactory 训练
        
        Args:
            config_path: 训练配置文件路径
            output_dir: 输出目录
            use_swanlab: 是否使用 SwanLab 监控
            swanlab_project: SwanLab 项目名称
        
        Returns:
            训练结果字典
        """
        
        result = {
            "success": False,
            "training_started": False,
            "config_path": config_path,
            "output_dir": output_dir,
            "log_file": None,
            "swanlab_url": None,
            "error_message": None,
            "training_time": 0
        }
        
        try:
            # 检查配置文件
            if not os.path.exists(config_path):
                result["error_message"] = f"配置文件不存在: {config_path}"
                return result
            
            # 准备环境
            # self._prepare_environment(use_swanlab, swanlab_project)
            
            # 创建输出目录
            os.makedirs(output_dir, exist_ok=True)
            
            # 创建日志文件
            log_file = os.path.join(output_dir, "training.log")
            result["log_file"] = log_file
            
            # 构建训练命令
            cmd = self._build_training_command(config_path, log_file)
            
            logger.info(f"开始执行训练命令: {' '.join(cmd)}")
            
            # 执行训练
            start_time = time.time()
            success = self._run_training_process(cmd, log_file)
            end_time = time.time()
            
            result["training_time"] = end_time - start_time
            result["training_started"] = True
            
            if success:
                result["success"] = True
                result["swanlab_url"] = self._get_swanlab_url(swanlab_project) if use_swanlab else None
                logger.info("训练完成!")
            else:
                result["error_message"] = "训练过程中发生错误，请查看日志文件"
                logger.error("训练失败!")
            
        except Exception as e:
            result["error_message"] = f"训练执行异常: {str(e)}"
            logger.error(f"训练执行异常: {str(e)}")
        
        return result
    
    def monitor_training(self, log_file: str, callback=None) -> Dict[str, Any]:
        """
        实时监控训练进度
        
        Args:
            log_file: 训练日志文件路径
            callback: 进度回调函数
        
        Returns:
            监控结果
        """
        
        monitoring_result = {
            "status": "unknown",
            "current_epoch": 0,
            "total_epochs": 0,
            "current_step": 0,
            "total_steps": 0,
            "loss": 0.0,
            "learning_rate": 0.0,
            "progress_percentage": 0.0
        }
        
        if not os.path.exists(log_file):
            return monitoring_result
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in reversed(lines[-50:]):  # 检查最后50行
                if "epoch" in line.lower() and "step" in line.lower():
                    # 解析训练进度信息
                    monitoring_result.update(self._parse_training_progress(line))
                    break
            
            # 调用回调函数
            if callback:
                callback(monitoring_result)
                
        except Exception as e:
            logger.warning(f"监控训练进度时发生错误: {str(e)}")
        
        return monitoring_result
    
    def stop_training(self):
        """停止训练进程"""
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                logger.info("训练进程已停止")
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning("强制终止训练进程")
            except Exception as e:
                logger.error(f"停止训练进程时发生错误: {str(e)}")
    
    def _prepare_environment(self, use_swanlab: bool, swanlab_project: str):
        """准备训练环境"""
        
        # 设置环境变量
        if use_swanlab:
            os.environ["SWANLAB_PROJECT"] = swanlab_project
            os.environ["SWANLAB_EXPERIMENT_NAME"] = f"llamafactory_{int(time.time())}"
        
        # 检查 LlamaFactory 是否安装
        try:
            import llamafactory
            logger.info(f"LlamaFactory 版本: {llamafactory.__version__}")
        except ImportError:
            logger.warning("LlamaFactory 未安装，尝试安装...")
            self._install_llamafactory()
        
        # 检查 SwanLab 是否安装
        if use_swanlab:
            try:
                import swanlab
                logger.info(f"SwanLab 版本: {swanlab.__version__}")
            except ImportError:
                logger.warning("SwanLab 未安装，尝试安装...")
                self._install_swanlab()
    
    def _install_llamafactory(self):
        """安装 LlamaFactory"""
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", 
                "llamafactory[torch,metrics]"
            ])
            logger.info("LlamaFactory 安装成功")
        except subprocess.CalledProcessError as e:
            logger.error(f"安装 LlamaFactory 失败: {str(e)}")
            raise
    
    def _install_swanlab(self):
        """安装 SwanLab"""
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "swanlab"
            ])
            logger.info("SwanLab 安装成功")
        except subprocess.CalledProcessError as e:
            logger.error(f"安装 SwanLab 失败: {str(e)}")
            raise
    
    def _build_training_command(self, config_path: str, log_file: str) -> List[str]:
        """构建训练命令"""
        
        cmd = [
            sys.executable, "-m", "llamafactory.train",
            "--config", config_path
        ]
        
        return cmd
    
    def _run_training_process(self, cmd: List[str], log_file: str) -> bool:
        """运行训练进程"""
        
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1
                )
                
                # 实时写入日志
                for line in iter(self.process.stdout.readline, ''):
                    f.write(line)
                    f.flush()
                    self.training_log.append(line.strip())
                    
                    # 实时打印重要信息
                    if any(keyword in line.lower() for keyword in ["epoch", "loss", "error", "完成"]):
                        logger.info(line.strip())
                
                # 等待进程结束
                return_code = self.process.wait()
                return return_code == 0
                
        except Exception as e:
            logger.error(f"执行训练进程时发生错误: {str(e)}")
            return False
    
    def _parse_training_progress(self, log_line: str) -> Dict[str, Any]:
        """解析训练进度信息"""
        
        progress = {}
        
        try:
            # 这里需要根据 LlamaFactory 的实际日志格式来解析
            # 示例解析逻辑
            if "epoch" in log_line.lower():
                # 解析 epoch 信息
                pass
            if "step" in log_line.lower():
                # 解析 step 信息
                pass
            if "loss" in log_line.lower():
                # 解析 loss 信息
                pass
                
        except Exception as e:
            logger.warning(f"解析训练进度时发生错误: {str(e)}")
        
        return progress
    
    def _get_swanlab_url(self, project_name: str) -> Optional[str]:
        """获取 SwanLab 项目 URL"""
        
        try:
            # 这里需要根据 SwanLab 的 API 来获取项目 URL
            # 暂时返回默认格式
            return f"https://swanlab.cn/project/{project_name}"
        except Exception as e:
            logger.warning(f"获取 SwanLab URL 时发生错误: {str(e)}")
            return None


def validate_training_environment() -> Dict[str, Any]:
    """验证训练环境"""
    
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "python_version": sys.version,
        "packages": {}
    }
    
    # 检查必要的包
    required_packages = ["torch", "transformers", "datasets"]
    optional_packages = ["llamafactory", "swanlab"]
    
    for package in required_packages + optional_packages:
        try:
            __import__(package)
            result["packages"][package] = "已安装"
        except ImportError:
            if package in required_packages:
                result["errors"].append(f"缺少必要的包: {package}")
                result["valid"] = False
            else:
                result["warnings"].append(f"缺少可选的包: {package}")
                result["packages"][package] = "未安装"
    
    # 检查 CUDA 可用性
    try:
        import torch
        if torch.cuda.is_available():
            result["cuda_available"] = True
            result["cuda_device_count"] = torch.cuda.device_count()
        else:
            result["cuda_available"] = False
            result["warnings"].append("CUDA 不可用，将使用 CPU 训练（速度较慢）")
    except:
        result["cuda_available"] = False
    
    return result


def generate_training_report(result: Dict[str, Any]) -> str:
    """生成训练报告"""
    
    report = []
    report.append("="*50)
    report.append("训练执行报告")
    report.append("="*50)
    report.append("")
    
    # 基本信息
    report.append(f"训练状态: {'成功' if result.get('success') else '失败'}")
    report.append(f"配置文件: {result.get('config_path', 'N/A')}")
    report.append(f"输出目录: {result.get('output_dir', 'N/A')}")
    report.append(f"训练时间: {result.get('training_time', 0):.2f} 秒")
    report.append("")
    
    # 日志文件
    if result.get("log_file"):
        report.append(f"日志文件: {result['log_file']}")
    
    # SwanLab 链接
    if result.get("swanlab_url"):
        report.append(f"SwanLab 监控: {result['swanlab_url']}")
    
    # 错误信息
    if result.get("error_message"):
        report.append("")
        report.append("错误信息:")
        report.append(f"  ❌ {result['error_message']}")
    
    # 建议
    if not result.get("success"):
        report.append("")
        report.append("故障排除建议:")
        report.append("  1. 检查配置文件格式是否正确")
        report.append("  2. 确认数据集路径和格式")
        report.append("  3. 检查系统资源（内存、显存）")
        report.append("  4. 查看详细日志文件")
    
    return "\n".join(report)
