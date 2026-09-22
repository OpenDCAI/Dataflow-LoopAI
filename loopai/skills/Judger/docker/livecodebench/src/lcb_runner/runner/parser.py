import os
import argparse

try:
    import torch
except ImportError:  # torch is only required for local vLLM inference
    torch = None

from lcb_runner.utils.scenarios import Scenario


def _optional_bool(value):
    """None / "true" / "false" -> bool，用来把「没传这个开关」和「显式关掉」分开。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected a boolean, got {value!r}")


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("LCB_MODEL", "gpt-3.5-turbo-0301"),
        help="Name of the model to use matching `lm_styles.py`, or the served model name when `--vllm_base_url` is given",
    )
    parser.add_argument(
        "--local_model_path",
        type=str,
        default=None,
        help="If you have a local model, specify it here in conjunction with --model",
    )
    parser.add_argument(
        "--vllm_base_url",
        type=str,
        default=os.getenv("VLLM_BASE_URL"),
        help="Base URL of an OpenAI-compatible vLLM server, e.g. http://localhost:8000/v1. "
        "When set, generations are requested from the server instead of loading the model locally.",
    )
    parser.add_argument(
        "--vllm_api_key",
        type=str,
        default=os.getenv("VLLM_API_KEY"),
        help="API key for the vLLM server. Defaults to the VLLM_API_KEY env var, otherwise 'EMPTY'.",
    )
    parser.add_argument(
        "--model_style",
        type=str,
        default=os.getenv("LCB_MODEL_STYLE", "OpenAIChat"),
        help="LMStyle name used for models that are not listed in `lm_styles.py` "
        "(only used together with `--vllm_base_url`). Defaults to OpenAIChat, which lets the "
        "vLLM server apply the served model's own chat template.",
    )
    parser.add_argument(
        "--trust_remote_code",
        action="store_true",
        help="trust_remote_code option used in huggingface models",
    )
    parser.add_argument(
        "--scenario",
        type=Scenario,
        default=Scenario.codegeneration,
        help="Type of scenario to run",
    )
    parser.add_argument(
        "--not_fast",
        action="store_true",
        help="whether to use full set of tests (slower and more memory intensive evaluation)",
    )
    parser.add_argument(
        "--release_version",
        type=str,
        default="release_latest",
        help="whether to use full set of tests (slower and more memory intensive evaluation)",
    )
    parser.add_argument(
        "--local_dataset_path",
        type=str,
        default=os.getenv("LCB_LOCAL_DATASET_PATH"),
        help="Path to a locally saved dataset: a `save_to_disk` directory, or a .json/.jsonl/.parquet "
        "file. Skips downloading the dataset from the HuggingFace Hub.",
    )
    parser.add_argument(
        "--cot_code_execution",
        action="store_true",
        help="whether to use CoT in code execution scenario",
    )
    parser.add_argument(
        "--n", type=int, default=10, help="Number of samples to generate"
    )
    parser.add_argument(
        "--codegen_n",
        type=int,
        default=10,
        help="Number of samples for which code generation was run (used to map the code generation file during self-repair)",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="Temperature for sampling"
    )
    parser.add_argument("--top_p", type=float, default=0.95, help="Top p for sampling")
    parser.add_argument(
        "--max_tokens", type=int, default=2000, help="Max tokens for sampling"
    )
    parser.add_argument(
        "--multiprocess",
        default=0,
        type=int,
        help="Number of processes to use for generation (vllm runs do not use this)",
    )
    parser.add_argument(
        "--stop",
        default="###",
        type=str,
        help="Stop token (use `,` to separate multiple tokens)",
    )
    parser.add_argument("--continue_existing", action="store_true")
    parser.add_argument("--continue_existing_with_eval", action="store_true")
    parser.add_argument(
        "--use_cache", action="store_true", help="Use cache for generation"
    )
    parser.add_argument(
        "--cache_batch_size", type=int, default=100, help="Batch size for caching"
    )
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate the results")
    parser.add_argument(
        "--num_process_evaluate",
        type=int,
        default=12,
        help="Number of processes to use for evaluation",
    )
    parser.add_argument("--timeout", type=int, default=6, help="Timeout for evaluation")
    parser.add_argument(
        "--openai_timeout", type=int, default=90, help="Timeout for requests to OpenAI"
    )
    parser.add_argument(
        "--tensor_parallel_size",
        type=int,
        default=-1,
        help="Tensor parallel size for vllm",
    )
    parser.add_argument(
        "--enable_prefix_caching",
        action="store_true",
        help="Enable prefix caching for vllm",
    )
    parser.add_argument(
        "--custom_output_file",
        type=str,
        default=None,
        help="Path to the custom output file used in `custom_evaluator.py`",
    )
    parser.add_argument(
        "--custom_output_save_name",
        type=str,
        default=None,
        help="Folder name to save the custom output results (output file folder modified if None)",
    )
    parser.add_argument("--dtype", type=str, default="bfloat16", help="Dtype for vllm")
    # Added to avoid running extra generations (it's slow for reasoning models)
    parser.add_argument(
        "--enable_thinking",
        type=_optional_bool,
        default=os.getenv("LCB_ENABLE_THINKING"),
        help="Send chat_template_kwargs={'enable_thinking': ...} to the OpenAI-compatible "
        "server. Thinking models (Qwen3, ...) default to reasoning on, which burns the whole "
        "--max_tokens budget on the reasoning trace before any code is written; pass false to "
        "turn it off. Unset = do not send the field at all.",
    )
    parser.add_argument(
        "--start_date",
        type=str,
        default=None,
        help="Start date for the contest to filter the evaluation file (format - YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end_date",
        type=str,
        default=None,
        help="End date for the contest to filter the evaluation file (format - YYYY-MM-DD)",
    )

    args = parser.parse_args()

    args.stop = args.stop.split(",")

    if args.tensor_parallel_size == -1:
        args.tensor_parallel_size = torch.cuda.device_count() if torch is not None else 0

    if args.multiprocess == -1:
        args.multiprocess = os.cpu_count()

    return args


def test():
    args = get_args()
    print(args)


if __name__ == "__main__":
    test()
