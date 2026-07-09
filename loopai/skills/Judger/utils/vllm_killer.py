import os
import subprocess
import socket
import time
import re
import json
import threading  # 新增：引入线程相关模块

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger
logger = get_logger()

def is_process_running(process_pattern: str) -> bool:
    """
    检测指定模式的进程是否仍在运行（校验旧进程是否彻底终止）
    param process_pattern: 进程匹配字符串
    return: 进程存在返回True，否则返回False
    """
    try:
        # 使用pgrep检测进程是否存在（返回0表示存在，非0表示不存在）
        result = subprocess.run(
            ["pgrep", "-f", process_pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except FileNotFoundError:
        raise Exception("系统中未找到pgrep命令，该功能仅支持Linux/macOS环境")

def is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """
    检测指定主机和端口是否可正常连接（TCP），判断服务是否就绪/是否被占用
    param host: 主机地址（通常为localhost）
    param port: 待检测端口
    param timeout: 连接超时时间（秒）
    return: 端口可用（已监听/被占用）返回True，否则返回False
    """
    try:
        # 创建TCP套接字并尝试连接
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        # 连接超时、被拒绝、端口未监听，均返回False
        return False

def kill_vllm_openai_api_server(
    port,
    stop_event: threading.Event = None,  # 新增：接收启动时返回的线程停止事件
    process_kill_wait: float = 30.0  # 旧进程终止等待超时时间
) -> bool:
    """
    关闭vllm openai兼容api服务（适配后台消费线程，彻底关闭）
    port: vllm服务启动服务端口
    stop_event: 启动vllm时返回的消费线程停止事件（新增参数）
    param process_kill_wait: 旧进程终止等待超时时间（秒）
    return: 终止成功返回True，否则返回False
    """
    process_pattern = "vllm.entrypoints.openai.api_server"
    host = "localhost"

    # 第一步先停止后台消费线程 
    if stop_event and not stop_event.is_set():
        stop_event.set()
        logger.info("已发送vllm输出消费线程停止信号")
        # 短暂等待线程退出，避免资源竞争
        time.sleep(0.5)

    # 终止vllm进程 
    logger.info("开始终止旧的vllm进程...")
    try:
        # 发送普通终止信号（pkill）
        subprocess.run(
            ["pkill", "-f", process_pattern],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 循环校验进程是否仍在运行，直到终止或超时
        start_kill_time = time.time()
        while True:
            if not is_process_running(process_pattern):
                logger.info("旧的vllm进程已彻底终止")
                break

            elapsed_time = time.time() - start_kill_time
            if elapsed_time > process_kill_wait:
                # 普通终止失败，发送强制终止信号（pkill -9）
                logger.warning("普通终止失败，发送强制终止信号...")
                subprocess.run(
                    ["pkill", "-9", "-f", process_pattern],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                # 强制终止后再次检查
                if not is_process_running(process_pattern):
                    logger.info("强制终止vllm进程成功")
                    break

            # 超出等待时间，退出循环
            if time.time() - start_kill_time > process_kill_wait:
                logger.warning(f"旧vllm进程终止超时，超过{process_kill_wait}秒仍未终止")
                break

            # 等待一段时间后重试校验
            time.sleep(1.0)

        logger.info(f"等待端口{port}释放...")
        while is_port_open(host, int(port)):
            time.sleep(0.5)
        logger.info(f"端口{port}已释放")
        return True

    except FileNotFoundError:
        logger.error("系统中未找到pkill/pgrep命令，该功能仅支持Linux/macOS环境")
        return False
    except Exception as e:
        logger.error(f"终止旧vllm进程失败：{str(e)}")
        return False
