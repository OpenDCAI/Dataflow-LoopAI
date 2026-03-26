# loopai/agents/Judger/test_qa.py
import json
from pathlib import Path
from loopai.agents.Judger.judger_agent import JudgerAgent
from loopai.schema.states import LoopAIState


def build_test_state():
    output_dir = Path("./judger_output_qa")
    output_dir.mkdir(parents=True, exist_ok=True)

    # QA 测试数据：每条必须有 id, question, reference
    test_data = [
        {"id": 1, "question": "What is the capital of France?", "reference": "Paris"},
        {"id": 2, "question": "How many days are there in a week?", "reference": "Seven"},
        {"id": 3, "question": "What is 2 + 3?", "reference": "5"},
        {"id": 4, "question": "Why is the sky blue?", "reference": "Because shorter wavelengths of sunlight are scattered more by molecules in the atmosphere."},
        {"id": 5, "question": "Who wrote 'Romeo and Juliet'?", "reference": "William Shakespeare"},
        {"id": 6, "question": "What is the largest planet in our solar system?", "reference": "Jupiter"},
        {"id": 7, "question": "What is the boiling point of water at sea level?", "reference": "100 degrees Celsius"},
        {"id": 8, "question": "What language is spoken in Brazil?", "reference": "Portuguese"},
    ]

    input_file = output_dir / "qa_test.jsonl"
    with open(input_file, "w", encoding="utf-8") as f:
        for item in test_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    input_path = str(input_file.resolve())
    output_result_path = str((output_dir / "judger_qa_output.jsonl").resolve())

    # ✅ 关键：QA 任务的 key_mapping
    key_mapping = {
        "input_question_key": "question",      # 问题字段
        "reference_answer_key": "reference"   # 标准答案字段
    }

    state = LoopAIState()
    state["task_id"] = "test_qa_001"
    state["output_dir"] = str(output_dir)
    state["current"] = "judger"

    # === judger 配置 ===
    state["judger"] = {
        "eval_task_type": "general_text",  # 仍走 general_text_node（支持多种子类型）

        "is_api": False,
        "eval_model_path": "/jizhicfs/hymiezhao/models/Qwen3-8B",
        "eval_temperature": 0.0,
        "eval_top_p": 0.95,

        "eval_problem_path": input_path,       # 输入：含 question + reference
        "eval_result_path": input_path,        # 同上（Analyzer 会读它）
        "out_result_path": output_result_path,

        "eval_batch_size": 1,
        "eval_case_num": len(test_data),
        "eval_max_concurrency": 1,
        "eval_api_key": "",
        "key_mapping": key_mapping,
    }

    # === analyzer 配置：指定 QA 评测类型 ===
    state["analyzer"] = {
        "analyze_task_type": "key2_qa",  # 启用答案正确性评测

        "is_api": False,
        "analyze_model_path": "/jizhicfs/hymiezhao/models/Qwen3-8B",
        "analyze_temperature": 0.0,
        "analyze_top_p": 0.95,
        "analyze_max_tokens": 128,

        "eval_result_path": input_path,
        "output_dir": str(output_dir),
        "key_mapping": key_mapping,

        # 控制参数（可选）
        "analyze_batch_size": 1,
        "analyze_max_concurrency": 1,
    }

    return state


def main():
    state = build_test_state()

    agent = JudgerAgent()

    # 临时绕过 check_required_fields 的上下文问题（保持不变）
    original_func = agent.get_check_required_fields_node
    def safe_get_check_required_fields_node(self):
        def check_required_fields(state, runtime):
            from loopai.schema.states import get_missing_fields
            from loopai.logger import get_logger
            logger = get_logger()
            _isNotNone = lambda v: v != "" and v is not None

            required_fields = {
                'judger': ["eval_api_key", "eval_temperature", "eval_top_p",
                          "eval_problem_path", "eval_batch_size", "eval_case_num", "eval_task_type"],
                'default': ["output_dir", "task_id"]
            }
            missing_fields = get_missing_fields(required_fields, state)

            if not missing_fields:
                if not _isNotNone(state.get("judger", {}).get("eval_model_path", "")):
                    if _isNotNone(state.get("trainer", {}).get("train_input_model_name", "")):
                        state["judger"]["eval_model_path"] = state["trainer"]["train_input_model_name"]
                    else:
                        missing_fields = get_missing_fields({'judger': ["eval_model_path"]}, state)

            base_url = state.get("judger", {}).get("eval_base_url")
            if _isNotNone(base_url):
                from .utils.oj.vllm_check import check_vllm_running
                if not check_vllm_running(base_url):
                    missing_fields.setdefault("judger", []).append("eval_base_url")

            from .utils.oj.data import check_file
            check_result = check_file(state)
            for k, v in check_result.items():
                if not bool(v):
                    missing_fields.setdefault("judger", []).append(k)

            if not missing_fields and state["judger"].get("eval_task_type") == "text2sql":
                missing_fields = get_missing_fields({'judger': ["eval_text2sql_dir"]}, state)

            if missing_fields:
                raise ValueError(f"Missing required fields: {missing_fields}")

            state["judger"]["output_result_path"] = ""
            state["judger"]["output_case_path"] = ""
            state["judger"]["output_problem_path"] = ""
            return state

        return check_required_fields

    agent.get_check_required_fields_node = lambda: safe_get_check_required_fields_node(agent)

    try:
        graph = agent()
        result = graph.invoke(state)
        print("✅ Success! Output files:")
        print(" - Result:", result["judger"].get("output_result_path"))
        print(" - Problem:", result["judger"].get("output_problem_path"))
    except Exception as e:
        print("❌ Error:", e)
        raise


if __name__ == "__main__":
    main()