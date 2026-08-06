from typing import Literal
from langchain_core.tools import tool
from pydantic import BaseModel, Field


# 定义 motivation 的枚举类型
MotivationType = Literal[
    "chat", "train", "judge", "analyze", "obtain", "config", "finish",
]


class CheckMotivationInput(BaseModel):
    """check_motivation 工具的输入 schema"""
    motivation: MotivationType = Field(
        description=(
            "用户意图类型，必须是以下枚举值之一：\n"
            "- chat: 闲聊、聊天、普通对话\n"
            "- train: 训练模型、开始训练、继续训练\n"
            "- judge: 评测模型、给答案打分、评分\n"
            "- analyze: 分析模型表现、查看输出、可视化\n"
            "- obtain: 搜索/抓取/下载数据、入湖、清洗、映射、构造并导出训练数据集\n"
            "- config: 设置参数、修改配置、调参\n"
            "- finish: 结束对话、停止流程、退出"
        )
    )


@tool(args_schema=CheckMotivationInput)
def check_motivation(motivation: MotivationType) -> dict:
    """
    根据用户意图确定下一个工作流节点。
    
    【重要】motivation 参数必须是以下英文枚举值之一，不接受中文：
    - "chat": 闲聊对话
    - "train": 训练模型
    - "judge": 评测模型
    - "analyze": 分析结果
    - "obtain": 搜索/抓取/下载数据、入湖、清洗、映射、构造并导出训练数据集
    - "config": 配置参数
    - "finish": 结束对话

    Returns:
        dict: {"motivation": "<motivation>", "next_to": "<target_node>"}
    """
    
    mapping = {
        "train": "train_node",
        "judge": "judge_node",
        "analyze": "analyze_node",
        "obtain": "obtain_node",
        "config": "config_node",
        "finish": "end_node",
        "chat": "query_node",
    }

    next_to = mapping.get(motivation, "query_node")

    return {
        "motivation": motivation,
        "next_to": next_to
    }
