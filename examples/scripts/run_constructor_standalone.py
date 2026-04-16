#!/usr/bin/env python3
"""
独立跑 Constructor 主图（与主流程一致），或仅跑清洗子图做调试。

**默认 ``--mode full``**（完整 Constructor）::

  start → postprocess v2（从 ``downloads`` 扫数据集、导出 ``processed_output/related_jsonl``）
  → 清洗子图（中间路径由 postprocess 写入，**无需手填**）
  → 映射子图（默认 ``alpaca``，免交互）

仅需准备好 **下载根目录**（内含数据集子目录，与线上一致）和 **benchmark 目录**。

**``--mode cleaning``**（仅清洗）：不跑 postprocess。中间 JSONL 默认从
``{DOWNLOAD_DIR}/processed_output/related_jsonl`` 推断；也可用 ``--intermediate`` 显式指定。

命令行与环境变量可覆盖下方 CONFIG。

末尾 CoT：设置 ``APPEND_COT_AFTER_CLEANING``、环境变量 ``APPEND_COT_AFTER_CLEANING``（true/false），
或命令行 ``--append-cot-after-cleaning`` / ``--no-append-cot-after-cleaning``。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG — 命令行与环境变量可覆盖
# ═══════════════════════════════════════════════════════════════════════

USER_QUERY = "收集数据集用于大模型微调，以下是数据集的获取的建议：1.  **增强多因素关联分析能力**：针对利润、增长率等问题，设计专项训练数据，强调识别所有相关成本/价值基数，并明确增长、利润等概念的精确定义。建议在推理链中增加“明确所有基准项”的验证步骤。2.  **强化过程推理的完整性校验**：对于包含状态重置、阶段回溯的问题，在训练中引入“状态检查点”机制。要求模型在每一步推理后明确当前状态（如已下载量、当前位置），并在条件变更时（如重启）重新评估初始状态，确保整个流程的推导闭环。3.  **提升对问题目标的精准对齐**：在指令微调阶段，加强对问题最终询问目标的强调（例如，“距离家多远”vs“行驶了多远”）。可以设计对比学习样本，让模型区分中间步骤输出与最终答案要求的区别，确保推理指向性明确。4.  **引入分步计算验证与回溯**：在模型生成解答时，鼓励或强制其展示关键子步骤的计算结果。可探索让模型在输出最终答案前，自行复核关键运算，或通过提示工程要求其“逐步计算并确认”，以减少低级计算错误和推理中断。"
MODEL_NAME = "gpt-4o-mini"
BASE_URL = "http://123.129.219.111:3000/"
API_KEY = "sk-..."

# full 模式：Postprocess 扫描的数据集根目录（与 DOWNLOAD_DIR 一致）
OUTPUT_DIR = os.path.join(_REPO_ROOT, "outputs")
DOWNLOAD_DIR = os.path.join(_REPO_ROOT, "outputs", "downloads")
# benchmark 源目录（用于初始化采样池）
BENCHMARK_SOURCE_DIR = os.path.join(_REPO_ROOT, "outputs", "benchmark")
# benchmark 采样池路径
BENCHMARK_POOL_PATH = os.path.join(_REPO_ROOT, "outputs", "benchmark_load", "benchmark_pool.jsonl")
BENCHMARK_POOL_SIZE = 5
# 初始化采样池时 random.sample 的种子（仅当池文件不存在、会调用 initialize_benchmark_pool 时生效）
BENCHMARK_SAMPLE_SEED = 20260411
# postprocess 落盘 → 清洗：apply_sampling 蓄水池 + ShareGPT benchmark 抽取（与池初始化独立）
CLEANING_RANDOM_SEED = 20260411

# 仅 cleaning 模式且要跳过自动推断时填写；full 模式忽略
INTERMEDIATE_DATA_DIR = ""

CATEGORY = "SFT"
MAX_SAMPLES_BEFORE_CLEANING = 21000
DATASETS_BACKGROUND = "通用领域问答数据集，包含各种领域的数据以及推理。"
# 映射免交互（与 Constructor 默认一致）
DEFAULT_MAPPING_FORMAT = "alpaca"
CONSTRUCTOR_TEST_MODE = "full"  # full | cleaning
# Benchmark 清洗之后是否跑末尾 CoT（norma_filter_and_add_cot），并将 output 写成 <think>…</think> + 原文
APPEND_COT_AFTER_CLEANING = True


def _normalize_openai_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return u
    if not u.endswith("/v1"):
        u = f"{u}/v1"
    return u


def _infer_intermediate_from_postprocess_output(download_dir: str) -> str:
    """与 postprocess v2 落盘一致：优先 related_jsonl，否则 processed_output 根目录。"""
    po = os.path.join(download_dir, "processed_output")
    rel = os.path.join(po, "related_jsonl")
    if os.path.isdir(rel):
        try:
            if any(
                fn.endswith(".jsonl") and os.path.isfile(os.path.join(rel, fn))
                for fn in os.listdir(rel)
            ):
                return os.path.abspath(rel)
        except OSError:
            pass
    if os.path.isdir(po):
        return os.path.abspath(po)
    return ""


def _build_base_state(
    *,
    user_query: str,
    output_dir: str,
    intermediate_data_path: str,
    benchmark_source_dir: str,
    benchmark_pool_path: str,
    benchmark_pool_size: int,
    model: str,
    base_url: str,
    api_key: str,
    category: str,
    max_samples: int,
    datasets_background: str,
    download_dir: str,
    append_cot_after_cleaning: bool = False,
    cleaning_random_seed: Optional[int] = None,
) -> Dict[str, Any]:
    constructor: Dict[str, Any] = {
        "user_query": user_query,
        "datasets_background": datasets_background,
        "category": category.upper(),
        "model_path": model,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": 0.0,
        "intermediate_data_path": intermediate_data_path,
        "max_samples_before_cleaning": max_samples,
        "max_retries": 3,
        "llm_timeout": 300.0,
        "postprocess_version": "agent_v2",
        "default_mapping_format": DEFAULT_MAPPING_FORMAT,
        "benchmark_source_dir": benchmark_source_dir,
        "benchmark_pool_path": benchmark_pool_path,
        "benchmark_pool_size": benchmark_pool_size,
        "append_cot_after_cleaning": append_cot_after_cleaning,
    }
    if cleaning_random_seed is not None:
        constructor["cleaning_random_seed"] = cleaning_random_seed

    return {
        "task_id": "standalone-constructor-test",
        "mined_data": "",
        "output_dir": output_dir,
        "download_dir": download_dir,
        "obtainer": {
            "subtasks": [
                {
                    "type": "download",
                    "status": "completed_successfully",
                    "objective": user_query,
                }
            ],
        },
        "constructor": constructor,
        "configer": {},
        "judger": {},
        "analyzer": {},
        "trainer": {},
        "webcrawler": {},
        "messages": [],
        "current": "",
        "next_to": "",
        "automated_query": user_query,
        "obtainer_subtask_query": "",
        "exception": "",
    }


def run_cleaning_subgraph(state: Dict[str, Any]) -> Dict[str, Any]:
    from loopai.agents.Constructor.nodes.filter_node import CleaningSubgraph

    graph = CleaningSubgraph().build()
    return graph.invoke(state)


def run_full_constructor(state: Dict[str, Any], api_key: str, base_url: str, model: str) -> Dict[str, Any]:
    from langgraph.checkpoint.memory import MemorySaver

    from loopai.agents.Constructor.Constructor_agent import ConstructorAgent

    agent = ConstructorAgent(
        model_name=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.0,
        checkpointer=MemorySaver(),
    )
    graph = agent()
    cfg = {"configurable": {"thread_id": state["task_id"]}}
    return graph.invoke(state, cfg)


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone Constructor（默认完整主图）或仅清洗")
    p.add_argument(
        "--mode",
        choices=("full", "cleaning"),
        default=os.getenv("CONSTRUCTOR_TEST_MODE", CONSTRUCTOR_TEST_MODE),
        help="full=Constructor 全图（postprocess→清洗→映射）；cleaning=仅清洗子图",
    )
    p.add_argument("--user-query", default=os.getenv("USER_QUERY", USER_QUERY))
    p.add_argument(
        "--intermediate",
        default=os.getenv("INTERMEDIATE_DATA_DIR", INTERMEDIATE_DATA_DIR),
        help="仅 cleaning：中间 JSONL 目录/文件；空则根据 DOWNLOAD_DIR/processed_output 推断",
    )
    p.add_argument(
        "--benchmark-source-dir",
        default=os.getenv("BENCHMARK_SOURCE_DIR", BENCHMARK_SOURCE_DIR),
        help="benchmark 源目录（用于初始化采样池）",
    )
    p.add_argument(
        "--benchmark-pool-path",
        default=os.getenv("BENCHMARK_POOL_PATH", BENCHMARK_POOL_PATH),
        help="benchmark 采样池文件路径",
    )
    p.add_argument(
        "--benchmark-pool-size",
        type=int,
        default=int(os.getenv("BENCHMARK_POOL_SIZE", str(BENCHMARK_POOL_SIZE))),
        help="benchmark 采样池大小",
    )
    p.add_argument(
        "--benchmark-sample-seed",
        type=int,
        default=int(os.getenv("BENCHMARK_SAMPLE_SEED", str(BENCHMARK_SAMPLE_SEED))),
        help="初始化 benchmark 采样池时的随机种子（已有 pool 文件时不会重新采样，需删池文件才生效）",
    )
    p.add_argument(
        "--cleaning-random-seed",
        type=int,
        default=int(os.getenv("CLEANING_RANDOM_SEED", str(CLEANING_RANDOM_SEED))),
        help="清洗子图：postprocess 后 apply_sampling 蓄水池与 ShareGPT benchmark 抽取的随机种子（可复现）",
    )
    p.add_argument("--output-dir", default=os.getenv("OUTPUT_DIR", OUTPUT_DIR))
    p.add_argument("--download-dir", default=os.getenv("DOWNLOAD_DIR", DOWNLOAD_DIR), help="数据集根目录（postprocess 扫描）")
    p.add_argument("--category", default=os.getenv("CATEGORY", CATEGORY))
    p.add_argument("--max-samples", type=int, default=int(os.getenv("MAX_SAMPLES_BEFORE_CLEANING", str(MAX_SAMPLES_BEFORE_CLEANING))))
    p.add_argument("--datasets-background", default=os.getenv("DATASETS_BACKGROUND", DATASETS_BACKGROUND))
    p.add_argument("--model", default=os.getenv("MODEL_NAME", MODEL_NAME))
    p.add_argument("--base-url", default=os.getenv("BASE_URL", BASE_URL))
    p.add_argument(
        "--api-key",
        default=os.getenv("CONSTRUCTOR_API_KEY") or os.getenv("OPENAI_API_KEY") or API_KEY,
    )
    p.add_argument(
        "--append-cot-after-cleaning",
        action="store_true",
        help="清洗子图在 benchmark 之后执行 CoT，并改写 output（<think>…</think> + 原文）",
    )
    p.add_argument(
        "--no-append-cot-after-cleaning",
        action="store_true",
        help="显式关闭末尾 CoT（覆盖 CONFIG / 环境变量 / --append-cot-after-cleaning）",
    )
    args = p.parse_args()

    # 优先级：CLI 显式开关 > 环境变量 APPEND_COT_AFTER_CLEANING > CONFIG
    append_cot = bool(APPEND_COT_AFTER_CLEANING)
    env_raw = (os.getenv("APPEND_COT_AFTER_CLEANING") or "").strip().lower()
    if env_raw in ("1", "true", "yes"):
        append_cot = True
    elif env_raw in ("0", "false", "no"):
        append_cot = False
    if args.append_cot_after_cleaning:
        append_cot = True
    if args.no_append_cot_after_cleaning:
        append_cot = False

    api_key = (args.api_key or "").strip()
    if not api_key:
        print("错误: 请在 CONFIG 中填写 API_KEY，或设置 CONSTRUCTOR_API_KEY / --api-key")
        sys.exit(1)

    base_url_raw = (args.base_url or "").strip()
    if not base_url_raw:
        print("错误: 请在 CONFIG 中填写 BASE_URL")
        sys.exit(1)
    base_url = _normalize_openai_base_url(base_url_raw)

    benchmark_source_dir = (args.benchmark_source_dir or "").strip()
    if not benchmark_source_dir or not os.path.isdir(benchmark_source_dir):
        print(f"错误: benchmark 源目录不存在: {benchmark_source_dir}")
        sys.exit(1)

    benchmark_pool_path = (args.benchmark_pool_path or "").strip()
    if not benchmark_pool_path:
        print("错误: 请指定 benchmark_pool_path")
        sys.exit(1)

    # 初始化 benchmark 采样池（如果不存在或需要更新）
    if not os.path.isfile(benchmark_pool_path):
        print(f"初始化 benchmark 采样池: {benchmark_pool_path}")
        from loopai.agents.Postprocess.tools.benchmark_sampler import initialize_benchmark_pool

        result = initialize_benchmark_pool(
            benchmark_source_dir=benchmark_source_dir,
            pool_path=benchmark_pool_path,
            pool_size=args.benchmark_pool_size,
            random_seed=args.benchmark_sample_seed,
        )
        if not result.get("success"):
            print(f"错误: 初始化 benchmark 采样池失败: {result.get('error', 'unknown')}")
            sys.exit(1)
        print(
            f"✓ 采样池初始化完成: {result.get('pool_size')} 条样本 "
            f"(random_seed={result.get('random_seed')})"
        )
    else:
        print(f"✓ 使用已有 benchmark 采样池: {benchmark_pool_path}")

    download_dir = os.path.abspath((args.download_dir or "").strip() or os.path.join(args.output_dir, "downloads"))
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(download_dir, exist_ok=True)

    if args.mode == "full":
        if not os.path.isdir(download_dir):
            print("错误: --download-dir 必须是已存在的数据集根目录")
            sys.exit(1)
        intermediate = ""
    else:
        explicit = (args.intermediate or "").strip()
        if explicit and os.path.exists(explicit):
            intermediate = os.path.abspath(explicit)
        else:
            intermediate = _infer_intermediate_from_postprocess_output(download_dir)
        if not intermediate or not os.path.exists(intermediate):
            print(
                "错误: cleaning 模式需要已有 postprocess 产物。\n"
                "  请先跑一次 full，或指定 --intermediate 为 related_jsonl 目录/文件。\n"
                f"  已尝试推断: {_infer_intermediate_from_postprocess_output(download_dir) or '(无)'}"
            )
            sys.exit(1)

    state = _build_base_state(
        user_query=args.user_query,
        output_dir=os.path.abspath(args.output_dir),
        intermediate_data_path=intermediate,
        benchmark_source_dir=os.path.abspath(benchmark_source_dir),
        benchmark_pool_path=os.path.abspath(benchmark_pool_path),
        benchmark_pool_size=args.benchmark_pool_size,
        model=args.model.strip(),
        base_url=base_url,
        api_key=api_key,
        category=args.category,
        max_samples=args.max_samples,
        datasets_background=args.datasets_background,
        download_dir=download_dir,
        append_cot_after_cleaning=append_cot,
        cleaning_random_seed=args.cleaning_random_seed,
    )

    print("=" * 60)
    print("  Constructor 独立测试")
    print("=" * 60)
    print(f"  mode:                {args.mode}")
    print(f"  model:               {args.model}")
    print(f"  base_url:            {base_url}")
    print(f"  download_dir:        {download_dir}")
    print(f"  benchmark_source:    {state['constructor']['benchmark_source_dir']}")
    print(f"  benchmark_pool:      {state['constructor']['benchmark_pool_path']}")
    print(f"  benchmark_pool_size: {state['constructor']['benchmark_pool_size']}")
    print(f"  benchmark_sample_seed: {args.benchmark_sample_seed} (仅新建池文件时用于采样)")
    print(f"  cleaning_random_seed:  {args.cleaning_random_seed} (蓄水池 + ShareGPT benchmark 抽取)")
    if args.mode == "cleaning":
        print(f"  intermediate:        {state['constructor']['intermediate_data_path']}")
    else:
        print(f"  intermediate:        (由 postprocess v2 写入，通常为 …/processed_output/related_jsonl)")
    print(f"  category:            {state['constructor']['category']}")
    print(f"  append_cot_after_cleaning: {state['constructor'].get('append_cot_after_cleaning', True)}")
    print(f"  mapping_format:      {state['constructor']['default_mapping_format']}")
    print(f"  user_query:          {args.user_query[:80]}...")
    print("=" * 60)

    if args.mode == "cleaning":
        out = run_cleaning_subgraph(state)
    else:
        out = run_full_constructor(state, api_key=api_key, base_url=base_url, model=args.model.strip())

    cons = out.get("constructor") or {}
    print("\n--- 结果摘要 ---")
    print(f"  exception:              {out.get('exception') or '(无)'}")
    print(f"  intermediate_data_path: {cons.get('intermediate_data_path', '')}")
    print(f"  cleaning_tool_plan:     {cons.get('cleaning_tool_plan')}")
    cr = cons.get("cleaning_results") or {}
    if cr.get("tools_executed"):
        for item in cr["tools_executed"]:
            t = item.get("tool") if isinstance(item, dict) else item
            print(f"    - tool: {t}")
    # if cons.get("cleaning_sharegpt_rewrite"):
    #     print(f"  sharegpt_rewrite:       {cons.get('cleaning_sharegpt_rewrite')}")
    if cons.get("postprocess_results"):
        pr = cons["postprocess_results"]
        print(f"  postprocess:            output_dir={pr.get('output_dir', '')}")
        print(f"                          related_jsonl_dir={pr.get('related_jsonl_dir', '')}")
    print("\n完成。")


if __name__ == "__main__":
    main()
