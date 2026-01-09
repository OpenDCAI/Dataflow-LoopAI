import json
import os
import shutil
import tempfile
from typing import Optional, Callable

from langgraph.config import get_stream_writer
from loopai.schema.events import StreamEvent

from loopai.schema.states import LoopAIState
from loopai.agents import BaseAgent

from loopai.logger import get_logger
logger = get_logger()

def data_format(state):
    judger_state = state.get("judger", {})
    """选择适配器"""
    method = judger_state['eval_format_type']
    match method:
        case "human-eval":  # human-eval
            return preprocess_json_file(state, judger_state['eval_problem_path'], judger_state['eval_problem_format_path'], human_eval_format)
        case _:  # 通配符（类似 switch 的 default）
            return preprocess_json_file(state, judger_state['eval_problem_path'], judger_state['eval_problem_format_path'], human_eval_format)


def human_eval_format(line):
    """
    问题集标准目标json格式
    {
        "task_id": "test/0",          # 题号:{问题集名}/{序号}
        "prompt": "def return1():\n    \"\"\"This function has no input parameters, and your task is to make it return the integer 1.\n    \"\"\"", # 函数定义+问题描述提示（以多行注释形式写在函数定义下），为了减少处理过程，需保证大模型生成的结果为完整函数，不包含其他文本内容
        "entry_point": "return1",     # 函数名
        "canonical_solution": "def return1():\n    return 1", # 标准程序，需要完整的(包含函数定义)代码
        "test_list":["assert return1() == 1"] # 测试用例列表，其中的函数名需要和entry_point一致
    }

    样例集标准目标json格式
    {
        "task_id": "test/0",          # 题号:{问题集名}/{序号}
        "completion":"def return1():\n    \"\"\"This function has no input parameters, and your task is to make it return the integer 1.\n    \"\"\"\n    return 1" # 完整的样例代码
    }
    """
    if not isinstance(line, dict):
        """验证是否为字典（避免非 JSON 对象格式，比如数组、字符串"""
        logger.error(f"警告：行数据不是 JSON 对象（是 {type(line).__name__}），返回空字典")
        return {}
    line["canonical_solution"] = line["prompt"] + line["canonical_solution"]

    # 划分测试用例
    parts = line["test"].split("assert ")
    result = [parts[0]] + [f"assert {part}" for part in parts[1:]]
    cleaned_array = []
    for item in result:
        """去除末尾的空格和\n"""
        cleaned_item = item.rstrip(' \n\r')
        cleaned_array.append(cleaned_item)
    
    """存储筛选后的结果"""
    filtered_lines = [] 
    for _line in cleaned_array:
        """判断开头是否为assert """
        if _line.startswith("assert "):
            filtered_lines.append(_line.replace("candidate",line["entry_point"]))
    
    """strip() 去除首尾空格"""
    result_lines = [
        _line for _line in filtered_lines
        if _line.strip().startswith("assert ")
    ]

    line["test"] = result_lines

    old_key = "test"
    new_key = "test_list"
    if old_key in line:
        line[new_key] = line[old_key]
        del line[old_key]
    return line

def preprocess_json_file(
    state: LoopAIState,
    input_path: str,
    output_path: str,
    line_processor: Optional[Callable[[dict], Optional[dict]]] = None
) -> None:
    """
    按行预处理JSON文件（JSONL格式），支持输入输出路径相同（大文件友好）
    Args:
        input_path: 输入JSON文件路径（每行一个JSON对象）
        output_path: 处理后输出文件路径（可与输入路径相同）
        line_processor: 自定义行处理函数（可选），接收每行解析后的dict，返回处理后的dict；
                       返回None则过滤该行数据（不写入输出文件
    """
    # 统计变量
    total_lines = 0
    success_lines = 0
    error_lines = 0
    filtered_lines = 0
    cnt_lines = 0
    temp_file = None  # 临时文件句柄（用于后续清理）
    writer = get_stream_writer()
    try:
        # 判断是否需要用临时文件（输入输出路径相同，或为了安全）
        use_temp_file = (input_path == output_path)
        
        # 打开文件：同路径→写临时文件；不同路径→直接写目标文件
        if use_temp_file:
            # 创建临时文件（与原文件同目录，避免跨分区移动）
            temp_dir = os.path.dirname(os.path.abspath(input_path))
            temp_file = tempfile.NamedTemporaryFile(
                mode='w', 
                encoding='utf-8', 
                dir=temp_dir, 
                delete=False  # 手动控制删除，避免中途关闭
            )
            outfile = temp_file
        else:
            outfile = open(output_path, 'w', encoding='utf-8')

        # 读取并处理文件
        with open(input_path, 'r', encoding='utf-8') as infile:
            total_lines = sum(1 for _ in infile)
        with open(input_path, 'r', encoding='utf-8') as infile, outfile:
            for line_num, line in enumerate(infile, start=1):
                cnt_lines += 1
                # total_lines += 1
                line = line.strip()
                if not line:
                    continue  # 跳过空行
                
                # JSON解析
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        error_lines += 1
                        continue
                except json.JSONDecodeError as e:
                    error_lines += 1
                    continue
                
                # 调用处理函数
                if line_processor is not None:
                    processed_data = line_processor(data)
                else:
                    processed_data = data
                
                # 过滤或写入数据
                if processed_data is None:
                    filtered_lines += 1
                else:
                    json.dump(processed_data, outfile, ensure_ascii=False)
                    outfile.write('\n')
                    success_lines += 1

                if writer:
                    writer(StreamEvent(
                        current=state['current'],
                        progress=round(cnt_lines/total_lines, 1),
                        message="任务数据格式化中",
                        data={"success": success_lines, "filtered": filtered_lines, "error": error_lines, "progress": f"{cnt_lines}/{total_lines}"}
                    ).json())

        # 关键：如果用了临时文件，替换原文件（原子操作，避免文件损坏）
        if use_temp_file and temp_file:
            # 跨平台兼容：Windows需要先删除原文件，Unix可直接替换
            if os.path.exists(output_path):
                os.remove(output_path)
            # 移动临时文件到目标路径（shutil.move跨平台兼容）
            shutil.move(temp_file.name, output_path)
            logger.info(f"临时文件已替换原文件 → {output_path}")

        # 输出统计信息
        logger.info(f"\n预处理完成！")
        logger.info(f"总读取行数：{total_lines}")
        logger.info(f"成功处理行数：{success_lines}")
        logger.info(f"JSON解析错误行数：{error_lines}")
        logger.info(f"被过滤行数：{filtered_lines}")
        logger.info(f"输出文件路径：{output_path}")

    except FileNotFoundError:
        logger.error(f"错误：输入文件不存在 → {input_path}")
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message="任务数据格式化出错",
                data={"msg": f"错误：输入文件不存在 → {input_path}"}
            ).json())
    except PermissionError:
        logger.error(f"错误：无读写权限 → 输入文件：{input_path} / 输出文件：{output_path}")
        if writer:
            writer(StreamEvent(
                current=state['current'],
                progress=1.0,
                message="任务数据格式化出错",
                data={"msg": f"错误：无读写权限 → 输入文件：{input_path} / 输出文件：{output_path}"}
            ).json())
    except Exception as e:
        # 出错时清理临时文件，避免残留
        if use_temp_file and temp_file and os.path.exists(temp_file.name):
            os.remove(temp_file.name)
            logger.error(f"处理失败，已清理临时文件 → {temp_file.name}")
            if writer:
                writer(StreamEvent(
                    current=state['current'],
                    progress=1.0,
                    message="任务数据格式化出错",
                    data={"msg": f"意外错误：{str(e)}，已清理临时文件 → {temp_file.name}"}
                ).json())
        logger.error(f"意外错误：{str(e)}")
    finally:
        # 确保临时文件句柄关闭（即使出错）
        if temp_file:
            temp_file.close()
# preprocess_json_file("/root/brjverl/verl/compiler/data/copy.jsonl","/root/brjverl/verl/compiler/data/copy2.jsonl",data_format)
