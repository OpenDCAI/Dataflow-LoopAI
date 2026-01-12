#!/usr/bin/env python3
"""
脚本用于提取 Obtainer 目录中所有 state[""] 和 state[''] 中的参数名称
记录每个参数出现的文件路径和行号
"""

import os
import re
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def find_python_files(directory: str) -> List[str]:
    """查找目录下所有 Python 文件"""
    python_files = []
    for root, dirs, files in os.walk(directory):
        # 跳过 __pycache__ 目录
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    return python_files


def extract_state_params_from_file(file_path: str) -> List[Tuple[int, str]]:
    """
    从文件中提取所有 state["key"] 或 state['key'] 中的 key
    
    返回: [(行号, 参数名), ...]
    """
    results = []
    # 正则表达式匹配 state["key"] 或 state['key']
    # 匹配模式: state["..."] 或 state['...']
    pattern = r'state\[["\']([^"\']+)["\']\]'
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, start=1):
                # 查找所有匹配
                matches = re.finditer(pattern, line)
                for match in matches:
                    param_name = match.group(1)
                    results.append((line_num, param_name))
    except Exception as e:
        print(f"读取文件 {file_path} 时出错: {e}")
    
    return results


def extract_state_params_from_directory(directory: str) -> Dict[str, Dict[str, List[int]]]:
    """
    从目录中提取所有 state 参数
    
    返回: {
        '参数名': {
            'files': ['文件路径1', '文件路径2'],
            'lines': [行号1, 行号2, ...]
        }
    }
    """
    # 存储结果: {参数名: {文件路径: [行号列表]}}
    param_locations: Dict[str, Dict[str, List[int]]] = defaultdict(lambda: defaultdict(list))
    
    python_files = find_python_files(directory)
    
    # 排除脚本文件本身
    script_name = os.path.basename(__file__)
    python_files = [f for f in python_files if os.path.basename(f) != script_name]
    
    print(f"找到 {len(python_files)} 个 Python 文件")
    
    for file_path in python_files:
        # 获取相对路径（相对于 Obtainer 目录）
        rel_path = os.path.relpath(file_path, directory)
        
        # 提取参数
        params = extract_state_params_from_file(file_path)
        
        for line_num, param_name in params:
            param_locations[param_name][rel_path].append(line_num)
    
    # 转换为最终格式
    result = {}
    for param_name, file_locations in param_locations.items():
        files = []
        lines = []
        for file_path, line_nums in file_locations.items():
            files.append(file_path)
            lines.extend([f"{file_path}:{line_num}" for line_num in line_nums])
        
        result[param_name] = {
            'files': sorted(set(files)),  # 去重并排序
            'lines': sorted(lines)  # 排序所有行号引用
        }
    
    return result


def save_results_json(results: Dict, output_file: str):
    """保存结果为 JSON 格式"""
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到 JSON 文件: {output_file}")


def save_results_text(results: Dict, output_file: str):
    """保存结果为文本格式"""
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("Obtainer 目录中 state 参数提取结果\n")
        f.write("=" * 80 + "\n\n")
        
        # 按参数名排序
        sorted_params = sorted(results.items())
        
        f.write(f"总共找到 {len(sorted_params)} 个不同的参数\n\n")
        
        for param_name, info in sorted_params:
            f.write(f"\n参数名: {param_name}\n")
            f.write("-" * 80 + "\n")
            f.write(f"出现文件数: {len(info['files'])}\n")
            f.write(f"出现总次数: {len(info['lines'])}\n")
            f.write(f"\n文件列表:\n")
            for file_path in info['files']:
                f.write(f"  - {file_path}\n")
            f.write(f"\n详细位置:\n")
            for line_ref in info['lines']:
                f.write(f"  - {line_ref}\n")
    
    print(f"结果已保存到文本文件: {output_file}")


def main():
    # 获取 Obtainer 目录路径
    script_dir = Path(__file__).parent.absolute()
    obtainer_dir = str(script_dir)
    
    print(f"开始扫描目录: {obtainer_dir}")
    print("=" * 80)
    
    # 提取参数
    results = extract_state_params_from_directory(obtainer_dir)
    
    # 统计信息
    total_params = len(results)
    total_occurrences = sum(len(info['lines']) for info in results.values())
    
    print("\n" + "=" * 80)
    print("提取完成!")
    print(f"总共找到 {total_params} 个不同的参数")
    print(f"总共出现 {total_occurrences} 次")
    print("=" * 80)
    
    # 保存结果
    json_output = os.path.join(obtainer_dir, 'state_params.json')
    txt_output = os.path.join(obtainer_dir, 'state_params.txt')
    
    save_results_json(results, json_output)
    save_results_text(results, txt_output)
    
    # 打印简要摘要
    print("\n参数列表 (前20个):")
    print("-" * 80)
    for i, (param_name, info) in enumerate(sorted(results.items())[:20], 1):
        print(f"{i:3d}. {param_name:40s} - {len(info['files'])} 个文件, {len(info['lines'])} 次出现")
    
    if len(results) > 20:
        print(f"... 还有 {len(results) - 20} 个参数")


if __name__ == '__main__':
    main()

