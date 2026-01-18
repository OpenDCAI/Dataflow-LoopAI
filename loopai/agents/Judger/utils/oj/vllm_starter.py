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

def parse_port_from_command_str(command_str: str) -> int:
    """
    从vllm完整命令字符串中解析出--port对应的端口号
    :param command_str: 完整的vllm命令字符串
    :return: 解析出的端口号（整数类型）
    :raises: ValueError 当未找到port配置或端口号不合法时抛出异常
    """
    port_pattern = r'--port\s*=?\s*(\d+)'
    
    # 查找匹配项（只找第一个符合条件的port配置，符合命令规范）
    match = re.search(port_pattern, command_str, re.IGNORECASE)
    
    if not match:
        raise ValueError("命令字符串中未找到--port配置项")
    
    # 提取分组中的端口号字符串，转换为整数
    try:
        port = int(match.group(1))
    except ValueError:
        raise ValueError("提取到的端口号不是有效的数字")
    
    # 校验端口号的合法性（TCP/UDP端口合法范围：1-65535）
    if not (1 <= port <= 65535):
        raise ValueError(f"端口号{port}超出合法范围（1-65535）")
    
    return port

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

def start_vllm_openai_api_server(
    env_configs,
    vllm_command,
    poll_interval: float = 2.0,
    max_timeout: float = 300.0,
    process_kill_wait: float = 10.0  # 旧进程终止等待超时时间
) -> subprocess.Popen:
    """
    启动vllm openai兼容api服务（彻底重新启动）
    :env_configs: 评估模型vllm启动环境参数
    :vllm_command: vllm服务启动命令
    :param poll_interval: 端口轮询检测间隔（秒）
    :param max_timeout: 最大等待超时时间（秒）
    :param process_kill_wait: 旧进程终止等待超时时间（秒）
    :return: vllm子进程对象
    """
    env_configs = json.loads(env_configs)
    process_pattern = "vllm.entrypoints.openai.api_server"
    port = parse_port_from_command_str(vllm_command)
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
        logger.info("等待端口{port}释放...")
        while is_port_open(host, port):
            time.sleep(0.5)
        logger.info(f"端口{port}已释放")

    except FileNotFoundError:
        raise Exception("系统中未找到pkill/pgrep命令，该功能仅支持Linux/macOS环境")
    except Exception as e:
        raise Exception(f"终止旧vllm进程失败：{str(e)}")

    # 设置NCCL和CUDA环境变量
    for key, value in env_configs.items():
        os.environ[key] = value
    logger.info("已设置GPU和NCCL环境变量")

    # 启动vllm子进程
    proc = subprocess.Popen(
        vllm_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding='utf-8',
        bufsize=1,  # 行缓冲，避免日志堆积，加快读取速度
        shell=True
    )

    # 轮询检测端口可用性，判断服务是否就绪（优化读取逻辑，避免阻塞）
    logger.info(f"正在启动vllm服务，监听端口{port}，等待服务就绪（超时{max_timeout}秒）...")
    start_time = time.time()

    while True:
        # 检查子进程是否已异常退出
        if proc.poll() is not None:
            raise Exception(f"vllm服务启动失败，进程异常退出，退出码：{proc.returncode}")
        
        # 检查是否超时
        elapsed_time = time.time() - start_time
        if elapsed_time > max_timeout:
            proc.terminate()  # 超时终止子进程
            raise Exception(f"vllm服务启动超时，超过{max_timeout}秒仍未监听端口{port}")
        
        # 检测端口是否可用
        if is_port_open(host, port):
            logger.info(f"\n✅ vllm服务启动完成，端口{port}已就绪！")
            logger.info("🔛 函数即将返回，开始执行后续Python代码...")
            break
        
        # 非阻塞读取子进程日志
        try:
            # 读取当前缓冲区所有可用数据，不等待新数据
            line = proc.stdout.readline()
            if line:
                logger.info(line.strip())
        except:
            pass  # 无数据时直接跳过，不影响循环
        
        # 未就绪，等待轮询间隔后重试
        time.sleep(poll_interval)

    # 直接返回子进程对象
    return proc

# if __name__ == "__main__":
#     # 调用函数启动vllm服务（基于端口检测）
#     try:
#         vllm_proc = start_vllm_openai_api_server('{"CUDA_VISIBLE_DEVICES": "0","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}', "python -m vllm.entrypoints.openai.api_server --model /root/brjverl/models/Qwen2.5-Coder-7B-Instruct/ --port 8911 --tensor-parallel-size 1 --trust-remote-code --gpu-memory-utilization 0.9 --enable-auto-tool-choice --tool-call-parser hermes")

#         # 后续Python业务代码（服务已完全就绪，可安全调用API）
#         logger.info("=" * 50)
#         logger.info("vllm启动过程执行完毕")
#         # 示例：调用vllm OpenAI兼容接口
#         # import openai
#         # openai.api_base = "http://localhost:8911/v1"
#         # openai.api_key = "dummy_key"  # vllm无需真实密钥，填写任意值即可
#         # models = openai.Model.list()
#         # print(f"可用模型列表：{models}")

#     except Exception as e:
#         logger.info(f"错误：{e}")
