# loopai/agents/Judger/test_text_score.py
import json
from pathlib import Path
from loopai.agents.Judger.judger_agent import JudgerAgent
from loopai.schema.states import LoopAIState


def build_test_state():
    output_dir = Path("./judger_output3")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 测试数据：每条必须有 id 和 text
    test_data = [
        {
            "id": 1,
            "text": "Paris is the capital of France. It is known for landmarks such as the Eiffel Tower and the Louvre Museum."
        },
        {
            "id": 2,
            "text": "A week has seven days: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, and Sunday."
        },
        {
            "id": 3,
            "text": "Two plus three equals five."
        },
        {
            "id": 4,
            "text": "On a clear day, the sky usually appears blue because shorter wavelengths of sunlight are scattered more strongly in the atmosphere."
        },
        {
            "id": 5,
            "text": "Paris Paris is capital of France and it have many famous place, people like go there because it is very beautiful city."
        },
        {
            "id": 6,
            "text": "The sky is blue. Blue sky is blue because the blue sky looks blue in blue daylight and blue air."
        },
        {
            "id": 7,
            "text": "There are seven days in a week, but sometimes people feel like there are too many tasks and too little rest."
        },
        {
            "id": 8,
            "text": "France capital maybe Paris, and week maybe has seven or something, the answer is not stable and the sentence is confusing."
        }
    ]

    input_file = output_dir / "test_general_text_eval.jsonl"
    with open(input_file, "w", encoding="utf-8") as f:
        for item in test_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    input_path = str(input_file.resolve())
    output_result_path = str((output_dir / "judger_output.jsonl").resolve())

    key_mapping = {
        "input_text_key": "text"  # 必须与 BenchAdapter 期望一致
    }

    state = LoopAIState()
    state["task_id"] = "test_text_score_001"
    state["output_dir"] = str(output_dir)
    state["current"] = "judger"  # 用于日志

    # === judger 配置：满足 check_required_fields 要求 ===
    state["judger"] = {
        # 任务类型：触发 eval_general_text_node
        "eval_task_type": "general_text",

        # 模型配置
        "is_api": False,
        "eval_model_path": "/jizhicfs/hymiezhao/models/Qwen3-8B",
        "eval_temperature": 0.0,
        "eval_top_p": 0.95,

        # 路径
        "eval_problem_path": input_path,
        "eval_result_path": input_path,      # 模拟 Analyzer 输出
        "out_result_path": output_result_path,

        # 控制参数
        "eval_batch_size": 1,
        "eval_case_num": len(test_data),
        "eval_max_concurrency": 1,

        # 其他
        "eval_api_key": "",                  # 占位，避免缺失
        "key_mapping": key_mapping,
    }
    state["analyzer"] = {
        "analyze_task_type": "key1_text_score",

        # ✅ 关键：改成本地模型
        "is_api": False,
        "analyze_model_path": "/jizhicfs/hymiezhao/models/Qwen3-8B",

        "analyze_temperature": 0.0,
        "analyze_top_p": 0.95,
        "analyze_max_tokens": 32,
    
        "output_brief": False,
        "output_suggestion": False,
    
        "analyze_batch_size": 1,
        "analyze_max_concurrency": 1,
        "analyze_chunk_size": 1,
    
        "quick_brief": False,
        "quick_brief_limit": 20,

        "eval_result_path": input_path,

        "key_mapping": key_mapping,
    }

    return state


def main():
    state = build_test_state()

    # 创建 agent 并构建图
    agent = JudgerAgent()
    
    # 临时绕过 @BaseAgent.set_current 装饰器问题（如果存在）
    # 方法：直接替换 get_check_required_fields_node 为无装饰版本
    original_func = agent.get_check_required_fields_node
    def safe_get_check_required_fields_node(self):
        def check_required_fields(state, runtime):
            # 复制原逻辑，但移除对 runtime.context 的依赖（顶层调用时）
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

    # 替换方法（仅用于测试）
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