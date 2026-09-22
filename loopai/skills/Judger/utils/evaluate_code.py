# -*- coding: utf-8 -*-
"""code 评测：把「后处理 + 判分」整段交给 evalplus 的官方镜像。

分工和 math 分支同构 —— 宿主机 Judger 只负责起 vLLM + 采样，其余在容器里：

```
generate（宿主机 vLLM）──> evaluate（ganler/evalplus 官方镜像）
<bench>_sample.jsonl        evalplus.sanitize → evalplus.evaluate
                            <bench>_result.jsonl / <bench>_summary.json
```

为什么分两步跑官方 CLI、而不是让 ``evalplus.evaluate`` 一把梭：它只有在带
``--model/--backend`` 时才会自己生成（``run_codegen`` → ``codegen`` → ``sanitize``），
而 vLLM 生命周期、采样参数和 ``<bench>_sample.jsonl`` 契约都归 Judger（Analyzer /
DataFlow 都读它）。只给 ``--samples`` 时它**不做后处理** —— ``solution`` 有就用、
没有就 ``prompt + completion`` 直接执行，所以抽取这一步显式调官方的
``evalplus.sanitize``，口径仍然是 evalplus 自己的。

用官方镜像意味着**这边不构建镜像、不 vendor 源码**：容器里跑的就是镜像自带的
evalplus 和它构建期烤好的 HumanEval+ / MBPP+（164 / 378 题）。代价是数据集版本跟
镜像走（HumanEval+ 的 prompt/task_id 长期稳定；MBPP 必须用 evalplus 那份 378 题的
MBPP+，原始 MBPP 的 974/500 题对不上）。

样本契约：``<bench>_sample.jsonl`` 每行 ``{"task_id": "HumanEval/0", "completion": ...}``，
是模型在题目 prompt 之后的**原始续写**，不截断、不抽取；``task_id`` 必须和镜像数据集
对得上，且**每一题都要有样本**（``evalplus.evaluate`` 内部是 assert）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

from loopai.common.exception import ErrorCode, emit_error

# evalplus 官方镜像（Dockerfile 在 evalplus 仓库根目录，CMD 是 bash，所以我们自己给命令）
CODE_EVAL_IMAGE = os.getenv("CODE_EVAL_IMAGE", "ganler/evalplus:latest")

# LiveCodeBench 分支用的镜像。它和 evalplus 分支的分工不同：生成和判分都在容器里
# 完成（容器通过 ``--vllm_base_url`` 调 Judger 起的 vLLM），不需要 evalplus，
# 也没有固定的 task_id 前缀和题数 —— 题目随 release 版本变化
# （codegeneration v1=400 … v6=1055，testoutputprediction 442）。
#
# 这个镜像是我们自己 fork 的（上游 LCB 源码 + Judger 改动），没有官方镜像可拉：源码和
# Dockerfile 就在 ``loopai/skills/Judger/docker/livecodebench/``，缺镜像时现场构建
# （见 ``_ensure_livecodebench_image``），和 math 评测镜像同一套做法。
LIVE_CODEBENCH = "livecodebench"
LCB_EVAL_IMAGE = os.getenv("LCB_EVAL_IMAGE", "livecodebench:latest")
LCB_EVAL_CONTEXT = Path(__file__).resolve().parent.parent / "docker" / "livecodebench"

# code bench 的 ``format_type`` 支持这三种写法：前两种直接对应 evalplus 的两个数据集
# （也是官方镜像里烤好的那两份），第三种是 code 任务下的另一个判分后端 LiveCodeBench。
# 三个都是 ``task_type=code``，只是后端不同；不做别名：名字写错就当场报错，不猜。
FORMAT_TYPES = {
    "humaneval+": "humaneval",
    "mbpp+": "mbpp",
    LIVE_CODEBENCH: LIVE_CODEBENCH,
}

# 判分容器认得的数据集名（evalplus 那两个 + LiveCodeBench）
CODE_TASKS = tuple(FORMAT_TYPES.values())

# 判分容器一律离线：vLLM 在宿主机、题目和样本都在镜像/挂载里，判分环境不该联网。
DEFAULT_NETWORK = "none"

# evalplus 的 pass@k 估计量：1 - C(n-c, k) / C(n, k)。
# 镜像里 0.3.x 只把 pass@k 打印到 stdout、不写结果文件，所以这里按同一公式自己算，
# 免得去解析日志。k 的取值和 evalplus 一致（1/10/100，样本不够的 k 不出现）。
_PASS_K = (1, 10, 100)


def resolve_code_task(judger: Dict[str, Any]) -> str:
    """bench 的 ``format_type`` → 判分侧的数据集名（evalplus 的或 LiveCodeBench）。"""
    raw = str(judger.get("format_type") or "").strip()
    task = FORMAT_TYPES.get(raw)
    if not task:
        raise ValueError(
            f"code bench 的 format_type 只支持 {list(FORMAT_TYPES)}，当前是 {raw!r}")
    return task


def _bench_paths(state: Dict[str, Any], writer=None) -> Tuple[Path, Path, str]:
    """定位 bench 产物目录、模型原始样本文件、bench 名。

    ``output_case_path`` 是 generate 步骤写回 state 的；只重判（state 里没有它）时
    按上次跑出来的文件名兜底。路径一律 resolve 成绝对路径：它要进 ``docker -v``，
    docker 只接受绝对路径。
    """
    judger = state.get("judger") or {}
    task_id = str(state.get("task_id") or "task")
    version_id = str(getattr(writer, "version_id", None) or state.get("version_id") or "run")
    bench_name = str(judger.get("bench_name") or resolve_code_task(judger))
    bench_dir = (Path(str(state.get("output_dir") or "./outputs")).expanduser().resolve()
                 / task_id / "judger" / version_id / bench_name)
    raw = str(judger.get("output_case_path") or "")
    sample_path = (Path(raw).expanduser().resolve() if raw
                   else bench_dir / f"{bench_name}_sample.jsonl")
    return bench_dir, sample_path, bench_name


def _ensure_image(writer=None, image: str = None,
                  env_var: str = "CODE_EVAL_IMAGE") -> None:
    """镜像不存在就 ``docker pull`` 一次；拉不动就 emit_error（而不是让 docker run 报一堆 125）。

    两种情况都走结构化报错：
    - 没有 docker 命令 → ``DEPENDENCY_ERROR``
    - ``docker pull`` 失败（无网 / 私有 registry / 镜像名写错） → ``EXTERNAL_SERVICE_ERROR``
    报错里直接给出可照抄的补救命令（手动 pull、``docker save`` + ``docker load`` 离线搬运、
    用镜像名环境变量指向本地已有镜像），省得再去翻文档。
    """
    image = image or CODE_EVAL_IMAGE
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except FileNotFoundError as exc:
        emit_error(
            exc, code=ErrorCode.DEPENDENCY_ERROR, recoverable=True, stream_writer=writer,
            message="code 判分要在宿主机上跑评测镜像，但当前环境没有 docker 命令；"
                    "请先安装并启动 docker，或换一台有 docker 的机器重跑。")
    if probe.returncode == 0:
        return
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current="judger", progress=0.0,
                           message=f"本地没有 {image}，正在拉取",
                           data={"image": image}))
    try:
        subprocess.run(["docker", "pull", image], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = ((exc.stderr or "") + (exc.stdout or "")).strip().splitlines()
        reason = " / ".join(detail[-3:]) if detail else "docker 没有给出原因"
        emit_error(
            exc, code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True,
            stream_writer=writer,
            message=f"拉取评测镜像失败：{image}（{reason}）。"
                    "补救办法任选其一："
                    f"1) 在能联网的机器上 `docker pull {image}`，再 "
                    f"`docker save {image} | gzip > eval_image.tgz` 拷到本机，"
                    "执行 `docker load -i eval_image.tgz`；"
                    f"2) 用环境变量 {env_var} 指向本机已有的等价镜像；"
                    "3) 换一台已有该镜像的机器重跑。")


def _ensure_livecodebench_image(writer=None) -> None:
    """本地没有 LCB 镜像就按仓库里的上下文构建（和 math 评测镜像同一套做法）。

    这份镜像是我们自己 fork 的（上游源码 + Judger 改动），没有官方镜像可拉，所以缺了
    就现场 build；pip 源和代理照抄宿主机的环境变量 —— docker 的 bridge 网络经常出不了
    网，用默认源装依赖会直接失败。
    """
    try:
        probe = subprocess.run(
            ["docker", "image", "inspect", LCB_EVAL_IMAGE],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except FileNotFoundError as exc:
        emit_error(
            exc, code=ErrorCode.DEPENDENCY_ERROR, recoverable=True, stream_writer=writer,
            message="LiveCodeBench 判分要在宿主机上跑评测镜像，但当前环境没有 docker 命令；"
                    "请先安装并启动 docker，或换一台有 docker 的机器重跑。")
    if probe.returncode == 0:
        return
    if not LCB_EVAL_CONTEXT.is_dir():
        emit_error(
            FileNotFoundError(f"LiveCodeBench docker context does not exist: {LCB_EVAL_CONTEXT}"),
            code=ErrorCode.DEPENDENCY_ERROR, recoverable=True, stream_writer=writer,
            message=f"找不到 LiveCodeBench 镜像上下文 {LCB_EVAL_CONTEXT}：要装完整仓库"
                    "（不是只拷几个 .py）。也可以手动 docker build 之后用 LCB_EVAL_IMAGE "
                    "指向已有的等价镜像。")
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current="judger", progress=0.0,
                           message="本地没有 LiveCodeBench 镜像，正在构建（首次要几分钟）",
                           data={"image": LCB_EVAL_IMAGE, "context": str(LCB_EVAL_CONTEXT)}))
    command = ["docker", "build", "--network", os.getenv("LCB_BUILD_NETWORK", "host"),
               "-t", LCB_EVAL_IMAGE]
    for name in ("PIP_INDEX_URL", "PIP_TRUSTED_HOST", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        value = os.getenv(name)
        if value:
            command += ["--build-arg", f"{name}={value}"]
    command.append(str(LCB_EVAL_CONTEXT))
    try:
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        emit_error(
            exc, code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True, stream_writer=writer,
            message=f"构建 LiveCodeBench 镜像失败（{LCB_EVAL_IMAGE}）；docker 输出见上方。"
                    "常见原因：构建时装依赖出不了网（用 PIP_INDEX_URL 指内网源）、"
                    "docker 存储或权限不足。")


def _evalplus_result_path(samples: Path) -> Path:
    """evalplus 自己决定结果文件名：旧版 ``*_eval_results.json``，新版 ``*.eval_results.json``。"""
    legacy = samples.with_name(samples.name.replace(".jsonl", "_eval_results.json"))
    modern = samples.with_name(samples.name.replace(".jsonl", ".eval_results.json"))
    return legacy if legacy.is_file() else modern


def _pass_at_k(rows: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, float]]:
    """从 evalplus 的逐题结果算 base / plus 两个口径的 pass@k（版本无关）。

    plus 口径 = base 用例和 plus 扩展用例**都过**才算这道样本过，和 evalplus 官方一致。
    """
    import math

    total = [len(items) for items in rows.values()]
    if not total:
        return {}
    base_correct = [sum(1 for item in items if item.get("base_status") == "pass")
                    for items in rows.values()]
    plus_correct = [sum(1 for item in items
                         if item.get("base_status") == "pass" and item.get("plus_status") == "pass")
                    for items in rows.values()]

    def estimate(num_samples: int, num_correct: int, k: int) -> float:
        if num_samples - num_correct < k:
            return 1.0
        return 1.0 - math.prod(
            1.0 - k / denom for denom in range(num_samples - num_correct + 1, num_samples + 1))

    out: Dict[str, Dict[str, float]] = {"base": {}, "plus": {}}
    for k in _PASS_K:
        if min(total) >= k:
            out["base"][f"pass@{k}"] = sum(
                estimate(n, c, k) for n, c in zip(total, base_correct)) / len(total)
        if all(n >= k for n in total):
            out["plus"][f"pass@{k}"] = sum(
                estimate(n, c, k) for n, c in zip(total, plus_correct)) / len(total)
    return out


# 各数据集必需的字段。``test`` / ``assertion`` 不参与判分，但它们是"这确实是官方
# 那一份数据集"的标记 —— 原始 HumanEval / sanitized-mbpp 都缺 base_input/plus_input/atol，
# 只看前 7 个字段不足以区分。注意判分必须覆盖镜像数据集里的**全部**题目，
# 所以行数也要对得上（镜像里 HumanEval+ 164 题、MBPP+ 378 题）。
_PROBLEM_FIELDS = {
    "humaneval": ("task_id", "prompt", "entry_point", "canonical_solution",
                  "base_input", "plus_input", "atol", "test"),
    "mbpp": ("task_id", "prompt", "entry_point", "canonical_solution",
             "base_input", "plus_input", "atol", "assertion"),
    # LiveCodeBench 的字段按 scenario 分（codegeneration / selfrepair 一份数据集，
    # testoutputprediction 另一份），见下面 LCB_PROBLEM_FIELDS。
}
_TASK_ID_PREFIX = {"humaneval": "HumanEval/", "mbpp": "Mbpp/"}
_EXPECTED_ROWS = {"humaneval": 164, "mbpp": 378}

_DATASET_LABEL = {"humaneval": "HumanEval+", "mbpp": "MBPP+",
                  LIVE_CODEBENCH: "LiveCodeBench"}
_DATASET_MODULE = {"humaneval": "humaneval", "mbpp": "mbpp"}
_DATASET_READY_FUNC = {
    "humaneval": "_ready_human_eval_plus_path",
    "mbpp": "_ready_mbpp_plus_path",
}


def dataset_label(task: str) -> str:
    """人话里的数据集名（报错信息用）。"""
    return _DATASET_LABEL.get(task, task)


def dataset_dump_hint(task: str, target: str = "", lcb_scenario: str = "") -> str:
    """导出这份数据集的命令（报错信息里直接给出，省得再去翻文档）。

    不能拿 ``get_*_plus()`` + ``write_jsonl``：这两个 getter 会把输入反序列化成
    ``complex`` / ``tuple`` / ``set``，而 evalplus 的 ``write_jsonl`` 是裸
    ``json.dumps``，碰到 MBPP+ 的 Mbpp/124、Mbpp/252 直接 TypeError。所以直接复制
    它缓存的原始 jsonl —— 那本来就是官方数据集原文，字段齐全、无二次序列化损耗。
    """
    out = target or f"data/evalplus/{task}_plus.jsonl"
    if task == LIVE_CODEBENCH:
        scenario = str(lcb_scenario or "").strip() or LCB_DEFAULT_SCENARIO
        if scenario == "testoutputprediction":
            # test output prediction 没有按 release 版本切分的原始 jsonl，从 Hub 导一份
            return ("python -c \"from datasets import load_dataset; "
                    "load_dataset('livecodebench/test_generation', split='test')"
                    f".to_json('{out}', orient='records', lines=True)\"")
        if scenario == "codeexecution":
            # 代码执行用的是另一份数据集（上游代码里写死的 execution-v2，不是老的
            # livecodebench/execution —— 那份缺 contest_date 等字段，加载会报错）
            return ("python -c \"from datasets import load_dataset; "
                    "load_dataset('livecodebench/execution-v2', split='test')"
                    f".to_json('{out}', orient='records', lines=True)\"")
        # codegeneration / selfrepair 用同一份：上游把每个 release 版本放成 testN.jsonl
        # （test.jsonl=release_v1 … test6.jsonl=release_v6），判分需要带
        # private_test_cases 的那一份，不能只下题目描述。
        return (f"curl -L -o {out} "
                "https://huggingface.co/datasets/livecodebench/code_generation_lite/"
                "resolve/main/test.jsonl")
    return (f"python -c \"import shutil; from evalplus.data.{_DATASET_MODULE[task]} import "
            f"{_DATASET_READY_FUNC[task]}; shutil.copy({_DATASET_READY_FUNC[task]}(), '{out}')\"")


def _check_livecodebench_problem_file(path: Path, required: Tuple[str, ...],
                                      sample_rows: int = 3) -> List[str]:
    """LiveCodeBench 题目文件的校验。

    这份文件很大（含 base64 压缩的私有用例，release_v1 就有 1.2GB），所以只解析头部
    几行做字段检查、用纯文本计数统计题数 —— 否则 validate 阶段要把整份文件 JSON 解析
    一遍，白等几分钟。题数不设固定值：它随 release 版本变化（400/511/612/713/880/1055）。
    """
    missing: Dict[str, int] = {}
    rows = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            if rows > sample_rows:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                return [f"第 {rows} 行 JSON 解析失败：{exc}"]
            for field in required:
                if field not in row:
                    missing[field] = missing.get(field, 0) + 1

    problems: List[str] = []
    if missing:
        detail = "、".join(f"{field}（缺 {count} 行）" for field, count in missing.items())
        problems.append(f"缺少 {dataset_label(LIVE_CODEBENCH)} 必需字段：{detail}")
    if rows == 0:
        problems.append("题目文件是空的")
    return problems


def check_problem_file(problem_path: str, task: str,
                       lcb_scenario: str = "") -> List[str]:
    """检查题目文件是不是判分侧认的 ``task`` 数据集格式；返回问题列表（空 = 通过）。

    只做"这份数据能不能判分"的检查：必需字段、task_id 前缀、题目数量。分开写是因为
    HumanEval+ 和 MBPP+ 的字段不完全一样（``test`` vs ``assertion``），LCB 的三个
    scenario 也不一样，混用一套字段表就会给出误导性的报错。
    """
    if task not in _PROBLEM_FIELDS and task != LIVE_CODEBENCH:
        raise ValueError(f"未知的 code 评测任务 {task!r}，可选 {list(CODE_TASKS)}")
    path = Path(problem_path)
    if not path.is_file():
        return [f"文件不存在：{path}"]

    if task == LIVE_CODEBENCH:
        # LCB 没有 task_id 前缀，题数也随 release 版本变，只能查字段
        scenario = str(lcb_scenario or "").strip() or LCB_DEFAULT_SCENARIO
        if scenario not in LCB_PROBLEM_FIELDS:
            raise ValueError(
                f"LiveCodeBench 的 scenario 只支持 {list(LCB_SCENARIOS)}，"
                f"当前是 {scenario!r}")
        return _check_livecodebench_problem_file(path, LCB_PROBLEM_FIELDS[scenario])

    required = _PROBLEM_FIELDS[task]

    prefix = _TASK_ID_PREFIX[task]
    expected_rows = _EXPECTED_ROWS[task]

    missing: Dict[str, int] = {}
    bad_ids: List[str] = []
    rows = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                return [f"第 {rows} 行 JSON 解析失败：{exc}"]
            for field in required:
                if field not in row:
                    missing[field] = missing.get(field, 0) + 1
            task_id = str(row.get("task_id", ""))
            if not task_id.startswith(prefix) and len(bad_ids) < 3:
                bad_ids.append(task_id or "<空>")

    problems: List[str] = []
    if missing:
        detail = "、".join(f"{field}（缺 {count} 行）" for field, count in missing.items())
        problems.append(f"缺少 {dataset_label(task)} 必需字段：{detail}")
    if bad_ids:
        problems.append(
            f"task_id 不是 {dataset_label(task)} 的命名（应以 {prefix} 开头），例如 {bad_ids}")
    if rows != expected_rows:
        problems.append(f"题目数量是 {rows}，{dataset_label(task)} 官方是 {expected_rows} 题"
                        "（判分要求覆盖全部题目）")
    return problems


def _sanitized_name(samples: Path) -> str:
    """``evalplus.sanitize`` 的输出名：把 ``.jsonl`` 换成 ``-sanitized.jsonl``。"""
    return samples.name.replace(".jsonl", "-sanitized.jsonl")


def _result_base(sanitized: Path) -> Path:
    return sanitized.with_name(sanitized.name.replace(".jsonl", ""))


def _build_command(bench_dir: Path, samples: Path, task: str) -> List[str]:
    """官方镜像 + 官方 CLI：先 ``evalplus.sanitize`` 抽取，再 ``evalplus.evaluate`` 判分。

    整个 bench 目录挂成 /work：样本要在容器里可读，两个 CLI 写出来的中间产物和结果
    （``*-sanitized.jsonl``、``*_eval_results.json``）也都落回宿主机。
    """
    script = (
        f"evalplus.sanitize --samples /work/{samples.name} && "
        f"evalplus.evaluate --dataset {task} --samples /work/{_sanitized_name(samples)}"
    )
    return [
        "docker", "run", "--rm",
        "--network", DEFAULT_NETWORK,
        "-v", f"{bench_dir}:/work",
        CODE_EVAL_IMAGE,
        # 用 sh -c（不是 login shell）：PATH 直接继承镜像环境，避免 /etc/profile 覆盖
        "sh", "-c", script,
    ]


def run_evaluate_code(state: Dict[str, Any], writer=None) -> Dict[str, Any]:
    """跑一次 evalplus 容器，返回 ``{result_path, summary_path, metrics, summary}``。

    metrics 全是百分数（和 math 分支口径一致）：``pass@1`` 取 plus 口径，同时给
    ``base_pass@1`` / ``plus_pass@1`` 两个明细值。
    """
    judger = state.get("judger") or {}
    bench_dir, samples, bench_name = _bench_paths(state, writer)
    if not samples.is_file():
        raise FileNotFoundError(
            f"Code sample file does not exist: {samples}"
            "（generate 的产物；只重判时 state 里要有 output_case_path）")

    task = resolve_code_task(judger)
    result_path = bench_dir / f"{bench_name}_result.jsonl"
    summary_path = bench_dir / f"{bench_name}_summary.json"
    sanitized = bench_dir / _sanitized_name(samples)
    bench_dir.mkdir(parents=True, exist_ok=True)
    # 上一次的 summary 先删掉：否则容器失败时会把旧分数当成这次的结果报出去。
    summary_path.unlink(missing_ok=True)
    # evalplus 的结果文件也必须先删：它发现文件存在就「加载旧结果」，不会重算
    # （见 evalplus/evaluate.py 开头的 `if os.path.isfile(result_path) ...`）。
    # 上次的 sanitized 也一并删掉，避免这次 sanitize 失败时被当成这次的输入。
    sanitized.unlink(missing_ok=True)
    for stale in (sanitized.with_name(sanitized.name.replace(".jsonl", "_eval_results.json")),
                  sanitized.with_name(sanitized.name.replace(".jsonl", ".eval_results.json"))):
        stale.unlink(missing_ok=True)

    _ensure_image(writer)
    command = _build_command(bench_dir, samples, task)
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current=state.get("current", "judger"), progress=0.0,
                           message=f"正在评测 code 样本（evalplus sanitize + evaluate {task}）",
                           data={"command": command, "image": CODE_EVAL_IMAGE}))
    try:
        # 容器的 stdout/stderr 直接透传（判分动辄几分钟，日志要能实时看到），
        # 失败时只补一层结构化报错，不再重复打日志。
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        emit_error(
            exc, code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True, stream_writer=writer,
            message=f"evalplus 容器判分失败（退出码 {exc.returncode}，镜像 {CODE_EVAL_IMAGE}）；"
                    "容器日志见上方输出。常见原因：样本的 task_id 不在镜像数据集里、"
                    "题目没覆盖全（evalplus 要求每题都有样本）、或镜像里的数据集版本与"
                    "problem_path 不一致。")

    raw_result = _evalplus_result_path(sanitized)
    if not raw_result.is_file():
        emit_error(
            FileNotFoundError(f"evalplus result file not found: {raw_result}"),
            code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True, stream_writer=writer,
            message=f"evalplus 没有产出结果文件（找过 {raw_result}）。"
                    "常见原因：样本的 task_id 不在镜像数据集里，或题目没覆盖全"
                    "（evalplus 要求每题都有样本）。")
    payload = json.loads(raw_result.read_text(encoding="utf-8"))
    rows = payload.get("eval") or {}
    pass_at_k = payload.get("pass_at_k") or _pass_at_k(rows)

    # 逐题明细摊平成 jsonl：sanitize 只留了 solution，这里把判定结果补回去
    plus_pass_samples = base_pass_samples = 0
    failed_tasks = 0
    with open(result_path, "w", encoding="utf-8") as handle:
        for task_id, items in rows.items():
            task_ok = False
            for completion_id, item in enumerate(items):
                base_ok = item.get("base_status") == "pass"
                plus_ok = item.get("plus_status") == "pass"
                base_pass_samples += int(base_ok)
                plus_pass_samples += int(plus_ok)
                task_ok = task_ok or plus_ok
                handle.write(json.dumps({
                    "task_id": task_id,
                    "completion_id": completion_id,
                    "solution": item.get("solution"),
                    "base_status": item.get("base_status"),
                    "plus_status": item.get("plus_status"),
                    "base_fail_tests": item.get("base_fail_tests"),
                    "plus_fail_tests": item.get("plus_fail_tests"),
                }, ensure_ascii=False) + "\n")
            failed_tasks += int(not task_ok)

    pass_source = "plus" if pass_at_k.get("plus") else "base"
    lead = pass_at_k.get(pass_source) or {}
    samples_total = sum(len(items) for items in rows.values())
    summary = {
        "task": task,
        "image": CODE_EVAL_IMAGE,
        "dataset_hash": payload.get("hash"),
        "problems": len(rows),
        "samples": samples_total,
        "pass_source": pass_source,
        "pass@1": lead.get("pass@1"),
        "pass_at_k": pass_at_k,
        "base_pass_samples": base_pass_samples,
        "plus_pass_samples": plus_pass_samples,
        "failed_task_count": failed_tasks,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    metrics: Dict[str, Any] = {}
    for source, values in pass_at_k.items():
        for key, value in values.items():
            metrics[f"{source}_{key}"] = round(float(value) * 100, 2)
    if lead.get("pass@1") is not None:
        metrics["pass@1"] = round(float(lead["pass@1"]) * 100, 2)
    metrics["passed"] = plus_pass_samples
    metrics["samples"] = samples_total
    metrics["failed_task_count"] = failed_tasks

    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current=state.get("current", "judger"), progress=1.0,
                           message=f"code 评测完成（evalplus {task}，pass@1={metrics.get('pass@1')}）",
                           data={"result_path": str(result_path), "summary_path": str(summary_path),
                                 "metrics": metrics, "task": task,
                                 "raw_result_path": str(raw_result)}))
    return {"result_path": str(result_path), "summary_path": str(summary_path),
            "raw_result_path": str(raw_result), "metrics": metrics, "summary": summary}


# ---------------------------------------------------------------------------
# LiveCodeBench 分支：生成 + 判分都在容器里，产物回宿主机转成 Judger 契约
# ---------------------------------------------------------------------------

# LCB 用 ``str(scenario)`` 拼产物文件名，所以前缀是 ``Scenario.<scenario>``
# 而不是 ``<scenario>``；后面跟 ``_<n>_<temperature>``。
LCB_SCENARIO_PREFIX = "Scenario"

# 已接的四个 scenario（LCB 自己的 CLI 用 --scenario 选）。
LCB_SCENARIOS = ("codegeneration", "selfrepair", "testoutputprediction", "codeexecution")
LCB_DEFAULT_SCENARIO = "codegeneration"

# selfrepair 不重新生成代码，而是拿 codegen 的结果去修：LCB 从
# ``output/<model_repr>/Scenario.codegeneration_<codegen_n>_<temperature>_eval_all.json``
# 读待修的代码和判定（见 base_runner.run_main_repair），所以得先在同一个 bench 目录里
# 跑一遍 codegeneration。
LCB_SCENARIO_PREREQUISITES = {"selfrepair": "codegeneration"}

# 每个 scenario 的题目字段。codegeneration / selfrepair 用同一份代码生成数据集；
# testoutputprediction 和 codeexecution 各有一份自己的（后者是「给一段代码和输入，
# 让模型预测输出」）。
_LCB_CODEGEN_FIELDS = ("question_id", "question_content", "platform", "contest_date",
                       "difficulty", "starter_code", "public_test_cases",
                       "private_test_cases", "metadata")
LCB_PROBLEM_FIELDS = {
    "codegeneration": _LCB_CODEGEN_FIELDS,
    "selfrepair": _LCB_CODEGEN_FIELDS,
    "testoutputprediction": ("question_id", "question_title", "question_content",
                             "contest_id", "contest_date", "difficulty", "test",
                             "starter_code", "function_name", "test_id"),
    "codeexecution": ("question_id", "id", "contest_id", "contest_date", "difficulty",
                      "function_name", "code", "input", "output", "numsteps",
                      "problem_id"),
}

# 产物里装「抽出来的代码」的字段名，以及 task_id 的拼法。一题多份样本的两个 scenario
# 都得加后缀才不撞车：testoutputprediction 一题有多个待预测的测试（test_id），
# codeexecution 一题有多个输入（id，479 行里只有 92 个不同 question_id）。
LCB_SOLUTION_FIELD = {"codegeneration": "code_list", "selfrepair": "code_list",
                      "testoutputprediction": "pred_list", "codeexecution": "pred_list"}
LCB_TASK_ID_EXTRA = {"testoutputprediction": "test_id", "codeexecution": "id"}

# 上游 LCB 的 pass@1 尺度不统一：codegeneration / testoutputprediction 返回 0-1 比例，
# 而 codeexecution（evaluation/compute_code_execution_metrics.py）返回前就已经乘过 100。
# 落到 Judger 的百分数口径时要按 scenario 分开算，否则 codeexecution 会被再放大 100 倍
# （实测出现过 pass@1 = 4091.86，真实值是 40.92）。
LCB_PERCENT_METRIC_SCENARIOS = ("codeexecution",)


def lcb_scenario(judger: Dict[str, Any]) -> str:
    """bench 的 ``lcb_scenario``（可选）→ LCB scenario，默认 codegeneration。"""
    raw = str(judger.get("lcb_scenario") or "").strip() or LCB_DEFAULT_SCENARIO
    if raw not in LCB_SCENARIOS:
        raise ValueError(
            f"LiveCodeBench 的 scenario 只支持 {list(LCB_SCENARIOS)}，当前是 {raw!r}")
    return raw


def _lcb_model_name(judger: Dict[str, Any]) -> str:
    """vLLM 上架的名字 —— 也是 LCB 的 model_repr 和产物子目录名。"""
    return str(judger.get("eval_model_name")
               or Path(str(judger.get("eval_model_path") or "")).name)


def _lcb_sampling_args(judger: Dict[str, Any], scenario: str) -> Dict[str, str]:
    """采样参数 → LCB 命令行。默认值和 LCB 自己的 argparse 默认值对齐。

    selfrepair 的 ``--n`` 必须是 1（LCB 里是 assert）：它的「n」是被修的那批代码的
    数量，由 ``--codegen_n`` 指定，正好是这边 bench 的 case_num。
    """
    temperature = judger.get("eval_temperature")
    top_p = judger.get("eval_top_p")
    args = {
        "n": str(judger.get("eval_case_num") or 1),
        "temperature": str(0.0 if temperature is None else temperature),
        "top_p": str(1.0 if top_p is None else top_p),
        "max_tokens": str(judger.get("eval_max_tokens") or 2000),
    }
    if scenario == "selfrepair":
        args["codegen_n"] = args["n"]
        args["n"] = "1"
    return args


def _build_livecodebench_command(bench_dir: Path, problem_path: Path,
                                 judger: Dict[str, Any], image: str,
                                 scenario: str) -> List[str]:
    """LiveCodeBench 容器命令（一个 scenario 一次 docker run）。

    容器里跑的是 LCB 自己的 CLI，它通过 ``--vllm_base_url`` 回调 Judger 起的 vLLM
    生成，再用 LCB 自带的用例判分，所以不能用 ``--network none``（本机 daemon 是
    ``bridge: none``，只能用 host）。

    bench 目录挂成 ``/app/output``：LCB 把生成和评分结果都写在这个相对路径下
    （``<model_repr>/Scenario.<scenario>_<n>_<temperature>*_eval*.json``），挂回去之后
    宿主机就能像 evalplus 分支那样读它的产物、转成 Judger 的契约文件；selfrepair 也正是
    靠这个把上一轮 codegen 的结果交给容器。
    题目文件带私有用例、体积在 GB 级，按只读挂进容器，不打进镜像。
    """
    base_url = str(judger.get("eval_base_url") or "")
    if not base_url:
        raise ValueError(
            "LiveCodeBench 判分需要 Judger 已启动的 vLLM（judger.eval_base_url 为空）")
    sample = _lcb_sampling_args(judger, scenario)
    command = [
        "docker", "run", "--rm",
        "--network", os.getenv("LCB_NETWORK", "host"),
        "-v", f"{bench_dir}:/app/output",
        "-v", f"{problem_path}:/data/livecodebench.jsonl:ro",
        image,
        "--model", _lcb_model_name(judger),
        "--vllm_base_url", base_url,
        "--local_dataset_path", "/data/livecodebench.jsonl",
        "--scenario", scenario,
        "--n", sample["n"],
        "--temperature", sample["temperature"],
        "--top_p", sample["top_p"],
        "--max_tokens", sample["max_tokens"],
    ]
    if "codegen_n" in sample:
        command += ["--codegen_n", sample["codegen_n"]]
    # 思考模型（Qwen3 这类）默认先写思考链：max_tokens 一被思考吃光，样本里就一行代码
    # 都没有，pass@1 直接变成噪声。judger 的 eval_enable_thinking 就是这个开关；
    # 没配时不下发，让被服务模型用自己的默认模板。
    if judger.get("eval_enable_thinking") is not None:
        command += ["--enable_thinking",
                    "true" if judger["eval_enable_thinking"] else "false"]
    command.append("--evaluate")
    return command


def _run_lcb_container(bench_dir: Path, problem_path: Path, judger: Dict[str, Any],
                       image: str, scenario: str, writer) -> None:
    """跑一次 LCB 容器；失败时补一层结构化报错（容器日志照旧实时透传）。"""
    command = _build_livecodebench_command(
        bench_dir, problem_path, judger, image, scenario)
    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current=judger.get("current") or "judger", progress=0.0,
                           message=f"正在评测 LiveCodeBench 样本 [scenario={scenario}]"
                                   "（容器内生成 + 判分）",
                           data={"command": command, "image": image, "scenario": scenario}))
    try:
        # 容器日志实时透传（生成 + 判分要几分钟到几十分钟），失败时只补一层报错。
        subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        emit_error(
            exc, code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True, stream_writer=writer,
            message=f"LiveCodeBench 容器评测失败（scenario={scenario}，退出码 {exc.returncode}，"
                    f"镜像 {image}）；容器日志见上方输出。常见原因：容器访问不到 vLLM"
                    "（eval_base_url / LCB_NETWORK）、题目文件字段不对、--model 不是 vLLM 上架"
                    "的名字、或 selfrepair 缺少上一轮 codegeneration 的产物。")


def _lcb_native_outputs(bench_dir: Path, judger: Dict[str, Any],
                        scenario: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读回容器写出的 LCB 原生产物：逐题 ``*_eval_all.json`` + 汇总 ``*_eval.json``。

    只认当前 scenario 的前缀：selfrepair 的目录里还躺着一份 codegeneration 的产物
    （它的输入），不能被当成结果读走。

    ``eval_all`` 每题一条（``output_list`` 原始输出 / 抽取后的代码 / ``graded_list``
    逐样本判定），``eval`` 是 ``[metrics, results, ...]``，metrics 里是 pass@1、pass@5。
    """
    native_dir = bench_dir / _lcb_model_name(judger).split("/")[-1]
    pattern = f"{LCB_SCENARIO_PREFIX}.{scenario}_*_eval_all.json"
    candidates = sorted(native_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f"LiveCodeBench 没有在 {native_dir} 下写出评测结果（找过 {pattern}）")
    eval_all_path = candidates[-1]
    eval_path = eval_all_path.with_name(
        eval_all_path.name.replace("_eval_all.json", "_eval.json"))
    instances = json.loads(eval_all_path.read_text(encoding="utf-8"))
    if not isinstance(instances, list):
        raise ValueError(f"LiveCodeBench 评测结果格式不对：{eval_all_path} 不是 JSON 数组")
    metrics: Any = {}
    if eval_path.is_file():
        raw = json.loads(eval_path.read_text(encoding="utf-8"))
        metrics = (raw[0] if raw else {}) if isinstance(raw, list) else raw
    return instances, metrics


def _lcb_pass_at_k(raw_metrics: Dict[str, Any], scenario: str) -> Dict[str, float]:
    """LCB 产物的 ``pass@*`` → Judger 口径（百分数，两位小数）。

    ``codeexecution`` 的上游指标已经是百分数（见 ``LCB_PERCENT_METRIC_SCENARIOS``），
    其余 scenario 是 0-1 比例，所以倍数得按 scenario 定。
    """
    scale = 1.0 if scenario in LCB_PERCENT_METRIC_SCENARIOS else 100.0
    return {key: round(float(value) * scale, 2)
            for key, value in (raw_metrics or {}).items()
            if key.startswith("pass@") and isinstance(value, (int, float))}


def _write_livecodebench_artifacts(bench_dir: Path, bench_name: str,
                                   instances: List[Dict[str, Any]],
                                   raw_metrics: Dict[str, Any],
                                   image: str, scenario: str) -> Dict[str, Any]:
    """LCB 原生产物 → Judger 契约文件：sample / sanitized / result / summary。

    ``passed`` 是 Analyzer 判因直接读的字段（见 Analyzer 的 eval_model_node），
    evalplus 分支没写这一列，这里补上。
    """
    from loopai.skills.Judger.utils.data import write_jsonl

    solution_field = LCB_SOLUTION_FIELD[scenario]
    extra_task_field = LCB_TASK_ID_EXTRA.get(scenario)
    sample_rows: List[Dict[str, Any]] = []
    sanitized_rows: List[Dict[str, Any]] = []
    result_rows: List[Dict[str, Any]] = []
    passed_samples = 0
    failed_tasks = 0

    for instance in instances:
        question_id = str(instance.get("question_id") or "")
        extra = instance.get(extra_task_field) if extra_task_field else None
        task_id = f"{question_id}_{extra}" if extra is not None else question_id
        outputs = instance.get("output_list") or []
        solutions = instance.get(solution_field) or []
        graded = instance.get("graded_list") or []
        task_passed = False
        for completion_id, output in enumerate(outputs):
            code = solutions[completion_id] if completion_id < len(solutions) else ""
            passed = bool(graded[completion_id]) if completion_id < len(graded) else False
            passed_samples += int(passed)
            task_passed = task_passed or passed
            sample_rows.append({
                "task_id": task_id,
                "completion_id": completion_id,
                "completion": output,
            })
            sanitized_rows.append({
                "task_id": task_id,
                "completion_id": completion_id,
                "completion": output,
                "solution": code,
                "extract_method": f"lcb_runner.{scenario}.extract",
                "dropped_statements": 0,
            })
            result_rows.append({
                "task_id": task_id,
                "completion_id": completion_id,
                "completion": output,
                "solution": code,
                "passed": passed,
                "status": "pass" if passed else "fail",
            })
        failed_tasks += int(not task_passed)

    # 口径和 evalplus 分支一致：全百分数。LCB 只会给 pass@1（样本数 ≥5 时还有 pass@5）；
    # 上游各 scenario 的倍数不统一，交给 _lcb_pass_at_k 处理。
    pass_at_k = _lcb_pass_at_k(raw_metrics, scenario)

    sample_path = bench_dir / f"{bench_name}_sample.jsonl"
    sanitized_path = bench_dir / f"{bench_name}_sanitized.jsonl"
    result_path = bench_dir / f"{bench_name}_result.jsonl"
    summary_path = bench_dir / f"{bench_name}_summary.json"
    write_jsonl(str(sample_path), sample_rows)
    write_jsonl(str(sanitized_path), sanitized_rows)
    write_jsonl(str(result_path), result_rows)

    summary = {
        "bench": bench_name,
        "task": LIVE_CODEBENCH,
        "scenario": f"{LCB_SCENARIO_PREFIX}.{scenario}",
        "image": image,
        "problems": len(instances),
        "samples": len(result_rows),
        "pass_source": "lcb",
        "pass@1": pass_at_k.get("pass@1"),
        "pass_at_k": pass_at_k,
        "passed_samples": passed_samples,
        "failed_task_count": failed_tasks,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return {"sample_path": str(sample_path), "sanitized_path": str(sanitized_path),
            "result_path": str(result_path), "summary_path": str(summary_path),
            "metrics": pass_at_k, "summary": summary}


def run_evaluate_livecodebench(state: Dict[str, Any], writer=None) -> Dict[str, Any]:
    """跑 LiveCodeBench 容器（容器内生成 + 判分），转出 Judger 的产物。

    ``scenario`` 来自 ``judger["lcb_scenario"]``（默认 codegeneration）。selfrepair 会
    先跑一遍 codegeneration 当输入 —— 两次 docker run 共用同一个 bench 目录，所以 LCB
    能在容器的 ``/app/output`` 里读到上一轮的结果。

    metrics 全是百分数，和 evalplus 分支口径一致。题数不设固定值：它随 release
    版本变化（codegeneration v1=400 … v6=1055，testoutputprediction 442）。
    """
    import shutil

    judger = state.get("judger") or {}
    bench_dir, _sample_hint, bench_name = _bench_paths(state, writer)
    problem_path = Path(str(judger.get("eval_problem_path") or "")).expanduser().resolve()
    if not problem_path.is_file():
        raise FileNotFoundError(
            f"LiveCodeBench problem file does not exist: {problem_path}")

    scenario = lcb_scenario(judger)
    bench_dir.mkdir(parents=True, exist_ok=True)
    # 上一次的产物先清掉：容器失败时不能把旧分数当成这次的结果报出去（和 evalplus
    # 分支删 summary 同一个理由）。LCB 的产物目录整个归这次运行。
    (bench_dir / f"{bench_name}_summary.json").unlink(missing_ok=True)
    shutil.rmtree(bench_dir / _lcb_model_name(judger).split("/")[-1], ignore_errors=True)

    _ensure_livecodebench_image(writer)

    prerequisite = LCB_SCENARIO_PREREQUISITES.get(scenario)
    if prerequisite:
        # selfrepair 的输入：先把 codegen 跑出来（同一份题目、同一个 temperature，
        # 文件名才对得上 LCB 的 output/<model_repr>/Scenario.codegeneration_<n>_<t>_eval_all.json）
        _run_lcb_container(bench_dir, problem_path, judger, LCB_EVAL_IMAGE,
                           prerequisite, writer)
    _run_lcb_container(bench_dir, problem_path, judger, LCB_EVAL_IMAGE, scenario, writer)

    try:
        instances, raw_metrics = _lcb_native_outputs(bench_dir, judger, scenario)
    except (FileNotFoundError, ValueError) as exc:
        emit_error(
            exc, code=ErrorCode.EXTERNAL_SERVICE_ERROR, recoverable=True, stream_writer=writer,
            message=f"LiveCodeBench 容器跑完了但没有可读的评测结果：{exc}。"
                    "确认镜像就是 livecodebench（命令按 LCB CLI 拼的）且 --evaluate 有生效。")

    result = _write_livecodebench_artifacts(
        bench_dir, bench_name, instances, raw_metrics, LCB_EVAL_IMAGE, scenario)
    metrics = result["metrics"]
    from loopai.logger import get_logger
    get_logger().info(
        f"[Judger] livecodebench[{scenario}]: {len(instances)} 题 / "
        f"{result['summary']['samples']} 样本，pass@1={metrics.get('pass@1')}")

    if writer:
        from loopai.common.event_tool import StreamEvent
        writer(StreamEvent(current=state.get("current", "judger"), progress=1.0,
                           message=f"LiveCodeBench 评测完成（scenario={scenario}，"
                                   f"pass@1={metrics.get('pass@1')}）",
                           data={"result_path": result["result_path"],
                                 "summary_path": result["summary_path"],
                                 "metrics": metrics, "image": LCB_EVAL_IMAGE,
                                 "scenario": scenario}))
    return result
