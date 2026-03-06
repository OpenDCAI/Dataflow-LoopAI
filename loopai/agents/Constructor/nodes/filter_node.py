"""
Data Cleaning Subgraph - 数据清洗子图

在 mapping_subgraph 之前对中间格式数据进行清洗。
包含两个核心节点：
1. planner_node: 规划需要调用的工具（基础工具强制 + LLM判断领域工具）
2. process_node: 按顺序执行工具计划
"""
import os
import json
import re
import random
from typing import Dict, Any, List, Optional

from langgraph.graph import StateGraph
from langgraph.config import get_stream_writer
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from loopai.schema.states import LoopAIState
from loopai.schema.events import StreamEvent
from loopai.logger import get_logger
from loopai.agents.BaseAgent.base_agent import BaseAgent
from loopai.agents.Constructor.tools.data_filter_tools import (
    basic_data_flitter,
    domain_text2sql_cleaner,
    domain_code_gen_cleaner,
    domain_normal_data_cleaner,
    benchmark_data_cleaner
)

logger = get_logger()


_cleaning_agent_instance: Optional['CleaningSubgraph'] = None


def _get_cleaning_agent() -> 'CleaningSubgraph':
    """获取全局 CleaningSubgraph 实例（单例模式）"""
    global _cleaning_agent_instance
    if _cleaning_agent_instance is None:
        _cleaning_agent_instance = CleaningSubgraph()
    return _cleaning_agent_instance


# 工具映射字典：将工具名称映射到对应的工具函数
TOOL_MAP = {
    "basic_data_flitter": basic_data_flitter,
    "text2sql": domain_text2sql_cleaner,
    "code_generate": domain_code_gen_cleaner,
    "normal_data": domain_normal_data_cleaner,
    "benchmark_cleaner": benchmark_data_cleaner,
}


def _sample_intermediate_data(data_path: str, max_samples: int) -> None:
    """
    对基础清洗后的中间数据执行采样。
    当记录数超过 max_samples 时，随机采样到 max_samples 条并覆写原文件。
    支持单个 JSONL 文件或包含多个 JSONL 文件的目录。
    """
    if not data_path or not os.path.exists(data_path):
        return

    jsonl_files: List[str] = []
    if os.path.isfile(data_path) and data_path.endswith(".jsonl"):
        jsonl_files = [data_path]
    elif os.path.isdir(data_path):
        jsonl_files = [
            os.path.join(data_path, f)
            for f in os.listdir(data_path)
            if f.endswith(".jsonl") and os.path.isfile(os.path.join(data_path, f))
        ]

    if not jsonl_files:
        return

    file_records: Dict[str, List[str]] = {}
    total = 0
    for fp in jsonl_files:
        lines: List[str] = []
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line.rstrip("\n"))
        file_records[fp] = lines
        total += len(lines)

    if total <= max_samples:
        logger.info(
            f"Post-basic-cleaning sampling: {total} records <= {max_samples}, no sampling needed"
        )
        return

    logger.info(
        f"Post-basic-cleaning sampling: {total} records > {max_samples}, "
        f"sampling down to {max_samples}"
    )

    for fp, lines in file_records.items():
        share = max(1, int(max_samples * len(lines) / total)) if lines else 0
        share = min(share, len(lines))
        if len(lines) > share:
            file_records[fp] = random.sample(lines, share)

    for fp, lines in file_records.items():
        with open(fp, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    sampled_total = sum(len(v) for v in file_records.values())
    logger.info(f"Post-basic-cleaning sampling complete: {total} -> {sampled_total}")


def _read_jsonl_file(filepath: str) -> List[Dict[str, Any]]:
    """读取JSONL文件"""
    records = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON line in {filepath}: {e}")
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
    return records


def _extract_user_query(state: LoopAIState) -> str:
    """从state中提取user_query"""
    # 优先从 automated_query 获取
    if state.get("automated_query"):
        return state.get("automated_query", "")
    
    # 从 messages 中提取最后一个 HumanMessage
    if state.get("messages") and len(state["messages"]) > 0:
        from langchain_core.messages import HumanMessage
        
        # 从后往前搜索最后一个 HumanMessage
        for message in reversed(state["messages"]):
            if isinstance(message, HumanMessage):
                if hasattr(message, "content"):
                    return message.content
            elif isinstance(message, dict):
                msg_type = message.get("type", "")
                msg_role = message.get("role", "")
                if msg_type == "human" or msg_role == "human" or msg_type == "HumanMessage":
                    content = message.get("content", "")
                    if content:
                        return content
            elif hasattr(message, "type") and message.type == "human":
                if hasattr(message, "content"):
                    return message.content
    
    return ""


def _parse_json_list(response_text: str) -> List[str]:
    """解析LLM返回的JSON列表"""
    try:
        # 尝试直接解析JSON
        parsed = json.loads(response_text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    
    # 尝试提取代码块中的JSON
    pattern = r'```(?:json)?\s*(\[[\s\S]*?\])```'
    match = re.search(pattern, response_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    # 尝试查找方括号内的内容
    start_idx = response_text.find('[')
    end_idx = response_text.rfind(']')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            parsed = json.loads(response_text[start_idx:end_idx + 1])
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    
    logger.warning(f"Could not parse JSON list from LLM response: {response_text}")
    return []


def planner_node(state: LoopAIState) -> LoopAIState:
    """
    规划节点：决定需要调用哪些工具
    
    逻辑：
    1. 先执行 basic_data_flitter 进行初始过滤
    2. 基于过滤后的数据调用LLM判断是否需要领域工具
    3. 添加领域工具（如果有）
    """
    logger.info("=== Cleaning Subgraph: Planner Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 规划清洗工具",
            progress=0.0,
            data={"phase": "data_cleaning", "node": "planner"},
        ).json())

    try:
        # 1. 初始化 tool_plan，先添加基础工具 basic_data_flitter（始终执行，用于基础数据过滤）
        tool_plan: List[str] = ["basic_data_flitter"]
        
        # 2. 获取输入数据（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        intermediate_data_path = constructor_state.get("intermediate_data_path", "")
        if not intermediate_data_path:
            logger.warning("No intermediate data path found, skipping domain tool planning")
            if "constructor" not in state:
                state["constructor"] = {}
            state["constructor"]["cleaning_tool_plan"] = tool_plan
            return state
        
        if not os.path.exists(intermediate_data_path):
            logger.warning(f"Intermediate data path does not exist: {intermediate_data_path}")
            if "constructor" not in state:
                state["constructor"] = {}
            state["constructor"]["cleaning_tool_plan"] = tool_plan
            return state
        
        # 获取 user_query 和 category（从 state.constructor 中获取）
        user_query = _extract_user_query(state)
        category = constructor_state.get("category", "PT").upper()
        
        logger.info(f"Planning tools for data path: {intermediate_data_path}")
        logger.info(f"User query: {user_query[:100] if user_query else 'N/A'}...")
        logger.info(f"Category: {category}")
        
        # 3. 基于 user_query 和 datasets_background 使用LLM判断领域工具
        # 从 state.constructor 中获取 user_query 和 datasets_background（constructor_state 已在上面获取）
        user_query_from_state = constructor_state.get("user_query", "")
        datasets_background = constructor_state.get("datasets_background", "")
        
        # 如果 user_query 为空，尝试从其他地方获取
        if not user_query_from_state:
            user_query_from_state = user_query
        
        logger.info(f"User query from state: {user_query_from_state[:100] if user_query_from_state else 'N/A'}...")
        logger.info(f"Datasets background: {datasets_background[:100] if datasets_background else 'N/A'}...")
        
        # 使用LLM判断应该使用哪个领域工具
        domain_tools = []
        
        try:
            # 获取 CleaningSubgraph 实例以使用 BaseAgent 的方法
            cleaning_agent = _get_cleaning_agent()
            
            # 从 state 创建 LLM 实例（使用 BaseAgent 的方式）
            llm = cleaning_agent.create_llm_from_state(state)
            
            if llm:
                # 使用 BaseAgent 的 prompt_loader 加载 prompt（规范调用）
                system_prompt = cleaning_agent.get_prompt("system", "domain_tool_planner_prompt")
                task_prompt = cleaning_agent.get_prompt("task", "domain_tool_planner_prompt")
                
                # 构建用户提示词，包含用户需求和数据集背景
                user_prompt = task_prompt.format(
                    user_query=user_query_from_state if user_query_from_state else "Not provided",
                    datasets_background=datasets_background if datasets_background else "Not provided"
                )
                
                # 构建消息
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ]
                
                # 调用LLM（同步调用）
                response = llm.invoke(messages)
                response_text = response.content.strip()
                
                logger.debug(f"LLM response for domain tool planning: {response_text}")
                
                # 解析JSON响应
                domain_tools = _parse_json_list(response_text)
                
                # 验证工具名称是否有效
                valid_domain_tools = [
                    tool for tool in domain_tools
                    if tool in ["text2sql", "code_generate", "normal_data"]
                ]
                
                if valid_domain_tools:
                    tool_plan.extend(valid_domain_tools)
                    logger.info(f"LLM suggested domain tools based on user_query and datasets_background: {valid_domain_tools}")
                else:
                    # 如果LLM没有返回有效工具，使用默认的 normal_data
                    tool_plan.append("normal_data")
                    logger.info("LLM did not suggest any valid domain tools, using default normal_data")
            else:
                logger.warning("Missing LLM configuration, using default normal_data tool")
                tool_plan.append("normal_data")
                
        except Exception as e:
            logger.error(f"Error calling LLM for domain tool planning: {e}", exc_info=True)
            # LLM调用失败时，使用默认的 normal_data
            tool_plan.append("normal_data")
            logger.info("Using default normal_data tool due to LLM error")
        
        # 4. 更新State（确保 tool_plan 被正确设置到 state.constructor 中）
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["cleaning_tool_plan"] = tool_plan
        logger.info(f"Final tool_plan: {tool_plan}")
        logger.debug(f"State after planner_node: cleaning_tool_plan = {state.get('constructor', {}).get('cleaning_tool_plan')}")
        
    except Exception as e:
        logger.error(f"Error in planner_node: {e}", exc_info=True)
        # 发生错误时，至少保证基础工具被执行
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["cleaning_tool_plan"] = ["basic_data_flitter"]
        state["exception"] = f"Error in planner_node: {str(e)}"
    
    logger.info("=== Cleaning Subgraph: Planner Node Completed ===")
    if writer:
        tool_plan = state.get("constructor", {}).get("cleaning_tool_plan", [])
        writer(StreamEvent(
            current=current,
            message=f"Constructor: 数据清洗 - 规划完成，将执行 {len(tool_plan)} 个工具",
            progress=0.2,
            data={"phase": "data_cleaning", "node": "planner", "tool_plan": tool_plan},
        ).json())
    logger.debug(f"Returning state with cleaning_tool_plan: {state.get('constructor', {}).get('cleaning_tool_plan')}")
    return state


def process_node(state: LoopAIState) -> LoopAIState:
    """
    执行节点：按顺序执行工具计划
    
    逻辑：
    1. 获取 tool_plan
    2. 遍历执行每个工具
    3. 更新数据路径（链式调用）
    4. 更新State
    """
    logger.info("=== Cleaning Subgraph: Process Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - 执行清洗工具",
            progress=0.2,
            data={"phase": "data_cleaning", "node": "process"},
        ).json())

    try:
        # 1. 获取 tool_plan（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        tool_plan = constructor_state.get("cleaning_tool_plan", [])
        logger.debug(f"Process node received state with cleaning_tool_plan: {tool_plan}")
        logger.debug(f"Full state keys: {list(state.keys())}")
        logger.debug(f"Constructor state keys: {list(constructor_state.keys())}")
        
        if not tool_plan:
            logger.warning("No tool_plan found, skipping process node")
            logger.warning(f"Available state keys: {list(state.keys())}")
            logger.warning(f"Constructor state: {constructor_state}")
            return state
        
        logger.info(f"Executing tool_plan: {tool_plan}")
        
        # 2. 获取数据路径（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        current_data_path = constructor_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("No intermediate data path found, skipping tool execution")
            return state
        
        if not os.path.exists(current_data_path):
            logger.warning(f"Data path does not exist: {current_data_path}")
            return state
        
        # 3. 遍历执行工具
        cleaning_results = {
            "tools_executed": [],
            "tools_failed": [],
            "final_data_path": current_data_path
        }
        
        total_tools = len(tool_plan)
        max_samples = int(constructor_state.get("max_samples_before_cleaning", 1000) or 0)
        sampling_done_after_basic = False
        for tool_idx, tool_name in enumerate(tool_plan):
            try:
                if writer:
                    progress = 0.2 + 0.55 * (tool_idx / max(total_tools, 1))
                    writer(StreamEvent(
                        current=current,
                        message=f"Constructor: 数据清洗 - 执行工具 ({tool_idx + 1}/{total_tools}) {tool_name}",
                        progress=progress,
                        progress_num=tool_idx + 1,
                        total=total_tools,
                        data={"phase": "data_cleaning", "node": "process", "tool": tool_name},
                    ).json())
                logger.info(f"Executing tool: {tool_name}")
                
                # 从TOOL_MAP获取工具函数
                if tool_name not in TOOL_MAP:
                    logger.warning(f"Unknown tool: {tool_name}, skipping")
                    cleaning_results["tools_failed"].append({
                        "tool": tool_name,
                        "error": "Tool not found in TOOL_MAP"
                    })
                    continue
                
                tool_func = TOOL_MAP[tool_name]
                
                # 调用工具函数（同步调用）
                result = tool_func(current_data_path, state)
                
                # 检查工具是否执行成功（通过 success 字段或检查是否有错误）
                tool_success = getattr(result, 'success', True)  # 向后兼容：默认为 True
                error_message = getattr(result, 'error_message', '')
                
                if not tool_success:
                    logger.warning(f"Tool {tool_name} failed: {error_message}")
                    cleaning_results["tools_failed"].append({
                        "tool": tool_name,
                        "error": error_message or "Tool returned success=False"
                    })
                    # 工具失败时不更新路径，继续执行下一个工具
                    continue
                
                # 更新数据路径（链式调用）
                new_data_path = result.cleaned_data_path or current_data_path
                if new_data_path != current_data_path:
                    logger.info(f"Tool {tool_name} updated data path: {current_data_path} -> {new_data_path}")
                    current_data_path = new_data_path

                # 在基础清洗成功后执行采样，减少后续领域工具处理的数据量。
                if (
                    tool_name == "basic_data_flitter"
                    and not sampling_done_after_basic
                    and max_samples > 0
                    and current_data_path
                ):
                    _sample_intermediate_data(current_data_path, max_samples)
                    sampling_done_after_basic = True
                
                # 记录执行结果
                cleaning_results["tools_executed"].append({
                    "tool": tool_name,
                    "result": result
                })
                
                logger.info(f"Tool {tool_name} completed successfully")
                
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
                cleaning_results["tools_failed"].append({
                    "tool": tool_name,
                    "error": str(e)
                })
                # 继续执行下一个工具，不中断流程
        
        # 4. 更新State（更新 state.constructor 中的数据路径和清洗结果）
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["intermediate_data_path"] = current_data_path
        state["constructor"]["cleaning_results"] = cleaning_results
        
        logger.info(f"Process node completed. Final data path: {current_data_path}")
        logger.info(f"Tools executed: {len(cleaning_results['tools_executed'])}, "
                   f"Tools failed: {len(cleaning_results['tools_failed'])}")
        if writer:
            writer(StreamEvent(
                current=current,
                message=f"Constructor: 数据清洗 - 执行完成，共 {len(cleaning_results['tools_executed'])} 个工具",
                progress=0.75,
                data={"phase": "data_cleaning", "node": "process", "tools_executed": len(cleaning_results["tools_executed"])},
            ).json())

    except Exception as e:
        logger.error(f"Error in process_node: {e}", exc_info=True)
        state["exception"] = f"Error in process_node: {str(e)}"

    logger.info("=== Cleaning Subgraph: Process Node Completed ===")
    return state


def benchmark_cleaner_node(state: LoopAIState) -> LoopAIState:
    """
    Benchmark 清洗节点：移除与 benchmark 数据集相似/重复的记录
    
    此节点在领域工具清洗之后执行，根据 state.banckmark_jsonl_path 指定的
    benchmark 数据集，从清洗后的数据中移除相似的记录，防止测试数据泄露。
    
    逻辑：
    1. 检查 banckmark_jsonl_path 是否存在
    2. 如果存在，调用 benchmark_data_cleaner 工具执行清洗
    3. 更新数据路径和清洗结果
    """
    logger.info("=== Cleaning Subgraph: Benchmark Cleaner Node ===")
    current = state.get("current", "ConstructorAgent.data_cleaning")
    writer = get_stream_writer()
    if writer:
        writer(StreamEvent(
            current=current,
            message="Constructor: 数据清洗 - Benchmark 去重",
            progress=0.8,
            data={"phase": "data_cleaning", "node": "benchmark_cleaner"},
        ).json())

    try:
        # 1. 检查是否配置了 benchmark 路径
        benchmark_path = state.get("banckmark_jsonl_path", "")
        if not benchmark_path:
            logger.info("No benchmark_jsonl_path configured, skipping benchmark cleaning")
            if writer:
                writer(StreamEvent(
                    current=current,
                    message="Constructor: 数据清洗 - 未配置 Benchmark，跳过",
                    progress=1.0,
                    data={"phase": "data_cleaning", "node": "benchmark_cleaner", "skipped": True},
                ).json())
            return state

        import os
        if not os.path.exists(benchmark_path):
            logger.warning(f"Benchmark path does not exist: {benchmark_path}, skipping benchmark cleaning")
            return state
        
        # 2. 获取当前数据路径（从 state.constructor 中获取）
        constructor_state = state.get("constructor", {})
        current_data_path = constructor_state.get("intermediate_data_path", "")
        if not current_data_path:
            logger.warning("No intermediate data path found, skipping benchmark cleaning")
            return state
        
        if not os.path.exists(current_data_path):
            logger.warning(f"Data path does not exist: {current_data_path}, skipping benchmark cleaning")
            return state
        
        logger.info(f"Executing benchmark cleaner on: {current_data_path}")
        logger.info(f"Using benchmark file: {benchmark_path}")
        
        # 3. 调用 benchmark_data_cleaner 工具
        result = benchmark_data_cleaner(current_data_path, state)
        
        # 4. 检查工具执行结果
        tool_success = getattr(result, 'success', True)
        error_message = getattr(result, 'error_message', '')
        
        if not tool_success:
            logger.warning(f"Benchmark cleaner failed: {error_message}")
            # 工具失败时，记录错误但不中断流程
            if "constructor" not in state:
                state["constructor"] = {}
            cleaning_results = state["constructor"].get("cleaning_results", {})
            if "tools_failed" not in cleaning_results:
                cleaning_results["tools_failed"] = []
            cleaning_results["tools_failed"].append({
                "tool": "benchmark_cleaner",
                "error": error_message or "Tool returned success=False"
            })
            state["constructor"]["cleaning_results"] = cleaning_results
            return state
        
        # 5. 更新数据路径（链式调用）
        new_data_path = result.cleaned_data_path or current_data_path
        if new_data_path != current_data_path:
            logger.info(f"Benchmark cleaner updated data path: {current_data_path} -> {new_data_path}")
        
        # 6. 更新 State
        if "constructor" not in state:
            state["constructor"] = {}
        state["constructor"]["intermediate_data_path"] = new_data_path
        
        # 更新清洗结果统计
        cleaning_results = state["constructor"].get("cleaning_results", {})
        if "tools_executed" not in cleaning_results:
            cleaning_results["tools_executed"] = []
        cleaning_results["tools_executed"].append({
            "tool": "benchmark_cleaner",
            "result": {
                "cleaned_data_path": result.cleaned_data_path,
                "total_records": result.total_records,
                "valid_records": result.valid_records,
                "invalid_records": result.invalid_records
            }
        })
        cleaning_results["final_data_path"] = new_data_path
        
        # 记录 benchmark 清洗结果
        cleaning_results["benchmark_cleaning"] = {
            "benchmark_path": benchmark_path,
            "records_removed": result.invalid_records,
            "records_kept": result.valid_records
        }
        
        state["constructor"]["cleaning_results"] = cleaning_results
        
        logger.info(f"Benchmark cleaner completed - "
                   f"total: {result.total_records}, "
                   f"kept: {result.valid_records}, "
                   f"removed: {result.invalid_records}")
        if writer:
            writer(StreamEvent(
                current=current,
                message=f"Constructor: 数据清洗 - Benchmark 完成，保留 {result.valid_records} 条",
                progress=1.0,
                data={
                    "phase": "data_cleaning",
                    "node": "benchmark_cleaner",
                    "valid_records": result.valid_records,
                    "invalid_records": result.invalid_records,
                },
            ).json())

    except Exception as e:
        logger.error(f"Error in benchmark_cleaner_node: {e}", exc_info=True)
        # 发生错误时，记录异常但不中断流程
        if "constructor" not in state:
            state["constructor"] = {}
        cleaning_results = state["constructor"].get("cleaning_results", {})
        if "tools_failed" not in cleaning_results:
            cleaning_results["tools_failed"] = []
        cleaning_results["tools_failed"].append({
            "tool": "benchmark_cleaner",
            "error": str(e)
        })
        state["constructor"]["cleaning_results"] = cleaning_results
        if writer:
            writer(StreamEvent(
                current=current,
                message="Constructor: 数据清洗 - Benchmark 节点结束",
                progress=1.0,
                data={"phase": "data_cleaning", "node": "benchmark_cleaner", "error": str(e)},
            ).json())

    logger.info("=== Cleaning Subgraph: Benchmark Cleaner Node Completed ===")
    return state


class CleaningSubgraph(BaseAgent):
    """
    数据清洗子图类
    
    继承 BaseAgent 以使用统一的 LLM 创建和 prompt 加载机制
    管理数据清洗的完整子图流程
    """
    
    @property
    def role_name(self) -> str:
        """Role name"""
        return "CleaningSubgraph"
    
    @property
    def system_prompt_type(self) -> str:
        """System prompt type"""
        return "system"
    
    @property
    def system_prompt_name(self) -> str:
        """System prompt name"""
        return "domain_tool_planner_prompt"
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = 'empty',
        temperature: float = 0.7,
        top_p: float = 0.95,
        max_completion_tokens: int = 4096,
        prompt_template_dir: Optional[str] = None,
        checkpointer: Optional[BaseCheckpointSaver] = None,
        store: Optional[BaseStore] = None
    ):
        """
        初始化 CleaningSubgraph
        
        Args:
            model_name: 模型名称
            base_url: LLM 服务器地址
            api_key: API 密钥
            temperature: 温度参数
            top_p: top_p 参数
            max_completion_tokens: 最大生成 token 数
            prompt_template_dir: prompt 模板目录
            checkpointer: 检查点保存器
            store: 状态存储
        """
        # 初始化 BaseAgent（不自动创建 LLM，因为我们需要从 state 动态获取配置）
        super().__init__(
            tools=[],
            model_name=None,  # 不在这里创建 LLM，从 state 动态获取
            base_url=None,
            api_key='empty',
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
            prompt_template_dir=prompt_template_dir,
            checkpointer=checkpointer,
            store=store
        )
        self.checkpointer = checkpointer
        self.store = store
        self.graph = None
    
    def create_llm_from_state(self, state: LoopAIState) -> Optional[ChatOpenAI]:
        """
        从 state 中获取配置并创建 LLM 实例
        
        Args:
            state: 当前状态
            
        Returns:
            ChatOpenAI 实例，如果配置不完整则返回 None
        """
        # 从 state.constructor 中获取配置
        constructor_state = state.get("constructor", {})
        model_name = constructor_state.get("model_path") or state.get("analyze_model_path")
        base_url = constructor_state.get("base_url") or state.get("analyze_base_url")
        api_key = constructor_state.get("api_key") or state.get("analyze_api_key")
        temperature = constructor_state.get("temperature", self.temperature)
        top_p = constructor_state.get("top_p", self.top_p)
        max_completion_tokens = constructor_state.get("max_completion_tokens", self.max_completion_tokens)
        
        if not (model_name and base_url and api_key):
            return None
        
        # 使用 BaseAgent 的方式创建 LLM
        if base_url is None:
            logger.error(f'Undefined base_url in {self.role_name}')
            raise AssertionError(f'Undefined base_url in {self.role_name}')
        
        llm = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model_name,
            temperature=temperature,
            top_p=top_p,
            max_completion_tokens=max_completion_tokens,
            tags=[self.llm_tag]
        )
        
        return llm
    
    def get_prompt(self, prompt_type: str, prompt_name: str) -> str:
        """
        获取 prompt（使用 BaseAgent 的 prompt_loader）
        
        Args:
            prompt_type: prompt 类型
            prompt_name: prompt 名称
            
        Returns:
            prompt 字符串
        """
        return self.prompt_loader(prompt_type, prompt_name)
    
    def init_graph(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def __call__(self):
        """实现 BaseAgent 的抽象方法（不使用）"""
        pass
    
    def build(self, **kwargs) -> StateGraph:
        """
        构建并编译清洗子图
        
        流程：planner_node -> process_node -> benchmark_cleaner_node -> END
        
        Returns:
            编译后的 StateGraph
        """
        builder = StateGraph(LoopAIState)
        
        # 添加节点
        builder.add_node("planner_node", planner_node)
        builder.add_node("process_node", process_node)
        builder.add_node("benchmark_cleaner_node", benchmark_cleaner_node)
        
        # 设置入口点
        builder.set_entry_point("planner_node")
        
        # 连线: planner -> process -> benchmark_cleaner -> END
        builder.add_edge("planner_node", "process_node")
        builder.add_edge("process_node", "benchmark_cleaner_node")
        builder.set_finish_point("benchmark_cleaner_node")
        
        # 编译图
        compile_kwargs = {}
        if self.checkpointer:
            compile_kwargs["checkpointer"] = self.checkpointer
        if self.store:
            compile_kwargs["store"] = self.store
        compile_kwargs.update(kwargs)
        
        self.graph = builder.compile(**compile_kwargs)
        return self.graph


def create_cleaning_subgraph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
    store: Optional[BaseStore] = None,
    **kwargs
) -> StateGraph:
    """
    创建清洗子图的便捷函数
    
    Args:
        checkpointer: 检查点保存器
        store: 状态存储
        **kwargs: 传递给 compile 的其他参数
    
    Returns:
        编译后的 StateGraph
    """
    subgraph = CleaningSubgraph(checkpointer=checkpointer, store=store)
    return subgraph.build(**kwargs)


def filter_node(state: LoopAIState, store: BaseStore = None) -> LoopAIState:
    """
    映射后的过滤节点（用于 mapping_subgraph）
    
    这是一个占位符函数，用于 mapping_subgraph 中的 filter_node。
    实际的清洗逻辑在 CleaningSubgraph 中实现。
    
    Args:
        state: 当前状态
        store: 状态存储（可选）
    
    Returns:
        更新后的状态
    """
    logger.info("=== Mapping Filter Node: Starting ===")
    # 占位符实现：直接返回状态，不做任何处理
    # 如果需要映射后的过滤逻辑，可以在这里实现
    logger.info("=== Mapping Filter Node: Completed ===")
    return state
