import json
from typing import Optional, Callable
def data_format(line):
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
        """3. 验证是否为字典（避免非 JSON 对象格式，比如数组、字符串"""
        print(f"警告：行数据不是 JSON 对象（是 {type(line).__name__}），返回空字典")
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
    input_path: str,
    output_path: str,
    line_processor: Optional[Callable[[dict], Optional[dict]]] = None
) -> None:
    """
    按行预处理JSON文件（JSONL格式），预留每行处理逻辑
    Args:
        input_path: 输入JSON文件路径（每行一个JSON对象）
        output_path: 处理后输出文件路径
        line_processor: 自定义行处理函数（可选），接收每行解析后的dict，返回处理后的dict；
                       返回None则过滤该行数据（不写入输出文件
    """

    """
    统计变量（可选，用于日志输出）：
        total_lines - 总读取行数
        success_lines - 成功解析并处理的行数
        error_lines - JSON解析错误的行数
        filtered_lines - 被过滤的行数（处理函数返回None）
    """
    total_lines = 0
    success_lines = 0
    error_lines = 0
    filtered_lines = 0

    try:
        """
        读写文件
        """
        with open(input_path, 'r') as infile, \
             open(output_path, 'w') as outfile:

            for line_num, line in enumerate(infile, start=1):
                total_lines += 1
                line = line.strip() 
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        error_lines += 1
                        continue

                except json.JSONDecodeError as e:
                    error_lines += 1
                    continue
                
                """
                如果传入了处理函数，调用函数处理；否则直接保留原数据
                """
                if line_processor is not None:
                    processed_data = line_processor(data)
                else:
                    processed_data = data

                if processed_data is None:
                    filtered_lines += 1
                    continue

                json.dump(processed_data, outfile, ensure_ascii=False)
                outfile.write('\n') 
                success_lines += 1

        print(f"\n预处理完成！")
        print(f"总读取行数：{total_lines}")
        print(f"成功处理行数：{success_lines}")
        print(f"JSON解析错误行数：{error_lines}")
        print(f"被过滤行数：{filtered_lines}")
        print(f"输出文件路径：{output_path}")

    except FileNotFoundError:
        print(f"错误：输入文件不存在 → {input_path}")
    except PermissionError:
        print(f"错误：无读写权限 → 输入文件：{input_path} / 输出文件：{output_path}")
    except Exception as e:
        print(f"意外错误：{str(e)}")

preprocess_json_file("/root/brjverl/verl/compiler/data/copy.jsonl","/root/brjverl/verl/compiler/data/copy2.jsonl",data_format)
