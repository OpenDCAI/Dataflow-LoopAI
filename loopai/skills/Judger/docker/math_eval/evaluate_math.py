import argparse
import json
import os
import time
from pathlib import Path
from datasets import load_dataset

# Use math_verify package directly
from math_verify import parse, verify


def extract_boxed_answer(text: str) -> str:
    """
    Extract answer from \\boxed{} command in the text.
    Returns the last boxed answer found.
    """
    # Find all \boxed{...} patterns
    idx = text.rfind("\\boxed")
    if idx < 0:
        return None

    # Find the matching closing brace
    i = idx
    num_left_braces = 0
    right_brace_idx = None

    while i < len(text):
        if text[i] == "{":
            num_left_braces += 1
        if text[i] == "}":
            num_left_braces -= 1
            if num_left_braces == 0:
                right_brace_idx = i
                break
        i += 1

    if right_brace_idx is None:
        return None

    # Extract content inside \boxed{...}
    boxed_str = text[idx : right_brace_idx + 1]

    # Remove the \boxed{ and } wrapper
    if boxed_str.startswith("\\boxed{") and boxed_str.endswith("}"):
        answer = boxed_str[7:-1]  # Remove "\boxed{" and "}"
        return answer.strip()

    return None


def grade_answer(predicted: str, ground_truth: str) -> bool:
    """
    Grade the predicted answer against ground truth using math_verify.

    Args:
        predicted: The predicted answer (already extracted from \\boxed{})
        ground_truth: The ground truth answer

    Returns:
        True if answers match, False otherwise
    """
    if predicted is None or ground_truth is None:
        return False

    predicted = str(predicted)
    ground_truth = str(ground_truth)

    try:
        # Ensure answers are wrapped in $ for latex parsing
        if not "$" in predicted:
            predicted = f"${predicted}$"
        if not "$" in ground_truth:
            ground_truth = f"${ground_truth}$"

        # Parse both answers
        pred_parsed = parse(predicted, fallback_mode="no_fallback")
        gt_parsed = parse(ground_truth, fallback_mode="no_fallback")

        # Verify equivalence
        return verify(gt_parsed, pred_parsed, timeout_seconds=5)
    except Exception as e:
        # If math_verify fails, try simple string comparison
        # Normalize by removing spaces, $, and converting to lowercase
        pred_norm = predicted.replace("$", "").replace(" ", "").lower().strip()
        gt_norm = ground_truth.replace("$", "").replace(" ", "").lower().strip()
        return pred_norm == gt_norm


def load_vllm_model(
    base_model_path: str,
    lora_adapter_path: str = None,
    gpu_memory_utilization: float = 0.9,
    tensor_parallel_size: int = 1,
    max_model_len: int = None,
    max_num_seqs: int = None,
    enable_thinking: bool = True,
):
    """
    Load a model using vLLM for fast inference.

    Args:
        base_model_path: Path to the base model
        lora_adapter_path: Path to the LoRA adapters (checkpoint directory). If None, uses base model only.
        gpu_memory_utilization: GPU memory utilization (0.0 to 1.0)
        tensor_parallel_size: Number of GPUs to use for tensor parallelism
        max_model_len: Maximum model context length
        max_num_seqs: Optional cap on concurrently scheduled sequences. This
            can improve long-context LoRA evaluation stability without
            changing sampling parameters.
        enable_thinking: Whether to enable thinking mode for Qwen3

    Returns:
        Tuple of (vLLM LLM instance, tokenizer)
    """
    print(f"Loading model with vLLM from: {base_model_path}")

    # Set max_model_len based on thinking mode if not specified
    if max_model_len is None:
        # For thinking mode, use larger context as recommended by Qwen3
        max_model_len = 40960 if enable_thinking else 32768
        print(
            f"Auto-setting max_model_len to {max_model_len} for {'thinking' if enable_thinking else 'non-thinking'} mode"
        )

    # Build LLM configuration
    llm_config = {
        "model": base_model_path,
        "gpu_memory_utilization": gpu_memory_utilization,
        "tensor_parallel_size": tensor_parallel_size,
        "trust_remote_code": True,
        "max_model_len": max_model_len,
        "distributed_executor_backend": "mp",
        "enforce_eager": True,
    }
    if max_num_seqs is not None:
        llm_config["max_num_seqs"] = max_num_seqs

    if lora_adapter_path is not None:
        print(f"LoRA adapter path provided: {lora_adapter_path}")

        # Check if LoRA weights exist
        adapter_path = Path(lora_adapter_path) / "adapter_model.safetensors"
        if not adapter_path.exists():
            # Try alternative name
            adapter_path = Path(lora_adapter_path) / "adapter_model.bin"

        if adapter_path.exists():
            print("LoRA weights found. Enabling LoRA support...")
            llm_config["enable_lora"] = True
            # Use the tokenizer saved with the adapter: SFT may align EOS to
            # Qwen's <|im_end|> token while the base tokenizer uses endoftext.
            if (Path(lora_adapter_path) / "tokenizer.json").exists():
                llm_config["tokenizer"] = str(lora_adapter_path)
            llm_config["max_lora_rank"] = 64  # Adjust based on your LoRA rank
            llm_config["max_loras"] = 1
            llm_config["max_cpu_loras"] = 1
        else:
            print(f"Warning: No LoRA weights found at {lora_adapter_path}")
            print("Continuing with base model only...")
            lora_adapter_path = None

    from vllm import LLM
    from transformers import AutoTokenizer

    llm = LLM(**llm_config)

    # Load tokenizer for chat template
    tokenizer_path = lora_adapter_path if lora_adapter_path and (Path(lora_adapter_path) / "tokenizer.json").exists() else base_model_path
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)

    # Print dtype information
    print("\n" + "=" * 70)
    print("MODEL DTYPE INFORMATION")
    print("=" * 70)
    print(f"vLLM Model Config dtype: {llm.llm_engine.model_config.dtype}")
    print(f"vLLM Model quantization: {llm.llm_engine.model_config.quantization}")
    cache_config = getattr(llm.llm_engine, "cache_config", None)
    cache_dtype = getattr(cache_config, "cache_dtype", "unknown")
    print(f"KV cache dtype: {cache_dtype}")
    print("=" * 70 + "\n")

    print("vLLM model loaded successfully!")
    return llm, tokenizer


class _RemoteSamplingParams:
    """Small SamplingParams-compatible object used by the API adapter."""

    def __init__(self, temperature, top_p, top_k, min_p, max_tokens, presence_penalty, n):
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.min_p = min_p
        self.max_tokens = max_tokens
        self.presence_penalty = presence_penalty
        self.n = n
        self.stop_token_ids = [151643, 151645]


class _RemoteTokenizer:
    """Preserve chat messages so the remote vLLM server applies its template."""

    def apply_chat_template(self, messages, **kwargs):
        return messages


class _RemoteCompletion:
    def __init__(self, text, finish_reason=None):
        self.text = text or ""
        self.token_ids = []
        self.finish_reason = finish_reason
        self.stop_reason = None


class _RemoteRequestOutput:
    def __init__(self, completions):
        self.outputs = completions


class _RemoteVLLM:
    """Adapter exposing the subset of vLLM's generate interface used below."""

    def __init__(self, base_url, model, api_key=None, timeout=600, max_retries=3, enable_thinking=True):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Remote API mode requires the 'openai' package") from exc

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.enable_thinking = enable_thinking
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=api_key or "EMPTY",
            timeout=timeout,
            max_retries=0,
        )

    def generate(self, prompts, sampling_params, lora_request=None, use_tqdm=True):
        outputs = []
        for index, messages in enumerate(prompts, start=1):
            extra_body = {
                "top_k": sampling_params.top_k,
                "min_p": sampling_params.min_p,
                "stop_token_ids": sampling_params.stop_token_ids,
                "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
            }
            last_error = None
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=sampling_params.temperature,
                        top_p=sampling_params.top_p,
                        max_tokens=sampling_params.max_tokens,
                        presence_penalty=sampling_params.presence_penalty,
                        n=sampling_params.n,
                        extra_body=extra_body,
                    )
                    completions = []
                    for choice in response.choices:
                        message = choice.message
                        content = message.content or ""
                        reasoning = getattr(message, "reasoning_content", None)
                        if reasoning and reasoning not in content:
                            content = f"<think>\n{reasoning}\n</think>\n\n{content}"
                        completions.append(_RemoteCompletion(content, choice.finish_reason))
                    outputs.append(_RemoteRequestOutput(completions))
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** attempt, 10))
            else:
                raise RuntimeError(
                    f"vLLM API request failed for problem {index} after {self.max_retries + 1} attempts: {last_error}"
                ) from last_error
            if use_tqdm:
                print(f"API progress: {index}/{len(prompts)}")
        return outputs


def _load_local_aime(path_str: str, label: str):
    """Load a local math file and normalize common question/answer aliases.

    The evaluator deliberately never resolves dataset names through HuggingFace;
    ``path_str`` must point to a local JSON/JSONL/Parquet file.
    """
    path = Path(path_str)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        dataset = load_dataset("parquet", data_files=str(path), split="train")
    elif suffix in {".json", ".jsonl"}:
        dataset = load_dataset("json", data_files=str(path), split="train")
    else:
        raise ValueError(f"Unsupported local {label} file type: {suffix}")

    columns = {column.lower(): column for column in dataset.column_names}
    rename_map = {}
    key_mapping = {}
    for target, aliases in {
        "id": ("id", "problem_idx", "index"),
        "problem": ("problem", "question", "prompt", "query", "input"),
        "answer": ("answer", "target", "final_answer", "solution"),
    }.items():
        source = next((columns[name] for name in aliases if name in columns), None)
        if source:
            key_mapping[target] = source
            if source != target:
                rename_map[source] = target
    if rename_map:
        dataset = dataset.rename_columns(rename_map)
    missing = {"problem", "answer"} - set(dataset.column_names)
    if missing:
        available = ", ".join(dataset.column_names)
        raise ValueError(
            f"Local {label} dataset is missing columns: {sorted(missing)}; "
            f"available columns: {available}"
        )
    print(f"Loaded local {label} dataset from {path} with {len(dataset)} problems")
    print(f"Auto-mapped dataset keys: {key_mapping}")
    return dataset, key_mapping


def _dump_json_atomic(path: Path, payload: dict) -> None:
    """先写 .tmp 再 os.replace —— 读取方任何时刻拿到的都是完整的合法 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _write_result(
    output_file,
    *,
    config: dict,
    results: list,
    num_problems: int,
    pass_at_n: int,
    total_correct_per_problem: int,
    majority_vote_correct_count: int,
    formatted_count: int,
    truncated_count: int,
    total: int,
) -> None:
    """把当前进度原子写盘。

    每道题跑完写一次。评测动辄几小时，以前是全部跑完后才写一次 —— 中途任何
    失败（某道题的请求超时、被 Judger 杀掉、容器 OOM）都会让已经算出来的结果
    全部丢失。部分结果里 ``completed_problems`` 记录实际跑完了几题。
    """
    if not output_file:
        return
    payload = {
        **config,
        "num_problems": num_problems,
        "completed_problems": len(results),
        "total_solutions": total,
        "pass_at_n": pass_at_n,
        "pass_at_n_pct": pass_at_n / num_problems * 100 if num_problems else 0.0,
        "average_at_n": total_correct_per_problem,
        "average_at_n_pct": total_correct_per_problem / total * 100 if total else 0.0,
        "majority_vote_at_n": majority_vote_correct_count,
        "majority_vote_at_n_pct": majority_vote_correct_count / num_problems * 100 if num_problems else 0.0,
        "formatted_count": formatted_count,
        "format_rate": formatted_count / total * 100 if total else 0.0,
        "truncated_count": truncated_count,
        "truncation_rate": truncated_count / total * 100 if total else 0.0,
        "results": results,
    }
    _dump_json_atomic(Path(output_file), payload)


def evaluate_math500(
    llm,
    tokenizer,
    max_new_tokens: int,
    max_model_len: int | None = None,
    temperature: float = 0.6,
    top_p: float = 0.95,
    top_k: int = 20,
    min_p: float = 0.0,
    presence_penalty: float = 0.0,
    num_samples: int = None,
    output_file: str = None,
    lora_request=None,
    dataset_name: str = "auto",
    base_model_name: str = None,
    checkpoint_dir: str = None,
    enable_thinking: bool = True,
    val_n: int = 1,
    dataset_path: str = None,
):
    """
    Evaluate model on MATH500 or other datasets using Qwen3 thinking mode with best practices.

    Args:
        llm: The vLLM LLM instance
        tokenizer: The tokenizer for chat template
        max_new_tokens: Maximum tokens to generate
        temperature: Sampling temperature (0.6 for thinking, 0.7 for non-thinking)
        top_p: Top-p sampling parameter (0.95 for thinking, 0.8 for non-thinking)
        top_k: Top-k sampling parameter (20 recommended)
        min_p: Minimum probability threshold (0 recommended)
        presence_penalty: Presence penalty to reduce repetitions (0-2)
        num_samples: Number of samples to evaluate (None = all)
        output_file: Path to save detailed results
        lora_request: Optional LoRA request for inference
        dataset_name: Name of dataset to use
        base_model_name: Base model name for logging
        enable_thinking: Whether to use thinking mode
    """
    print(f"\n{'='*70}")
    print(f"EVALUATION CONFIGURATION")
    print(f"{'='*70}")
    dataset_name = (dataset_name or "auto").lower()
    if not dataset_path or not Path(dataset_path).is_file():
        raise FileNotFoundError("--dataset_path is required and must point to a local JSON/JSONL/Parquet file")
    print(f"Dataset: {dataset_name.upper()}")
    print(f"Thinking Mode: {'ENABLED' if enable_thinking else 'DISABLED'}")
    print(f"Temperature: {temperature} (Qwen3 {'thinking' if enable_thinking else 'non-thinking'} mode)")
    print(f"Top-P: {top_p}")
    print(f"Top-K: {top_k}")
    print(f"Min-P: {min_p}")
    print(f"Presence Penalty: {presence_penalty}")
    print(f"Max New Tokens: {max_new_tokens}")
    print(f"Max Model Length: {max_model_len}")
    print(f"Val-N (solutions per problem): {val_n}")
    print(f"{'='*70}\n")

    print(f"Loading local dataset ({dataset_name})...")
    dataset, key_mapping = _load_local_aime(str(dataset_path), "math")
    print(f"Loaded local dataset from {dataset_path} with {len(dataset)} problems")

    # Limit to num_samples if specified
    if num_samples:
        dataset = dataset.select(range(min(num_samples, len(dataset))))

    # # Check if output file already exists with required samples
    # if output_file and Path(output_file).exists():
    #     print(f"\nFound existing results file: {output_file}")
    #     try:
    #         with open(output_file, 'r', encoding='utf-8') as f:
    #             existing_data = json.load(f)

    #         existing_results = existing_data.get('results', [])
    #         existing_count = len(existing_results)
    #         expected_count = len(dataset)

    #         if existing_count >= expected_count:
    #             print(f"✓ Existing results already have {existing_count} samples (expected: {expected_count})")
    #             print(f"Skipping generation and returning existing results.")
    #             print(f"Accuracy: {existing_data.get('accuracy', 0):.2f}%")
    #             print("=" * 70 + "\n")
    #             return existing_data.get('accuracy', 0), existing_results
    #         else:
    #             print(f"✗ Existing results have {existing_count} samples, but need {expected_count}")
    #             print(f"Proceeding with generation...")
    #     except Exception as e:
    #         print(f"Warning: Could not validate existing results: {e}")
    #         print(f"Proceeding with generation...")

    mode_label = "remote vLLM API" if isinstance(llm, _RemoteVLLM) else "local vLLM"
    print(f"Evaluating on {len(dataset)} problems with {mode_label}...")

    # Use native vLLM params locally and a compatible object for API mode.
    if isinstance(llm, _RemoteVLLM):
        sampling_params = _RemoteSamplingParams(
            temperature, top_p, top_k, min_p, max_new_tokens, presence_penalty, val_n
        )
    else:
        from vllm import SamplingParams

        sampling_params = SamplingParams(
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            max_tokens=max_new_tokens,
            presence_penalty=presence_penalty,
            n=val_n,
            stop_token_ids=[151643, 151645],
        )

    total = 0
    formatted_count = 0
    truncated_count = 0
    results = []

    # Metrics for val_n > 1
    pass_at_n = 0  # At least one correct
    total_correct_per_problem = 0  # Sum of correct solutions across all problems
    majority_vote_correct_count = 0

    # 静态配置项，每次落盘都带上（见 _write_result）
    summary_config = {
        "base_model": base_model_name,
        "dataset": dataset_name,
        "enable_thinking": enable_thinking,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "min_p": min_p,
        "presence_penalty": presence_penalty,
        "max_new_tokens": max_new_tokens,
        "max_model_len": max_model_len,
        "checkpoint_dir": checkpoint_dir,
        "key_mapping": key_mapping,
        "val_n": val_n,
    }

    def dump_progress() -> None:
        """把当前进度写盘（每道题跑完调一次）。"""
        _write_result(
            output_file,
            config=summary_config,
            results=results,
            num_problems=len(dataset),
            pass_at_n=pass_at_n,
            total_correct_per_problem=total_correct_per_problem,
            majority_vote_correct_count=majority_vote_correct_count,
            formatted_count=formatted_count,
            truncated_count=truncated_count,
            total=total,
        )

    # Prepare all prompts/messages for batch inference
    all_prompts = []
    all_messages = []
    all_gt_answers = []
    all_problems = []
    all_question_ids = []

    for example in dataset:
        # _load_local_aime canonicalizes common aliases to problem/answer.
        # The answer content itself determines normalization: boxed solutions
        # use the boxed value, while GSM-style answers use the #### suffix.
        problem = example["problem"]
        raw_answer = str(example["answer"])
        gt_answer = raw_answer.split("####")[-1].strip() if "####" in raw_answer else raw_answer
        gt_answer = extract_boxed_answer(gt_answer) or gt_answer
        question_id = example.get("id", example.get("problem_idx", example.get("index", None)))

        # Format prompt following Qwen3 best practices for math problems
        user_message = (
            f"{problem}\n\nPlease reason step by step, and put your final answer within \\boxed{{}}."
        )

        messages = [{"role": "user", "content": user_message}]
        if os.environ.get("FORCE_EMPTY_THINK") == "1":
            messages.append({"role": "assistant", "content": "<think>\n</think>\n\n"})

        all_messages.append(messages)
        all_gt_answers.append(gt_answer)
        all_problems.append(problem)
        all_question_ids.append(question_id)

    # Run batch inference with vLLM using generate interface
    print(f"\nRunning vLLM batch inference on {len(all_messages)} problems...")
    print("Using remote chat-completions requests..." if isinstance(llm, _RemoteVLLM) else "Using generate interface with manual chat template...")

    # Apply chat template to all messages
    all_prompts = []
    for messages in all_messages:
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=enable_thinking
        )
        all_prompts.append(text)

    # Print dtype info before generation
    print("\n" + "=" * 70)
    print("GENERATION DTYPE CHECK")
    print("=" * 70)
    if hasattr(llm, "llm_engine"):
        print(f"Model dtype: {llm.llm_engine.model_config.dtype}")
        print(f"Quantization: {llm.llm_engine.model_config.quantization}")
        cache_config = getattr(llm.llm_engine, "cache_config", None)
        cache_dtype = getattr(cache_config, "cache_dtype", "unknown")
        print(f"KV cache dtype: {cache_dtype}")
    else:
        print(f"Remote vLLM endpoint: {llm.base_url}")
        print(f"Remote model: {llm.model}")
    print(f"Using LoRA: {lora_request is not None}")
    if lora_request is not None:
        if lora_request.lora_path is None:
            raise ValueError(
                "LoRA request created but lora_local_path is None; lora weights are empty, might be issue with using zero3 + peft; try using zero2"
            )
        print(f"LoRA path: {lora_request.lora_path}")
    print("=" * 70 + "\n")

    # Generate outputs
    if lora_request is not None:
        outputs = llm.generate(all_prompts, sampling_params, lora_request=lora_request, use_tqdm=True)
    else:
        outputs = llm.generate(all_prompts, sampling_params, use_tqdm=True)

    # Process results
    print("\nProcessing results...")
    for idx, (output, problem, gt_answer, question_id) in enumerate(
        zip(outputs, all_problems, all_gt_answers, all_question_ids)
    ):
        # Process all val_n generations for this problem
        generations = []
        predicted_answers = []
        is_correct_list = []
        is_formatted_list = []
        output_token_counts = []
        finish_reasons = []
        stop_reasons = []
        is_truncated_list = []

        for i in range(len(output.outputs)):
            completion_output = output.outputs[i]
            generated_text = completion_output.text
            output_token_count = len(completion_output.token_ids)
            finish_reason = completion_output.finish_reason
            stop_reason = completion_output.stop_reason
            is_truncated = finish_reason == "length" or output_token_count >= max_new_tokens

            # Extract answer from generated text
            predicted_answer = extract_boxed_answer(generated_text)

            # Check if answer was properly formatted
            is_formatted = predicted_answer is not None

            # Grade the answer
            is_correct = grade_answer(predicted_answer, gt_answer)

            generations.append(generated_text)
            predicted_answers.append(predicted_answer if predicted_answer else "[No boxed answer found]")
            is_correct_list.append(is_correct)
            is_formatted_list.append(is_formatted)
            output_token_counts.append(output_token_count)
            finish_reasons.append(finish_reason)
            stop_reasons.append(stop_reason)
            is_truncated_list.append(is_truncated)

        # Calculate metrics for this problem
        num_correct = sum(is_correct_list)
        num_formatted = sum(is_formatted_list)
        has_correct = any(is_correct_list)

        # Majority vote: find the most common answer among formatted predictions
        majority_vote_correct = False
        if num_formatted > 0:
            from collections import Counter

            formatted_predictions = [pred for pred, fmt in zip(predicted_answers, is_formatted_list) if fmt]
            if formatted_predictions:
                most_common_answer = Counter(formatted_predictions).most_common(1)[0][0]
                majority_vote_correct = grade_answer(most_common_answer, gt_answer)

        # Update global metrics
        if has_correct:
            pass_at_n += 1
        if majority_vote_correct:
            majority_vote_correct_count += 1
        total_correct_per_problem += num_correct
        formatted_count += num_formatted
        truncated_count += sum(is_truncated_list)
        total += val_n

        # Store result with all generations
        result = {
            "problem_id": question_id if question_id is not None else idx,
            "problem": problem,
            "ground_truth": gt_answer,
            "val_n": val_n,
            "generations": [
                {
                    "predicted_answer": pred,
                    "full_generation": gen,
                    "correct": corr,
                    "formatted": fmt,
                    "output_token_count": token_count,
                    "finish_reason": finish_reason,
                    "stop_reason": stop_reason,
                    "truncated": truncated,
                }
                for pred, gen, corr, fmt, token_count, finish_reason, stop_reason, truncated in zip(
                    predicted_answers,
                    generations,
                    is_correct_list,
                    is_formatted_list,
                    output_token_counts,
                    finish_reasons,
                    stop_reasons,
                    is_truncated_list,
                )
            ],
            "num_correct": num_correct,
            "pass_at_n": has_correct,
            "majority_vote_correct": majority_vote_correct,
            # For backward compatibility
            "predicted_answer": predicted_answers[0],
            "full_generation": generations[0],
            "correct": is_correct_list[0],
            "formatted": is_formatted_list[0],
        }
        results.append(result)
        # 每题落一次盘：中途挂掉也能留下已完成的部分，而不是一无所获。
        dump_progress()

        # Print progress for each problem
        format_rate = formatted_count / total * 100
        current_pass_at_n = pass_at_n / (idx + 1) * 100
        current_avg_at_n = total_correct_per_problem / total * 100

        # Print brief update for every problem
        status = "✓" if has_correct else "✗"
        print(
            f"{status} [{idx + 1}/{len(dataset)}] Pass@{val_n}: {current_pass_at_n:.1f}% | Avg@{val_n}: {current_avg_at_n:.1f}% | Formatted: {format_rate:.1f}%"
        )

        # Print detailed info every 10 problems
        if (idx + 1) % 10 == 0:
            print(f"\n{'='*70}")
            print(f"Progress: {idx + 1}/{len(dataset)}")
            print(f"Pass@{val_n}: {current_pass_at_n:.2f}%")
            print(f"Average@{val_n}: {current_avg_at_n:.2f}%")
            print(f"Format Rate: {format_rate:.2f}%")
            print(f"Last problem: {problem[:100]}...")
            print(f"Solutions correct: {num_correct}/{val_n}")
            print(f"Majority vote: {'✓' if majority_vote_correct else '✗'}")
            print(f"Ground truth: {gt_answer}")
            print(f"{'='*70}\n")

    # Calculate final metrics
    num_problems = len(dataset)
    format_rate = formatted_count / total * 100
    truncation_rate = truncated_count / total * 100

    # Calculate pass@n, average@n, and majority vote metrics
    pass_at_n_pct = pass_at_n / num_problems * 100
    average_at_n_pct = total_correct_per_problem / total * 100

    # Calculate majority vote accuracy（计数在循环里累加，见 dump_progress）
    majority_vote_at_n_pct = majority_vote_correct_count / num_problems * 100

    print("\n" + "=" * 70)
    print(f"FINAL RESULTS")
    print("=" * 70)
    print(f"Dataset: {dataset_name.upper()}")
    print(f"Thinking Mode: {'ENABLED' if enable_thinking else 'DISABLED'}")
    print(f"Total problems: {num_problems}")
    print(f"Solutions per problem: {val_n}")
    print(f"Total solutions: {total}")
    print(f"\nMetrics:")
    print(f"  Pass@{val_n}: {pass_at_n_pct:.2f}% ({pass_at_n}/{num_problems})")
    print(f"  Average@{val_n}: {average_at_n_pct:.2f}% ({total_correct_per_problem}/{total})")
    print(
        f"  Majority Vote@{val_n}: {majority_vote_at_n_pct:.2f}% ({majority_vote_correct_count}/{num_problems})"
    )
    print(f"\nFormatting:")
    print(f"  Formatted (boxed) answers: {formatted_count}/{total}")
    print(f"  Format rate: {format_rate:.2f}%")
    print(f"  Truncated outputs: {truncated_count}/{total}")
    print(f"  Truncation rate: {truncation_rate:.2f}%")
    print("=" * 70)

    # Save detailed results if output file specified（每道题已经落过盘，
    # 这里再写一次，覆盖掉数据集为空、循环一次都没进的情况）
    if output_file:
        dump_progress()
        print(f"\nDetailed results saved to: {output_file}")

    return average_at_n_pct, results


def main():
    parser = argparse.ArgumentParser(description="Evaluate models on MATH tasks with Qwen3 thinking mode")
    parser.add_argument(
        "--base_model",
        type=str,
        default="/vepfs-mlp2/c20250602/500050/models/Qwen3-8B/qwen/Qwen3-8B",
        help="Path to base model",
    )
    parser.add_argument(
        "--checkpoint_dir",
        type=str,
        default=None,
        help="Path to checkpoint directory with LoRA adapters. If not provided, will use base model only.",
    )
    parser.add_argument(
        "--vllm_base_url",
        type=str,
        default=None,
        help="Remote vLLM OpenAI-compatible base URL, e.g. http://127.0.0.1:8911/v1",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name exposed by the remote vLLM server (required with --vllm_base_url)",
    )
    parser.add_argument("--api_key", type=str, default=None, help="Optional API key for remote vLLM")
    parser.add_argument("--request_timeout", type=float, default=600.0, help="Remote API timeout in seconds")
    parser.add_argument("--max_retries", type=int, default=3, help="Remote API retries per problem")
    parser.add_argument(
        "--dataset",
        type=str,
        default="auto",
        help="Optional dataset label for logging; local files are always auto-mapped (default: auto)",
    )
    parser.add_argument(
        "--dataset_path",
        type=str,
        default=None,
        help="Required local JSON/JSONL/Parquet dataset path",
    )
    parser.add_argument(
        "--max_new_tokens",
        type=int,
        default=38912,
        help="Maximum tokens to generate (default: 32768, use 38912 for complex competition problems)",
    )
    parser.add_argument(
        "--enable_thinking",
        action="store_true",
        default=True,
        help="Enable Qwen3 thinking mode (default: True)",
    )
    parser.add_argument(
        "--no_thinking", dest="enable_thinking", action="store_false", help="Disable Qwen3 thinking mode"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Sampling temperature (auto: 0.6 for thinking, 0.7 for non-thinking)",
    )
    parser.add_argument(
        "--top_p",
        type=float,
        default=None,
        help="Top-p sampling parameter (auto: 0.95 for thinking, 0.8 for non-thinking)",
    )
    parser.add_argument(
        "--top_k", type=int, default=-1, help="Top-k sampling parameter (default: -1, disabled)"
    )
    parser.add_argument(
        "--min_p", type=float, default=0.0, help="Minimum probability threshold (default: 0.0)"
    )
    parser.add_argument(
        "--presence_penalty",
        type=float,
        default=0.0,
        help="Presence penalty to reduce repetitions (0-2, default: 0.0)",
    )
    parser.add_argument(
        "--num_samples", type=int, default=None, help="Number of samples to evaluate (None = all)"
    )
    parser.add_argument("--output_file", type=str, default=None, help="Path to save detailed results JSON")
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="Directory for results JSON; if set, an output filename is generated automatically",
    )
    parser.add_argument(
        "--gpu_memory_utilization",
        type=float,
        default=0.9,
        help="GPU memory utilization for vLLM (0.0 to 1.0, default: 0.9)",
    )
    parser.add_argument(
        "--tensor_parallel_size",
        type=int,
        default=1,
        help="Number of GPUs to use for tensor parallelism (default: 1)",
    )
    parser.add_argument(
        "--max_model_len",
        type=int,
        default=None,
        help="Maximum model context length (auto: 40960 for thinking, 32768 for non-thinking)",
    )
    parser.add_argument(
        "--max_num_seqs",
        type=int,
        default=None,
        help="Optional vLLM concurrency cap for long-context evaluation",
    )
    parser.add_argument(
        "--val_n", type=int, default=6, help="Number of solutions to sample per problem (default: 6)"
    )

    args = parser.parse_args()

    if args.vllm_base_url and not args.model:
        parser.error("--model is required when --vllm_base_url is provided")
    if args.vllm_base_url and args.checkpoint_dir:
        parser.error("--checkpoint_dir is only valid for local vLLM mode")
    if args.output_file and args.output_dir:
        parser.error("Use either --output_file or --output_dir, not both")

    # Validate checkpoint directory exists if provided
    if args.checkpoint_dir is not None:
        checkpoint_path = Path(args.checkpoint_dir)
        if not checkpoint_path.exists():
            print(f"\n{'='*70}")
            print("ERROR: Checkpoint directory does not exist")
            print(f"{'='*70}")
            print(f"Provided checkpoint directory: {args.checkpoint_dir}")
            print("This directory does not exist.")
            print(
                "\nPlease provide a valid checkpoint directory or omit --checkpoint_dir to use the base model only."
            )
            print(f"{'='*70}\n")
            exit(1)

    if args.top_p is None:
        args.top_p = 0.95 if args.enable_thinking else 0.8
        print(
            f"Auto-setting top_p to {args.top_p} for {'thinking' if args.enable_thinking else 'non-thinking'} mode"
        )

    # Warn if using greedy decoding in thinking mode
    if args.enable_thinking and args.temperature == 0.0:
        print("\n" + "!" * 70)
        print("WARNING: Using greedy decoding (temperature=0.0) in thinking mode!")
        print("Qwen3 recommends temperature=0.6 for thinking mode to avoid")
        print("performance degradation and endless repetitions.")
        print("!" * 70 + "\n")

    # Auto-generate output file if not specified
    if args.output_file is None:
        model_label = args.model or Path(args.base_model).name
        dataset_label = args.dataset if args.dataset != "auto" else (Path(args.dataset_path).stem if args.dataset_path else "local")
        parts = ["eval_results", dataset_label, model_label]
        if args.checkpoint_dir:
            checkpoint_path = Path(args.checkpoint_dir)
            parts += [checkpoint_path.parent.name, checkpoint_path.name]
        parts += [
            "thinking" if args.enable_thinking else "nonthinking",
            f"temp{args.temperature}",
            f"valn{args.val_n}",
        ]
        generated_filename = "_".join(parts) + ".json"
        output_parent = Path(args.output_dir) if args.output_dir else Path("eval_results")
        args.output_file = str(output_parent / generated_filename)

    print(f"Results will be saved to: {args.output_file}")

    print("\n" + "=" * 70)
    print("QWEN3 MATH EVALUATION WITH THINKING MODE")
    print("=" * 70)
    print(f"Dataset: {args.dataset.upper() if args.dataset else 'AUTO'}")
    print(f"Base model: {args.base_model}")
    print(f"Remote vLLM URL: {args.vllm_base_url or 'None (local vLLM mode)'}")
    print(f"Remote model: {args.model or 'None'}")
    print(f"Checkpoint: {args.checkpoint_dir or 'None (base model only)'}")
    print(f"Thinking Mode: {'ENABLED ✓' if args.enable_thinking else 'DISABLED'}")
    print(f"Max tokens: {args.max_new_tokens}")
    print(
        f"Temperature: {args.temperature} (Qwen3 {'thinking' if args.enable_thinking else 'non-thinking'} mode)"
    )
    print(f"Top-p: {args.top_p}")
    print(f"Top-k: {args.top_k}")
    print(f"Min-p: {args.min_p}")
    print(f"Presence penalty: {args.presence_penalty}")
    print(f"Num samples: {args.num_samples or 'All'}")
    print(f"Val-N (solutions per problem): {args.val_n}")
    print(f"Output file: {args.output_file}")
    print(f"GPU memory utilization: {args.gpu_memory_utilization}")
    print(f"Tensor parallel size: {args.tensor_parallel_size}")
    print(f"Max concurrent sequences: {args.max_num_seqs or 'vLLM default'}")
    print("=" * 70 + "\n")

    if args.vllm_base_url:
        llm = _RemoteVLLM(
            args.vllm_base_url,
            args.model,
            api_key=args.api_key,
            timeout=args.request_timeout,
            max_retries=args.max_retries,
            enable_thinking=args.enable_thinking,
        )
        tokenizer = _RemoteTokenizer()
        print(f"Using remote vLLM API at {args.vllm_base_url}")
    else:
        # Load model with local vLLM.
        llm, tokenizer = load_vllm_model(
            args.base_model,
            args.checkpoint_dir,
            gpu_memory_utilization=args.gpu_memory_utilization,
            tensor_parallel_size=args.tensor_parallel_size,
            max_model_len=args.max_model_len,
            max_num_seqs=args.max_num_seqs,
            enable_thinking=args.enable_thinking,
        )

    # Setup LoRA request if checkpoint is provided
    lora_request = None
    if args.checkpoint_dir is not None:
        try:
            from vllm.lora.request import LoRARequest

            # Verify LoRA weights exist
            adapter_safetensors = Path(args.checkpoint_dir) / "adapter_model.safetensors"
            adapter_bin = Path(args.checkpoint_dir) / "adapter_model.bin"

            if adapter_safetensors.exists() or adapter_bin.exists():
                lora_request = LoRARequest("checkpoint_lora", 1, args.checkpoint_dir)
                print(f"✓ Successfully created LoRA request for: {args.checkpoint_dir}")
            else:
                print(f"Warning: No LoRA adapter weights found at {args.checkpoint_dir}")
                print("Expected 'adapter_model.safetensors' or 'adapter_model.bin'")
                print("Continuing with base model only...")
        except ImportError:
            print("Warning: Could not import LoRARequest. Running without LoRA.")
        except Exception as e:
            print(f"Warning: Could not create LoRA request: {e}")
            print("Continuing without LoRA.")

    # Run evaluation
    average_at_n_pct, results = evaluate_math500(
        llm,
        tokenizer,
        max_new_tokens=args.max_new_tokens,
        max_model_len=args.max_model_len,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        min_p=args.min_p,
        presence_penalty=args.presence_penalty,
        num_samples=args.num_samples,
        output_file=args.output_file,
        lora_request=lora_request,
        dataset_name=args.dataset,
        base_model_name=args.model or args.base_model,
        checkpoint_dir=args.checkpoint_dir,
        enable_thinking=args.enable_thinking,
        val_n=args.val_n,
        dataset_path=args.dataset_path,
    )

    print("\n" + "=" * 70)
    print("EVALUATION COMPLETE!")
    print("=" * 70)
    print(f"Final Average@{args.val_n}: {average_at_n_pct:.2f}%")
    print(f"Results saved to: {args.output_file}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
