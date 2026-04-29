import json
import os
from typing import Any, Dict, List, Optional, Type, Union

from typing import Union
from langgraph.graph import StateGraph, END
from langgraph.runtime import Runtime
from langgraph.types import interrupt, Command

from pathlib import Path

from loopai.schema.states import LoopAIState, RuntimeContext, get_missing_fields
from loopai.agents import BaseAgent
from .utils.oj.generate import generate_sample_code, generate_sample_text2sql
from .utils.oj.evaluate import evaluate_sample_code, evaluate_sample_text2sql
from .utils.oj.format import data_format
from .utils.oj.data import check_jsonl_fields
from .utils.oj.vllm_starter import start_vllm_openai_api_server
from .utils.oj.vllm_starter import DEFAULT_VLLM_PORT
from .utils.oj.vllm_killer import kill_vllm_openai_api_server
from .utils.oj.vllm_check import check_vllm_running
from .nodes.eval_general_text_node import eval_general_text_node 
from .nodes.eval_general_text_node import set_gpu

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.logger import get_logger
logger = get_logger()

def _isNotNone(value):
    return value != "" and value is not None

def find_best_checkpoint(checkpoints: List[str], training_step_losses: List[Dict[str, Union[int, float]]]) -> str:
    """
    根据训练步骤的损失值，选择最合适的 checkpoint。
    参数:
        checkpoints: 字符串列表，每个元素格式如 'checkpoint-100'
        training_step_losses: 字典列表，每个字典包含 'step' 和 'loss' 字段

    返回:
        最佳匹配的 checkpoint 字符串
    """
    # 找到损失最小的步骤（损失最小，若相同则步骤最小）
    best_step = min(training_step_losses, key=lambda x: (x['loss'], x['step']))['step']

    # 从 checkpoints 中提取数值部分，并找到与 best_step 最接近的 checkpoint
    def extract_num(cp: str) -> int:
        return int(cp.split('-')[-1])

    # 使用 min 选择：先按距离排序，再按数值本身排序
    best_cp = min(checkpoints, key=lambda cp: (abs(extract_num(cp) - best_step), extract_num(cp)))

    return best_cp

class JudgerAgent(BaseAgent):
    @property
    def role_name(self) -> str:
        """Role name"""
        return "Judger"

    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"

    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "default_prompt"

    def get_check_required_fields_node(self):
        @BaseAgent.set_current
        def check_required_fields(state: LoopAIState, runtime: Runtime[RuntimeContext]):
            writer = get_stream_writer()
            required_fields = {
                'judger':["eval_api_key", "eval_temperature",
                        "eval_top_p", "eval_problem_path",
                        "eval_case_num", "eval_task_type"
                ],
                'default':["output_dir", "task_id"]
            }
            missing_fields = get_missing_fields(required_fields, state)
            if not missing_fields:
                # judger无模型参数则去查看trainer是否提供
                if _isNotNone(state.get("judger", {}).get("eval_model_path", "")) is not True :
                    trainer_task_id = state.get("trainer", {}).get("trainer_task_id", "")
                    training_checkpoints = state.get("trainer", {}).get("training_checkpoints", "")
                    training_step_losses = state.get("trainer", {}).get("training_step_losses", "")
                    output_dir = state.get("output_dir")
                    if _isNotNone(trainer_task_id) is True and _isNotNone(training_checkpoints) is True and _isNotNone(training_step_losses) is True:
                        logger.info("judger未提供模型参数，检测到trainer提供模型，将以trainer提供的模型进行评测")
                        best_checkpoint = find_best_checkpoint(training_checkpoints, training_step_losses)
                        state["judger"]["eval_model_path"] = f"{output_dir}/{state.get("task_id")}/trainer/{trainer_task_id}/{best_checkpoint}/"
                    else :
                        missing_fields = get_missing_fields({'judger':["eval_model_path"]}, state)

            """vllm启动检查"""
            base_url = state.get("judger", {}).get("eval_base_url", None)
            task_type = state.get("judger", {}).get("eval_task_type", "")
            logger.info(f"base_url:{base_url}")
            if(_isNotNone(base_url) and task_type!="general_text"):
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=0.0,
                        message="已设置[eval_base_url]，正在进行vllm启动检查",
                        data={"msg": f""}
                    ).json())

                res = check_vllm_running(base_url)

                if(res is True):
                    logger.info("已启动vllm")
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm已启动",
                            data={"msg": f""}
                        ).json())
                else:
                    logger.info("未启动vllm")
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm未启动",
                            data={"msg": f""}
                        ).json())
                    missing_fields.setdefault("judger", []).append("eval_base_url")

            """数据有效检查"""
            automated_query = self.prompt_loader(
                    "automated_query", "judger_missing_fields_prompt")
            problem_path = state.get("judger", {}).get("eval_problem_path", "")
            if not problem_path or not os.path.exists(problem_path):
                logger.info(f"problem_path {problem_path} not exists")
                automated_query = f"{automated_query}\nDetail: {problem_path} does not exist and make sure it is a valid file."
                missing_fields.setdefault("judger", []).append("eval_problem_path")
                    
            """检查text2sql必要字段"""
            if not missing_fields:
                if state.get("judger", {}).get("eval_task_type", "") == "text2sql":
                    missing_fields = get_missing_fields({'judger':["eval_text2sql_dir"]}, state)
            """检查general_text必要字段"""
            if not missing_fields:
                if state.get("judger", {}).get("eval_task_type", "") == "general_text":
                    missing_fields = get_missing_fields({'judger':["bench_dataflow_eval_type"]},state)
            logger.info(f"missing_fields:{missing_fields}")
            logger.info(f"bench_dataflow_eval_type:{state.get("judger", {}).get("bench_dataflow_eval_type")}")
            if missing_fields:
                logger.info("$"*50)
                logger.info(f"missing_fields:{missing_fields}")
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = automated_query
                state.setdefault('configer',{})['configer_error'] = f'Missing required fields: {json.dumps({"missing_fields": missing_fields}, ensure_ascii=False)}'
                goto_node = runtime.context['exception_navigate']
                logger.info(f'found missing fields, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )
            

            if task_type == "code":
                if state.get("judger", {}).get("eval_format_type", "") == "mbpp":
                    required_fields = ["text", "code", "task_id", "challenge_test_list", "test_list"]
                elif state.get("judger", {}).get("eval_format_type", "") == "human-eval":
                    required_fields = ["task_id", "prompt", "entry_point", "canonical_solution", "test"]
                else:
                    required_fields = ["task_id", "prompt", "entry_point", "canonical_solution", "test_list"]
            elif task_type == "text2sql":
                required_fields = ["task_id", "prompt", "db_id", "question", "ground_truth"]
            elif task_type == "general_text":
                check_file_fields = True
            
            if task_type == "code" or task_type =="text2sql":
                check_file_fields = check_jsonl_fields(state.get("judger", {}).get("eval_problem_path", ""), required_fields)

            if check_file_fields is not True:
                logger.info("$"*50)
                logger.info(["eval_problem_path"])
                state['exception'] = 'ConfigerError'
                state['next_to'] = 'config_node'
                state['automated_query'] = self.prompt_loader(
                    "automated_query", "judger_missing_fields_prompt")
                state.setdefault('configer',{})['configer_error'] = f'Wrong required fields: {json.dumps({"wrong_fields": "eval_problem_path"}, ensure_ascii=False)}'
                goto_node = runtime.context['exception_navigate']
                logger.info(f'found wrong fields, required fields missing in the file, goto {goto_node}')
                return Command(
                    update=state,
                    goto=goto_node,
                    graph=Command.PARENT
                )
            state["judger"]["output_result_path"] = ""
            state["judger"]["output_case_path"] = ""
            state["judger"]["output_problem_path"] = ""
        return check_required_fields

    @staticmethod
    @BaseAgent.set_current
    def check_param_type_node(state: LoopAIState) -> LoopAIState:
        eval_temperature = state.get("judger", {}).get("eval_temperature", None)
        if _isNotNone(eval_temperature):
            state["judger"]["eval_temperature"] = float(eval_temperature)

        eval_top_p = state.get("judger", {}).get("eval_top_p", None)
        if _isNotNone(eval_top_p):
            state["judger"]["eval_top_p"] = float(eval_top_p)

        eval_batch_size = state.get("judger", {}).get("eval_batch_size", None)
        if _isNotNone(eval_batch_size):
            state["judger"]["eval_batch_size"] = int(eval_batch_size)
        
        eval_case_num = state.get("judger", {}).get("eval_case_num", None)
        if _isNotNone(eval_case_num):
            state["judger"]["eval_case_num"] = int(eval_case_num)
        
        eval_vllm_port = state.get("judger", {}).get("eval_vllm_port", None)
        if _isNotNone(eval_vllm_port):
            state["judger"]["eval_vllm_port"] = int(eval_vllm_port)

        eval_vllm_tensor_parallel_size = state.get("judger", {}).get("eval_vllm_tensor_parallel_size", None)
        if _isNotNone(eval_vllm_tensor_parallel_size):
            state["judger"]["eval_vllm_tensor_parallel_size"] = int(eval_vllm_tensor_parallel_size)
        
        eval_vllm_gpu_memory_utilization = state.get("judger", {}).get("eval_vllm_gpu_memory_utilization", None)
        if _isNotNone(eval_vllm_gpu_memory_utilization):
            state["judger"]["eval_vllm_gpu_memory_utilization"] = float(eval_vllm_gpu_memory_utilization)

        return state

    @staticmethod
    @BaseAgent.set_current
    def vllm_kill_node(state: LoopAIState) -> LoopAIState:
        env_configs = state.get("judger", {}).get("eval_env_configs", None)
        vllm_port = state.get("judger", {}).get("eval_vllm_port", None)
        base_url = state.get("judger", {}).get("eval_base_url", None)
        writer = get_stream_writer()
        # 未设置base_url才会进入本地开启程序才会先关闭本地的vllm服务
        if  (not _isNotNone(base_url)) or (base_url == f"http://localhost:{vllm_port}/v1"):
            if(_isNotNone(env_configs) and _isNotNone(vllm_port)):
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=0.0,
                        message="vllm关闭",
                        data={"msg": f""}
                    ).json())

                res = kill_vllm_openai_api_server(vllm_port)

                if res is True:
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm已关闭",
                            data={"msg": f""}
                        ).json())
                else:
                    if writer:
                        writer(StreamEvent(
                            current=state['current'],
                            progress=1.0,
                            message="vllm关闭失败",
                            data={"msg": f""}
                        ).json())
            else:
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=1.0,
                        message="vllm开启结束",
                        data={"msg": f"因已开启自定义vllm服务而跳过该过程"}
                    ).json())
        return state

    @staticmethod
    @BaseAgent.set_current
    def vllm_start_node(state: LoopAIState) -> LoopAIState:
        # 设置 cuda_visible_devices
        set_gpu(state)

        env_configs = state.get("judger", {}).get("eval_env_configs", "{}")
        vllm_tensor_parallel_size = state.get("judger", {}).get("eval_vllm_tensor_parallel_size", 1)
        vllm_gpu_memory_utilization = state.get("judger", {}).get("eval_vllm_gpu_memory_utilization", 0.9)
        vllm_model = state.get("judger", {}).get("eval_model_path", None)

        writer = get_stream_writer()
        if(_isNotNone(env_configs) and _isNotNone(vllm_tensor_parallel_size) and _isNotNone(vllm_gpu_memory_utilization)):
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=0.0,
                    message="vllm准备启动",
                    data={"msg": f""}
                ).json())
            # try 捕捉异常，上报给前端日志信息
            try:
                start_vllm_openai_api_server(env_configs, vllm_tensor_parallel_size, vllm_gpu_memory_utilization, vllm_model)
                state["judger"]["eval_base_url"] = f"http://localhost:{DEFAULT_VLLM_PORT}/v1"
                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=1.0,
                        message="vllm已启动",
                        data={"msg": f""}
                    ).json())

            except Exception as e:
                logger.info("CUDA_VISIBLE_DEVICES from environment:", os.environ.get("CUDA_VISIBLE_DEVICES"))
                logger.error(f"[{bench.bench_name}] 评测失败: {e}")
                # 上报错误 直接完成结束
                _emit(
                    state['current'],
                    writer,
                    f"vllm 启动异常 ,请解决后重新评测：{e}",
                    progress=1.0,
                )
                # 直接结束 Judger 当前节点，不再跳转父图异常路由
                return state
        else:
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="vllm开启结束",
                    data={"msg": f"因参数[env_configs]或[vllm_tensor_parallel_size]或[vllm_gpu_memory_utilization]未设置而跳过该过程"}
                ).json())
        return state
    
    @staticmethod
    @BaseAgent.set_current
    def data_format_node(state: LoopAIState) -> LoopAIState:
        problem_path = state.get("judger", {}).get("eval_problem_path", "")
        format_type = state.get("judger", {}).get("eval_format_type", None)
        state_task_id = state.get("task_id")
        output_dir = Path(state.get("output_dir", "/"))
        problem_file_name = str(Path(problem_path).stem)

        target_format_path = output_dir / str(state_task_id) / "judger" / (problem_file_name + "_format.jsonl")
        writer = get_stream_writer()
        if _isNotNone(format_type):
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=0.0,
                    message="任务数据格式化开始",
                    data={"msg": f"由[{problem_path}]以[{format_type}]格式转存为[{str(target_format_path)}]"}
                ).json())
            data_format(state)
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化完成",
                    data={"msg": f"后续将以[{str(target_format_path)}]作为问题数据"}
                ).json())
            state["judger"]["eval_problem_path"] = str(target_format_path)
        else:
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化结束,未设置[eval_format_type]参数，跳过数据格式化过程",
                    # data={"msg": '未设置[eval_format_type]参数，跳过数据格式化过程'} -> 更新至 message 中
                ).json())
        state["judger"]["output_problem_path"] = state["judger"]["eval_problem_path"]
        return state

    @staticmethod
    @BaseAgent.set_current
    def generate_node(state: LoopAIState) -> Union[LoopAIState, Command]:
        task_type = state.get("judger", {}).get("eval_task_type", "code")
        
        match task_type:
            case "code":  # code
                res = generate_sample_code(state)
                state["judger"]["output_case_path"] = res
                goto = "evaluate"
                
                
            case "text2sql":
                res = generate_sample_text2sql(state)
                state["judger"]["output_case_path"] = res
                goto = "evaluate"
                
                
            case _:  # 所有其他类型（如 general_text）
                # 路由到 eval_general_text 节点
                goto="eval_general_text"
            
        return Command(goto=goto)

    @staticmethod
    @BaseAgent.set_current
    def evaluate_node(state: LoopAIState) -> LoopAIState:
        task_type = state.get("judger", {}).get("eval_task_type", "code")
        
        res = {}

        writer = get_stream_writer()
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=0.0,
                message=f"{task_type}任务评测样本开始",
                data={"msg": ''}
            ).json())
        match task_type:
            case "code":  # code
                res = evaluate_sample_code(state)
            case "text2sql":
                res = evaluate_sample_text2sql(state)
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message=f"{task_type}任务评测样本结果",
                data=res["pass_at_k"]
            ).json())
        state["judger"]["output_result_path"] = res["result_path"]
        return state

    @staticmethod
    @BaseAgent.set_current
    def check_param_type_next(state: LoopAIState) -> str:
        """
        如果已启动vllm则直接跳过本地启动阶段
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        task_type = state.get("judger", {}).get("eval_task_type","code")
        if _isNotNone(base_url) or task_type=="general_text":
            return "to_data_format"
        else:
            return "to_vllm_kill"

    @staticmethod
    @BaseAgent.set_current
    def evaluate_next(state: LoopAIState) -> str:
        """
        如果已完成则结束未完成则开启本地vllm
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        vllm_port = state.get("judger", {}).get("eval_vllm_port", DEFAULT_VLLM_PORT)
        # 判断是否为空，为空则开启本地
        if not _isNotNone(base_url):
            return "to_vllm_kill"
        else:
            # 判断是否为本地路由，如果是则kill掉本地vllm，否则直接结束
            if state["judger"]["eval_base_url"] == f"http://localhost:{vllm_port}/v1":
                return "to_vllm_kill"
            else:
                return "to_end"

    @staticmethod
    @BaseAgent.set_current
    def vllm_kill_next(state: LoopAIState) -> str:
        """
        如果已完成则结束未完成则开启本地vllm
        """
        base_url = state.get("judger", {}).get("eval_base_url", None)
        # 判断是否为空，为空则开启本地
        if not _isNotNone(base_url):
            return "to_vllm_start"
        else:
            return "to_end"

    def init_graph(self, **kwargs):
        builder = StateGraph(LoopAIState)
        builder.add_node("check_required_fields", self.get_check_required_fields_node())
        builder.add_node("check_param_type", self.check_param_type_node)
        builder.add_node("vllm_kill", self.vllm_kill_node)
        builder.add_node("vllm_start", self.vllm_start_node)
        builder.add_node("data_format", self.data_format_node)
        builder.add_node("generate_code", self.generate_node)
        builder.add_node("evaluate", self.evaluate_node)
        builder.add_node("eval_general_text", eval_general_text_node)
        
        builder.add_edge("check_required_fields", "check_param_type")
        builder.add_edge("vllm_start", "data_format")
        builder.add_edge("data_format", "generate_code")
        #builder.add_edge("generate_code", "evaluate")
        
        builder.set_entry_point("check_required_fields")
        builder.set_finish_point("eval_general_text")

        builder.add_conditional_edges(
            source="check_param_type",  # 来源节点（从哪个节点跳转出来）
            path=self.check_param_type_next,  # 路由函数（执行条件判断，返回路由键）
            path_map={  # 路由键-目标节点映射（路由键对应到具体节点）
                "to_vllm_kill": "vllm_kill",  # 路由键1 → 计算节点
                "to_data_format": "data_format",     # 路由键2 → 文本处理节点
            }
        )

        builder.add_conditional_edges(
            source="evaluate", 
            path=self.evaluate_next,
            path_map={
                "to_vllm_kill": "vllm_kill",
                "to_end": END,
            }
        )

        builder.add_conditional_edges(
            source="vllm_kill", 
            path=self.vllm_kill_next,
            path_map={
                "to_vllm_start": "vllm_start",
                "to_end": END,
            }
        )

        self.graph = builder.compile(
            checkpointer=self.checkpointer, store=self.store, **kwargs)

    def __call__(self, **kwargs):
        """
        build and return self.graph

        Args:
            kwargs: keyword arguments to pass to init_graph
        """
        self.init_graph(**kwargs)
        return self.graph
