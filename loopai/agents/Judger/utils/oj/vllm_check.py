import requests
from urllib.parse import urljoin  # 用于安全拼接URL，避免分隔符问题

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger
logger = get_logger()

def check_vllm_running(eval_base_url: str, timeout: int = 5) -> bool:
    """
    判断vLLM服务是否已启动
    :param eval_base_url: vLLM的基础URL（如 'http://127.0.0.1:8911/v1'）
    :param timeout: 请求超时时间（秒），默认5秒
    :return: 服务是否启动（True/False）
    """
    try:
        # 手动补全末尾的 /（推荐，简单直观）
        if not eval_base_url.endswith("/"):
            eval_base_url += "/"
        # 此时拼接 models 会得到 正确的 /v1/models
        check_url = urljoin(eval_base_url, "models")
        
        # 调试日志：输出最终请求地址，方便排查问题
        logger.info(f"正在检测vLLM服务，请求地址：{check_url}")
        
        # 发送轻量GET请求（移除无意义的 Content-Type 请求头）
        response = requests.get(
            check_url,
            timeout=timeout,  # 设置超时，避免长时间阻塞
        )
        
        # 判断响应状态码：200 OK 表示服务正常启动
        if response.status_code == 200:
            logger.info(f"✅ vLLM服务已正常启动，可用模型列表接口响应成功")
            # 可选：打印返回的模型列表（如需验证模型是否加载）
            # model_data = response.json()
            # logger.info(f"vLLM可用模型：{model_data.get('data', [])}")
            return True
        else:
            logger.error(f"❌ vLLM服务响应异常，状态码：{response.status_code}，响应内容：{response.text}")
            return False
    
    # 捕获服务未启动的核心异常（调整日志级别为 error）
    except requests.exceptions.ConnectionError:
        logger.error(f"❌ 无法连接到vLLM服务，地址：{eval_base_url}，服务可能未启动或端口错误")
        return False
    except requests.exceptions.Timeout:
        logger.error(f"❌ 连接vLLM服务超时（超时时间：{timeout}秒），服务可能繁忙或网络异常")
        return False
    except Exception as e:
        logger.error(f"❌ 检查vLLM服务状态时出现未知错误：{str(e)}")
        return False
