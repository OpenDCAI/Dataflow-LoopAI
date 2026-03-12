import os
import subprocess
import socket
import time
import re
import json
import threading

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

# ========== 核心修复：后台线程持续消费缓冲区，避免阻塞 ==========
def _consume_subprocess_output(proc, stop_event):
    """
    后台线程持续读取子进程输出，消费缓冲区（解决卡死核心）
    不落地文件，仅输出到logger，兼容shell=True的字符串命令
    """
    try:
        while not stop_event.is_set() and proc.poll() is None:
            # 非阻塞读取（避免readline()阻塞）
            # 先检查缓冲区是否有数据，再读取
            import select
            ready, _, _ = select.select([proc.stdout], [], [], 0.01)
            if ready:
                line = proc.stdout.readline()
                if line:
                    logger.info(f"VLLM输出: {line.strip()}")
    except Exception as e:     
        logger.warning(f"消费vllm输出时出现轻微异常（不影响运行）: {e}")
    finally:
        try:
            proc.stdout.close()
        except:
            pass

def start_vllm_openai_api_server(
    env_configs, 
    vllm_env_path,  # 新增：指定环境的Python可执行文件路径
    vllm_port, 
    vllm_tensor_parallel_size, 
    vllm_gpu_memory_utilization, 
    vllm_model,
    poll_interval: float = 2.0,
    max_timeout: float = 300.0
) -> tuple[subprocess.Popen, threading.Event]:
    """
    启动vllm openai兼容api服务（保留shell=True+字符串命令，解决卡死问题）
    :return: (vllm子进程对象, 输出消费线程停止事件)
    """
    # 校验环境路径（原有逻辑不变）
    if not os.path.exists(vllm_env_path):
        raise Exception(f"指定的环境Python路径不存在：{vllm_env_path}")
    if not os.access(vllm_env_path, os.X_OK):
        raise Exception(f"指定的Python路径无执行权限：{vllm_env_path}")
    logger.info(f"已确认环境Python路径有效：{vllm_env_path}")

    # ========== 保留你原有的字符串命令+shell=True ==========
    vllm_command = f"{vllm_env_path} -m vllm.entrypoints.openai.api_server --model {vllm_model} --port {vllm_port} --tensor-parallel-size {vllm_tensor_parallel_size} --trust-remote-code --gpu-memory-utilization {vllm_gpu_memory_utilization} --enable-auto-tool-choice --tool-call-parser hermes"
    env_configs = json.loads(env_configs)
    port = parse_port_from_command_str(vllm_command)
    host = "localhost"

    # 设置环境变量（优化：使用独立环境，避免覆盖全局）
    process_env = os.environ.copy()
    for key, value in env_configs.items():
        process_env[key] = value
    logger.info("已设置GPU和NCCL环境变量")

    # ========== 保留shell=True，仅优化缓冲区和进程参数 ==========
    proc = subprocess.Popen(
        vllm_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding='utf-8',
        bufsize=1,  # 行缓冲
        shell=True,  # 保留你需要的shell=True
        env=process_env,  # 独立环境变量
        start_new_session=True,  # 避免主进程影响vllm
        close_fds=True  # 减少资源占用
    )

    # ========== 启动后台线程消费缓冲区（卡死的核心解决方案） ==========
    stop_event = threading.Event()
    consume_thread = threading.Thread(
        target=_consume_subprocess_output,
        args=(proc, stop_event),
        daemon=True  # 守护线程，不影响主进程
    )
    consume_thread.start()
    logger.info("VLLM输出消费线程已启动，解决缓冲区阻塞问题")

    # 端口检测逻辑（完全保留你原有代码）
    logger.info(f"正在启动vllm服务，监听端口{port}，等待服务就绪（超时{max_timeout}秒）...")
    start_time = time.time()

    while True:
        if proc.poll() is not None:
            stop_event.set()
            consume_thread.join(timeout=2.0)
            raise Exception(f"vllm服务启动失败，进程异常退出，退出码：{proc.returncode}")
        
        elapsed_time = time.time() - start_time
        if elapsed_time > max_timeout:
            proc.terminate()
            stop_event.set()
            consume_thread.join(timeout=2.0)
            raise Exception(f"vllm服务启动超时，超过{max_timeout}秒仍未监听端口{port}")
        
        if is_port_open(host, port):
            logger.info(f"\n✅ vllm服务启动完成，端口{port}已就绪！")
            logger.info("🔛 函数即将返回，开始执行后续Python代码...")
            break
        
        time.sleep(poll_interval)

    # 返回子进程+停止事件（用于后续安全停止）
    return proc, stop_event

# ========== 配套：安全停止vllm的函数 ==========
def stop_vllm_server(proc: subprocess.Popen, stop_event: threading.Event):
    """安全停止vllm，确保消费线程也退出"""
    try:
        proc.terminate()
        proc.wait(timeout=10.0)
        stop_event.set()
        logger.info("vllm服务已安全停止")
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("vllm服务强制终止")
    except Exception as e:
        logger.error(f"停止vllm失败: {e}")

# ========== 调用示例（和你原有调用方式几乎一致） ==========
# if __name__ == "__main__":
#     try:
#         # 替换为你的环境路径
#         ENV_PYTHON_PATH = "/root/miniconda3/envs/brjl/bin/python"

#         # 调用函数（仅返回值多了stop_event，其他参数完全不变）
#         vllm_proc, stop_event = start_vllm_openai_api_server(
#             env_configs='{"CUDA_VISIBLE_DEVICES": "0","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}',
#             vllm_env_path=ENV_PYTHON_PATH,
#             vllm_port="8911",
#             vllm_tensor_parallel_size="1",
#             vllm_gpu_memory_utilization="0.9",
#             vllm_model="/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/"
#         )

#         logger.info("=" * 50)
#         logger.info("vllm启动完成，可正常调用model.batch接口")

#         # 业务完成后停止vllm（按需调用）
#         # stop_vllm_server(vllm_proc, stop_event)

#     except Exception as e:
#         logger.error(f"错误：{e}")
