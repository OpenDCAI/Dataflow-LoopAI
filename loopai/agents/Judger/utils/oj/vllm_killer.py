import os
import subprocess
import socket
import time
import re
import json

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger
logger = get_logger()

def is_process_running(process_pattern: str) -> bool:
    """
    检测指定模式的进程是否仍在运行（校验旧进程是否彻底终止）
    :param process_pattern: 进程匹配字符串
    :return: 进程存在返回True，否则返回False
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
    :param host: 主机地址（通常为localhost）
    :param port: 待检测端口
    :param timeout: 连接超时时间（秒）
    :return: 端口可用（已监听/被占用）返回True，否则返回False
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
    process_kill_wait: float = 10.0  # 旧进程终止等待超时时间
) -> bool:
    """
    关闭vllm openai兼容api服务（彻底关闭）
    :port: vllm服务启动服务端口
    :param poll_interval: 端口轮询检测间隔（秒）
    :param process_kill_wait: 旧进程终止等待超时时间（秒）
    :return: vllm子进程对象
    """
    process_pattern = "vllm.entrypoints.openai.api_server"
    host = "localhost"

    # 终止已存在的vllm api server进程（增强版，确保彻底终止）
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

            if time.time() - start_kill_time > process_kill_wait:
                # 普通终止失败，发送强制终止信号（pkill -9）
                logger.warning("普通终止失败，发送强制终止信号...")
                subprocess.run(
                    ["pkill", "-9", "-f", process_pattern],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

            # 等待一段时间后重试校验
            time.sleep(1.0)

            # 超出等待时间，抛出异常
            if time.time() - start_kill_time > process_kill_wait:
                raise Exception(f"旧vllm进程终止超时，超过{process_kill_wait}秒仍未终止")

        # 等待端口释放（避免端口占用，增加额外缓冲）
        logger.info(f"等待端口{port}释放...")
        while is_port_open(host, port):
            time.sleep(0.5)
        logger.info(f"端口{port}已释放")
        return True
    except FileNotFoundError:
        raise Exception("系统中未找到pkill/pgrep命令，该功能仅支持Linux/macOS环境")
        return False
    except Exception as e:
        raise Exception(f"终止旧vllm进程失败：{str(e)}")
        return False
