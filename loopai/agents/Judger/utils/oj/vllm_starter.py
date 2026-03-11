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

def start_vllm_openai_api_server(
    env_configs, 
    vllm_env_path,  # 新增：指定环境的Python可执行文件路径（核心新增参数）
    vllm_port, 
    vllm_tensor_parallel_size, 
    vllm_gpu_memory_utilization, 
    vllm_model,
    poll_interval: float = 2.0,
    max_timeout: float = 300.0
) -> subprocess.Popen:
    """
    启动vllm openai兼容api服务（支持指定环境Python路径启动）
    :env_configs: 评估模型vllm启动环境参数
    :vllm_env_path: 指定环境的Python可执行文件路径（如/root/envs/vllm/bin/python）
    :vllm_port: vllm启动参数——port
    :vllm_tensor_parallel_size: vllm启动参数——tensor_parallel_size
    :vllm_gpu_memory_utilization: vllm启动参数——gpu_memory_utilization
    :param poll_interval: 端口轮询检测间隔（秒）
    :param max_timeout: 最大等待超时时间（秒）
    :return: vllm子进程对象
    """
    # ========== 新增：校验指定环境Python路径的有效性 ==========
    if not os.path.exists(vllm_env_path):
        raise Exception(f"指定的环境Python路径不存在：{vllm_env_path}")
    if not os.access(vllm_env_path, os.X_OK):
        raise Exception(f"指定的Python路径无执行权限：{vllm_env_path}")
    logger.info(f"已确认环境Python路径有效：{vllm_env_path}")

    vllm_command = f"{vllm_env_path} -m vllm.entrypoints.openai.api_server --model {vllm_model} --port {vllm_port} --tensor-parallel-size {vllm_tensor_parallel_size} --trust-remote-code --gpu-memory-utilization {vllm_gpu_memory_utilization} --enable-auto-tool-choice --tool-call-parser hermes"
    env_configs = json.loads(env_configs)
    process_pattern = "vllm.entrypoints.openai.api_server"
    port = parse_port_from_command_str(vllm_command)
    host = "localhost"

    # 设置NCCL和CUDA环境变量（原有逻辑不变）
    for key, value in env_configs.items():
        os.environ[key] = value
    logger.info("已设置GPU和NCCL环境变量")

    # 启动vllm子进程（原有逻辑不变）
    proc = subprocess.Popen(
        vllm_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding='utf-8',
        bufsize=1,  # 行缓冲，避免日志堆积，加快读取速度
        shell=True
    )

    # 轮询检测端口可用性，判断服务是否就绪（原有逻辑不变）
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

# # ========== 新增调用示例：传入环境路径启动vllm ==========
# if __name__ == "__main__":
#     # 调用函数启动vllm服务（基于端口检测）
#     try:
#         # 替换为你服务器上的实际环境Python路径
#         ENV_PYTHON_PATH = "/root/envs/vllm_env/bin/python"  # 示例：虚拟环境Python路径
#         # ENV_PYTHON_PATH = "/usr/local/anaconda3/envs/vllm/bin/python"  # Anaconda环境示例

#         vllm_proc = start_vllm_openai_api_server(
#             env_configs='{"CUDA_VISIBLE_DEVICES": "0","NCCL_P2P_DISABLE": "1","NCCL_IB_DISABLE": "1","NCCL_DEBUG": "INFO","NCCL_SOCKET_IFNAME": "lo","NCCL_BLOCKING_WAIT": "1"}',
#             env_python_path=ENV_PYTHON_PATH,  # 传入指定的环境Python路径
#             vllm_port="8911",
#             vllm_tensor_parallel_size="1",
#             vllm_gpu_memory_utilization="0.9",
#             vllm_model="/root/brjverl/models/Qwen2.5-Coder-7B-Instruct/"
#         )

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
#         logger.error(f"错误：{e}")  # 改为error级别更易识别错误
