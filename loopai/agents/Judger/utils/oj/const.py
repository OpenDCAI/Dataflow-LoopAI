field_mapping = {
    "question": [
        "question", "prompt", "query", "input", "problem", "instruction",
        "题目", "问题", "输入", "提示"
    ],
    "target": [
        "target", "answer", "reference", "gold", "gold_answer", "gt","chosen"
        "label", "expected", "标准答案", "答案", "参考答案", "标签"
    ],
    "targets": [
        "targets", "answers", "references", "gold_answers", "候选答案", "参考答案列表"
    ],
    "prediction": [
        "generated_ans", "prediction", "pred", "response", "output",
        "model_output", "generated", "预测", "模型输出", "生成答案", "回答"
    ],
    "choices": [
        "choices", "options", "candidates", "选项", "候选项"
    ],
    "label": [
        "label", "answer", "target", "correct_option", "正确选项", "标签"
    ],
    "labels": [
        "labels", "answers", "targets", "正确选项列表", "标签列表"
    ],
    "better": [
        "better", "preferred", "winner", "更优答案", "偏好", "更好"
    ],
    "answer": [
        "chosen", "selected", "preferred", "positive", "pos", 
        "human", "good", "helpful", "harmless", "correct", "accepted", 
        "response_chosen", "output_good", "优", "选中", "正样本"
    ],
    "rejected": [
        "rejected", "unselected", "unpreferred", "loser", "negative", "neg", 
        "machine", "bad", "harmful", "helpless", "incorrect", "ignored", 
        "response_rejected", "output_bad", "差", "拒绝", "负样本"
    ],
    "text": [
        "text", "content", "essay", "article", "response", "output",
        "文本", "内容", "文章", "回答"
    ]
}

def build_inverted_index(source_dict):
    """
    构建倒排索引
    返回格式: { "候选词": "标准分类名" }
    """
    inverted_index = {}
    
    # 遍历每一个标准分类（如 "question", "target"）及其候选词列表
    for standard_key, candidates in source_dict.items():
        for candidate in candidates:
            # 如果候选词已经存在，可以选择保留第一个遇到的（优先级高）或者覆盖
            # 这里我们保留第一个遇到的，因为通常列表前面的词更常用
            if candidate not in inverted_index:
                inverted_index[candidate] = standard_key
                
    return inverted_index