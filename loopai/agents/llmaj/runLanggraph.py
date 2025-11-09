# -*- coding: utf-8 -*-
"""
LangGraph 版本的统一入口 run.py
- 节点1 model_test: 调用 modelTest.py（其内部使用 V2/run.py 的 CONFIG）
- 节点2 discover_outputs: 在 outdir 中自动寻找最新 summary 与 oj_records
- 节点3 llm_judge: 调用 llmJudge.py 生成模型表现评估（文本 + json）
- 节点4 code_conclusion: 调用 codeConclusion.py 生成最终报告（可选 --suggest）

CLI 说明：
- 不在本文件传 --samples / --humaneval；路径仍以 V2/run.py 的 CONFIG 为准
- 任何未显式声明的参数原样透传给 modelTest.py（例如你在 modelTest.py 中自定义了 --use-internal-config）
"""

from __future__ import annotations
import sys
import argparse
import subprocess
from pathlib import Path
from typing import TypedDict, List, Optional, Dict, Any

# --------------- 基本路径 ---------------
ROOT = Path(__file__).resolve().parent
V2_DIR = ROOT / "V2"

MODEL_TEST = ROOT / "modelTest.py"
LLM_JUDGE = ROOT / "llmJudge.py"
CODE_CONCLUSION = ROOT / "codeConclusion.py"

DEFAULT_OUTDIR = V2_DIR / "result"


# --------------- LangGraph ---------------
# 如果你的环境未安装，可用：pip install "langgraph>=0.2.0"
try:
    from langgraph.graph import StateGraph
except Exception as e:
    print("需要安装 langgraph：pip install 'langgraph>=0.2.0'", file=sys.stderr)
    raise


# --------------- 状态定义 ---------------
class PipelineState(TypedDict, total=False):

    outdir: str
    no_brief: bool
    suggest: bool
    extra: List[str]

    summary_path: str
    oj_path: str

    judge_json: str
    judge_txt: str
    final_report_json: Optional[str]
    final_report_txt: Optional[str]


# --------------- 工具函数 ---------------
def _must_exist(p: Path, hint: str = ""):
    if not p.is_file():
        msg = f"[ERR] 找不到文件：{p}"
        if hint:
            msg += f"\nHint: {hint}"
        raise FileNotFoundError(msg)


def _latest(dirpath: Path, pattern: str) -> Optional[Path]:
    files = sorted(dirpath.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _print_cmd(title: str, cmd: List[str]):
    print(f"{title} {' '.join(cmd)}", flush=True)


# --------------- 节点实现 ---------------
def node_model_test(state: PipelineState) -> PipelineState:
    """节点1：调用 modelTest.py"""
    outdir = Path(state["outdir"]).resolve()
    _must_exist(MODEL_TEST)
    _must_exist(LLM_JUDGE)
    _must_exist(CODE_CONCLUSION)
    outdir.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, str(MODEL_TEST), "--outdir", str(outdir)]
    if state.get("no_brief"):
        cmd += ["--no-brief"]
    # 透传其余自定义参数
    extra = state.get("extra", [])
    if extra:
        cmd += extra

    _print_cmd("▶️ 1/4 运行 modelTest.py：", cmd)
    subprocess.run(cmd, check=True)
    return state


def node_discover_outputs(state: PipelineState) -> PipelineState:
    """节点2：在 outdir 中寻找 summary 与 oj_records"""
    outdir = Path(state["outdir"]).resolve()

    summary = (_latest(outdir, "summary_*.json") or
               _latest(outdir, "*/summary_*.json"))
    if summary is None:
        raise FileNotFoundError(f"在 {outdir} 未找到 summary_*.json（请检查 V2/run.py 的输出）")

 
    oj = (_latest(outdir, "oj_records_enriched_*.jsonl") or
          _latest(outdir, "oj_records_*.jsonl") or
          _latest(outdir, "*.jsonl"))
    if oj is None:
        raise FileNotFoundError(f"在 {outdir} 未找到 oj_records_*.jsonl（请检查 V2/run.py / modelTest.py 输出）")

    print(f"找到 summary：{summary.name}")
    print(f"找到 oj_records：{oj.name}")

    state["summary_path"] = str(summary)
    state["oj_path"] = str(oj)
    return state


def node_llm_judge(state: PipelineState) -> PipelineState:
    """节点3：调用 llmJudge.py 生成模型表现评估（文本 + json）"""
    outdir = Path(state["outdir"]).resolve()
    summary = Path(state["summary_path"])
    oj = Path(state["oj_path"])

    judge_json = outdir / "model_eval_llm.json"
    judge_txt = outdir / "model_eval_llm.txt"

    cmd: List[str] = [
        sys.executable, str(LLM_JUDGE),
        str(summary),
        "--oj_path", str(oj),
        "--out_json", str(judge_json),
        "--out_txt", str(judge_txt),
    ]
    _print_cmd("▶️ 2/4 运行 llmJudge.py：", cmd)
    subprocess.run(cmd, check=True)

    state["judge_json"] = str(judge_json)
    state["judge_txt"] = str(judge_txt)
    return state


def node_code_conclusion(state: PipelineState) -> PipelineState:
    """节点4：调用 codeConclusion.py 生成最终报告（可选 --suggest）"""
    outdir = Path(state["outdir"]).resolve()
    summary = Path(state["summary_path"])

    cmd: List[str] = [
        sys.executable, str(CODE_CONCLUSION),
        str(summary),
        "--outdir", str(outdir),
    ]
    if state.get("suggest"):
        cmd.append("--suggest")

    _print_cmd("▶️ 3/4 运行 codeConclusion.py：", cmd)
    subprocess.run(cmd, check=True)

    fr_json = _latest(outdir, "final_report_*.json")
    fr_txt = _latest(outdir, "final_report_*.txt")
    state["final_report_json"] = str(fr_json) if fr_json else None
    state["final_report_txt"] = str(fr_txt) if fr_txt else None

    print("\n🎉 全流程完成！主要输出：")
    print(f"- OJ 记录：{state['oj_path']}")
    print(f"- LLM 评估（JSON）：{state['judge_json']}")
    print(f"- LLM 评估（TXT）：{state['judge_txt']}")
    if state["final_report_json"]:
        print(f"- 最终报告 JSON：{state['final_report_json']}")
    if state["final_report_txt"]:
        print(f"- 最终报告 TXT：{state['final_report_txt']}")
    print("✅ Done.")

    return state


# --------------- 构图 ---------------
def build_graph():
    g = StateGraph(PipelineState)
    g.add_node("model_test", node_model_test)
    g.add_node("discover_outputs", node_discover_outputs)
    g.add_node("llm_judge", node_llm_judge)
    g.add_node("code_conclusion", node_code_conclusion)

    g.set_entry_point("model_test")
    g.add_edge("model_test", "discover_outputs")
    g.add_edge("discover_outputs", "llm_judge")
    g.add_edge("llm_judge", "code_conclusion")
    g.set_finish_point("code_conclusion")

    return g.compile()


# --------------- CLI 入口 ---------------
def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="LangGraph Pipeline: modelTest -> discover -> llmJudge -> codeConclusion (paths from V2/run.py CONFIG)."
    )
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR),
                    help=f"输出目录（默认 {DEFAULT_OUTDIR}）")
    ap.add_argument("--no-brief", action="store_true",
                    help="只做 LLMaJ 判因，不生成中文短评（透传给 modelTest.py）")
    ap.add_argument("--suggest", action="store_true",
                    help="最终报告阶段启用模型建议（透传给 codeConclusion.py）")
 
    ap.add_argument("extra", nargs="*", help="其它将透传给 modelTest.py 的参数")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    app = build_graph()
    init_state: PipelineState = {
        "outdir": str(Path(args.outdir).resolve()),
        "no_brief": bool(args.no_brief),
        "suggest": bool(args.suggest),
        "extra": list(args.extra) if args.extra else [],
    }

    app.invoke(init_state)


if __name__ == "__main__":
    main()