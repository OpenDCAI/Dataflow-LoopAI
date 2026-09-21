#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""code 评测链路（evalplus 官方镜像）的测试。

分三层：

1. 纯函数：``resolve_code_task`` / ``_pass_at_k`` 这些不碰 docker 的口径；
2. docker 命令拼装：monkeypatch 掉 ``subprocess.run``，检查 ``docker run`` 的参数
   （官方镜像、强制离线、挂载绝对路径、sanitize + evaluate 两个官方 CLI）；
3. 结果解析：伪造 evalplus 的结果文件，检查 metrics / summary / 逐题明细。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from loopai.skills.Judger.utils import evaluate_code as ec  # noqa: E402


class _Writer:
    version_id = "v1"

    def __init__(self):
        self.events = []
        self.failed = None

    def __call__(self, event):
        self.events.append(event)

    def set_failed(self, payload):
        """emit_error 把结构化 payload 交回来，测试里用它断言报错信息。"""
        self.failed = payload


def _problems(count: int, task_id: str = "HumanEval/0") -> list:
    """造 ``count`` 条同一个 task 的 evalplus 结果条目（只用到 status 字段）。"""
    return [{"task_id": task_id, "solution": "def f():\n    return 1",
             "base_status": "pass", "plus_status": "pass",
             "base_fail_tests": [], "plus_fail_tests": []} for _ in range(count)]


def _bench(tmp_path: Path, name: str = "humaneval"):
    """建出 bench 目录 + 一份模型原始样本，返回 (bench_dir, samples)。"""
    bench_dir = tmp_path / "outputs" / "t1" / "judger" / "v1" / name
    bench_dir.mkdir(parents=True)
    samples = bench_dir / f"{name}_sample.jsonl"
    samples.write_text(
        json.dumps({"task_id": "HumanEval/0", "completion": "\n    return 1\n"}) + "\n",
        encoding="utf-8")
    return bench_dir, samples


def _state(tmp_path: Path, **judger) -> dict:
    return {"task_id": "t1", "output_dir": str(tmp_path / "outputs"), "version_id": "v1",
            "judger": {"bench_name": "humaneval", "eval_task_type": "code",
                       "format_type": "humaneval+", **judger}}


def _fake_docker(monkeypatch, result_payload=None, missing_result=False):
    """拦掉 subprocess.run；docker pull/inspect 返回成功，判分时把结果文件写出来。

    evalplus.evaluate 的结果文件名是从 **sanitize 产物** 派生的，所以这里照着
    真实的命名规则（``<sample>-sanitized.jsonl`` → ``*-sanitized.eval_results.json``）
    写回去，顺便验证我们把路径算对了。
    """
    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        if command[:2] == ["docker", "pull"]:
            class _Pull:
                returncode = 0
            return _Pull()
        if result_payload is not None and not missing_result:
            host_dir = Path(command[command.index("-v") + 1].split(":")[0])
            sample = next(host_dir.glob("*_sample.jsonl"))
            (host_dir / sample.name.replace(".jsonl", "-sanitized.jsonl")).write_text("",
                                                                                     encoding="utf-8")
            (host_dir / sample.name.replace(".jsonl", "-sanitized.eval_results.json")).write_text(
                json.dumps(result_payload), encoding="utf-8")

        class _Done:
            returncode = 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# 1. 纯函数
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("humaneval+", "humaneval"),
    ("mbpp+", "mbpp"),
    ("  mbpp+  ", "mbpp"),       # 首尾空格无所谓，其他一律不认
])
def test_resolve_code_task_accepts_only_the_two_format_types(raw, expected):
    assert ec.resolve_code_task({"format_type": raw}) == expected


@pytest.mark.parametrize("raw", [
    "humaneval", "human-eval", "HumanEval+", "humaneval_pro", "mbpp", "MBPP+", "mbppplus",
])
def test_resolve_code_task_rejects_legacy_names(raw):
    """不做别名：只支持 humaneval+ / mbpp+，历史写法一律报错（列表里列清楚）。"""
    with pytest.raises(ValueError) as excinfo:
        ec.resolve_code_task({"format_type": raw})

    assert "humaneval+" in str(excinfo.value) and "mbpp+" in str(excinfo.value)


def test_example_config_resolves_every_code_bench():
    """样例配置里每个 code bench 的 format_type 都要真能被识别（写错就是起完 vLLM 才发现）。

    配置是拿来改的工作文件（跑哪几个 bench、叫什么名字随时会动），所以这里只查
    「format_type → 后端」的映射，以及 LCB 的 bench 有没有把 ``lcb_scenario`` 写清楚。
    """
    config = json.loads(
        (Path(__file__).resolve().parents[1]
         / "examples" / "config" / "code_bench_gov.json").read_text(encoding="utf-8"))

    backend = {"humaneval+": "humaneval", "mbpp+": "mbpp",
               "livecodebench": "livecodebench"}
    benches = config["judger"]["benchlist"]
    assert benches, "样例配置至少要留一个 code bench"

    resolved = {bench["name"]: ec.resolve_code_task(bench) for bench in benches}
    assert resolved == {bench["name"]: backend[bench["format_type"]]
                        for bench in benches}

    # LCB 的 bench 必须显式配 lcb_scenario：漏了会全都落到默认的 codegeneration
    scenarios = {bench["name"]: bench.get("lcb_scenario")
                 for bench in benches if bench["format_type"] == "livecodebench"}
    assert all(scenarios.values()), f"这些 bench 没写 lcb_scenario：{scenarios}"
    assert set(scenarios.values()) <= set(ec.LCB_SCENARIOS)


def test_resolve_code_task_rejects_unknown_name():
    with pytest.raises(ValueError) as excinfo:
        ec.resolve_code_task({"format_type": "humaneval++"})

    assert "humaneval++" in str(excinfo.value)
    assert "humaneval" in str(excinfo.value)


def test_resolve_code_task_requires_something():
    with pytest.raises(ValueError) as excinfo:
        ec.resolve_code_task({})

    assert "format_type" in str(excinfo.value)


def test_pass_at_k_matches_evalplus_estimator():
    """164 题里 163 题过 → pass@1 = 163/164；样本数不够的 k 不出现。"""
    rows = {f"HumanEval/{i}": _problems(1, f"HumanEval/{i}") for i in range(164)}
    rows["HumanEval/0"] = [{**_problems(1)[0], "plus_status": "fail"}]

    result = ec._pass_at_k(rows)

    # base 用例全过；plus 口径里 HumanEval/0 那道不算过
    assert result["base"]["pass@1"] == pytest.approx(1.0)
    assert result["plus"]["pass@1"] == pytest.approx(163 / 164)
    assert set(result["base"]) == {"pass@1"}          # n=1，pass@10 不出现


def test_pass_at_k_needs_uniform_sample_count_for_plus():
    rows = {"a": _problems(1, "a"), "b": _problems(10, "b")}

    result = ec._pass_at_k(rows)

    assert "pass@10" not in result["plus"]
    assert result["plus"]["pass@1"] == pytest.approx(1.0)


def test_pass_at_k_empty():
    assert ec._pass_at_k({}) == {}


# ---------------------------------------------------------------------------
# 题目文件校验（按数据集分开，报错要点名缺哪些字段）
# ---------------------------------------------------------------------------

def _problem_rows(task: str, count: int, **overrides) -> list:
    """造 ``count`` 行 evalplus 数据集格式的题目（字段名按数据集区分）。"""
    if task == "humaneval":
        prefix, marker = "HumanEval/", {"test": "def check(candidate):\n    pass"}
    else:
        prefix, marker = "Mbpp/", {"assertion": "assert True"}
    rows = []
    for index in range(count):
        row = {"task_id": f"{prefix}{index}", "prompt": "def f():\n", "entry_point": "f",
               "canonical_solution": "    return 1\n",
               "base_input": [[]], "plus_input": [[]], "atol": 1e-6}
        row.update(marker)
        row.update(overrides)
        rows.append(row)
    return rows


def _write_problems(tmp_path: Path, name: str, rows: list) -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_check_problem_file_accepts_official_shape(tmp_path):
    path = _write_problems(tmp_path, "humaneval_plus.jsonl", _problem_rows("humaneval", 164))
    assert ec.check_problem_file(str(path), "humaneval") == []


def test_check_problem_file_accepts_mbpp_shape_with_assertion(tmp_path):
    path = _write_problems(tmp_path, "mbpp_plus.jsonl", _problem_rows("mbpp", 378))
    assert ec.check_problem_file(str(path), "mbpp") == []


def test_check_problem_file_names_missing_fields(tmp_path):
    """原始 HumanEval / sanitized-mbpp 缺字段时要直接点名，而不是笼统说"格式不对"。"""
    rows = _problem_rows("humaneval", 164)
    for row in rows:
        for field in ("base_input", "plus_input", "atol"):
            row.pop(field)
    path = _write_problems(tmp_path, "raw_humaneval.jsonl", rows)

    problems = ec.check_problem_file(str(path), "humaneval")

    assert any("HumanEval+" in problem for problem in problems)
    joined = " ".join(problems)
    for field in ("base_input", "plus_input", "atol"):
        assert field in joined


def test_check_problem_file_flags_wrong_dataset(tmp_path):
    """把 MBPP+ 挂到 humaneval bench 上：task_id 前缀和字段名都要报出来。"""
    path = _write_problems(tmp_path, "mbpp_plus.jsonl", _problem_rows("mbpp", 378))

    problems = ec.check_problem_file(str(path), "humaneval")

    joined = " ".join(problems)
    assert "HumanEval/" in joined          # task_id 前缀不对
    assert "test" in joined                # HumanEval+ 要 test、MBPP+ 给的是 assertion
    assert "assertion" not in joined.split("task_id")[0]  # 报的是缺 test，不是缺 assertion


def test_check_problem_file_flags_row_count(tmp_path):
    path = _write_problems(tmp_path, "partial.jsonl", _problem_rows("mbpp", 100))

    problems = ec.check_problem_file(str(path), "mbpp")

    assert len(problems) == 1
    assert "100" in problems[0] and "378" in problems[0]


def test_check_problem_file_missing_file(tmp_path):
    problems = ec.check_problem_file(str(tmp_path / "nope.jsonl"), "humaneval")

    assert len(problems) == 1 and "不存在" in problems[0]


def test_dataset_dump_hint_is_runnable(tmp_path):
    """提示里给的是「复制官方原始 jsonl」，不是 get_*_plus() + write_jsonl。

    后者对 MBPP+ 会 TypeError（反序列化出的 complex/set 过不了 json.dumps）。
    """
    hint = ec.dataset_dump_hint("mbpp", "data/evalplus/mbpp_plus.jsonl")

    assert "_ready_mbpp_plus_path" in hint and "shutil.copy" in hint
    assert "write_jsonl" not in hint
    assert "data/evalplus/mbpp_plus.jsonl" in hint


# ---------------------------------------------------------------------------
# 2. docker 命令拼装
# ---------------------------------------------------------------------------

def test_command_runs_official_clis_offline(tmp_path):
    bench_dir, samples = _bench(tmp_path)

    command = ec._build_command(bench_dir, samples, "humaneval")

    assert command[0] == "docker"
    assert ec.CODE_EVAL_IMAGE in command
    assert command[command.index("--network") + 1] == "none"
    host, _, container = command[command.index("-v") + 1].partition(":")
    assert Path(host).is_absolute()
    assert container == "/work"

    # 后处理 + 判分都走官方 CLI，容器里没有我们自己的代码
    script = command[-1]
    assert "evalplus.sanitize --samples /work/humaneval_sample.jsonl" in script
    assert "evalplus.evaluate --dataset humaneval" in script
    assert "--samples /work/humaneval_sample-sanitized.jsonl" in script


def test_command_infers_task_dataset_from_samples_name(tmp_path):
    bench_dir, samples = _bench(tmp_path, "mbpp")

    command = ec._build_command(bench_dir, samples, "mbpp")

    assert "evalplus.evaluate --dataset mbpp" in command[-1]


# ---------------------------------------------------------------------------
# 3. 结果解析
# ---------------------------------------------------------------------------

def test_evaluate_code_runs_container_and_parses_result(tmp_path, monkeypatch):
    bench_dir, samples = _bench(tmp_path)
    rows = _problems(4)
    rows[3] = {**rows[3], "plus_status": "fail"}
    _fake_docker(monkeypatch, {"hash": "abc", "eval": {"HumanEval/0": rows}})

    result = ec.run_evaluate_code(
        _state(tmp_path, output_case_path=str(samples)), _Writer())

    # metrics 是百分数口径：4 条样本 3 条过
    assert result["metrics"]["pass@1"] == pytest.approx(75.0)
    assert result["metrics"]["base_pass@1"] == pytest.approx(100.0)
    assert result["metrics"]["plus_pass@1"] == pytest.approx(75.0)
    # 一道题只要有任一样本过就不算失败（3/4 过 → 0）
    assert result["metrics"]["failed_task_count"] == 0
    assert result["summary"]["plus_pass_samples"] == 3

    detail = [json.loads(line) for line in
              Path(result["result_path"]).read_text(encoding="utf-8").splitlines()]
    assert len(detail) == 4
    assert [row["plus_status"] for row in detail] == ["pass", "pass", "pass", "fail"]


def test_evaluate_code_prefers_result_pass_at_k_when_present(tmp_path, monkeypatch):
    """新版 evalplus 会把 pass@k 写进结果文件，直接用它，别自己算。"""
    bench_dir, samples = _bench(tmp_path)
    _fake_docker(monkeypatch, {
        "hash": "abc", "eval": {"HumanEval/0": _problems(1)},
        "pass_at_k": {"base": {"pass@1": 0.5}, "plus": {"pass@1": 0.25}},
    })

    result = ec.run_evaluate_code(
        _state(tmp_path, output_case_path=str(samples)), _Writer())

    assert result["metrics"]["pass@1"] == pytest.approx(25.0)
    assert result["metrics"]["base_pass@1"] == pytest.approx(50.0)


def test_evaluate_code_clears_stale_artifacts_before_running(tmp_path, monkeypatch):
    """evalplus 发现结果文件已存在会「加载旧结果」而不重算，必须先删掉。"""
    bench_dir, samples = _bench(tmp_path)
    stale_sanitized = bench_dir / "humaneval_sample-sanitized.jsonl"
    stale_sanitized.write_text('{"task_id": "HumanEval/0", "solution": "old"}\n',
                               encoding="utf-8")
    stale_result = bench_dir / "humaneval_sample-sanitized.eval_results.json"
    stale_result.write_text(json.dumps({"eval": {"HumanEval/0": _problems(1)}}),
                            encoding="utf-8")
    stale_summary = bench_dir / "humaneval_summary.json"
    stale_summary.write_text('{"pass@1": 0.0}', encoding="utf-8")

    seen = {}

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        seen["stale_sanitized"] = stale_sanitized.is_file()
        seen["stale_result"] = stale_result.is_file()
        (bench_dir / "humaneval_sample-sanitized.eval_results.json").write_text(
            json.dumps({"eval": {"HumanEval/0": _problems(1)}}), encoding="utf-8")

        class _Done:
            returncode = 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), _Writer())

    assert seen["stale_sanitized"] is False    # 跑之前旧产物已经删了
    assert seen["stale_result"] is False
    assert json.loads(stale_summary.read_text())["pass@1"] != 0.0


def test_evaluate_code_missing_sample_file_says_what_to_do(tmp_path, monkeypatch):
    _fake_docker(monkeypatch)
    with pytest.raises(FileNotFoundError) as excinfo:
        ec.run_evaluate_code(_state(tmp_path), _Writer())

    assert "generate" in str(excinfo.value)


def test_evaluate_code_falls_back_to_legacy_result_name(tmp_path, monkeypatch):
    """老版本 evalplus 写的是 ``*_eval_results.json``（下划线）。"""
    bench_dir, samples = _bench(tmp_path)

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        (bench_dir / "humaneval_sample-sanitized_eval_results.json").write_text(
            json.dumps({"eval": {"HumanEval/0": _problems(1)}}), encoding="utf-8")

        class _Done:
            returncode = 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    result = ec.run_evaluate_code(
        _state(tmp_path, output_case_path=str(samples)), _Writer())

    assert result["metrics"]["pass@1"] == pytest.approx(100.0)


def test_evaluate_code_reports_missing_result_clearly(tmp_path, monkeypatch):
    bench_dir, samples = _bench(tmp_path)
    _fake_docker(monkeypatch, missing_result=True)
    writer = _Writer()

    with pytest.raises(SystemExit):
        ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), writer)

    assert writer.failed["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert "task_id" in writer.failed["message"]


def test_evaluate_code_pulls_image_then_reports_missing_result(tmp_path, monkeypatch):
    """镜像不在 → 先 pull；pull 成功但判分没结果 → 结构化报错（不能静默）。"""
    bench_dir, samples = _bench(tmp_path)
    calls = []
    writer = _Writer()

    def fake_run(command, **kwargs):
        calls.append(list(command))

        class _Done:
            returncode = 1 if command[:2] == ["docker", "image"] else 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), writer)

    assert ["docker", "pull", ec.CODE_EVAL_IMAGE] in calls
    assert any("正在拉取" in (event.message or "") for event in writer.events)


def test_evaluate_code_pull_failure_emits_error(tmp_path, monkeypatch):
    """镜像没有、pull 又失败 → emit_error（带 docker 报的原因和补救命令）。"""
    bench_dir, samples = _bench(tmp_path)
    writer = _Writer()

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 1
            return _Inspect()
        # 真 subprocess 在 check=True 时抛的是 CalledProcessError，这里如实模拟
        raise ec.subprocess.CalledProcessError(
            1, command,
            stderr="Error response from daemon: Get https://registry-1.docker.io: timeout")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), writer)

    assert writer.failed["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    message = writer.failed["message"]
    assert ec.CODE_EVAL_IMAGE in message
    assert "docker load" in message                 # 给了离线搬运的办法
    assert "timeout" in message                     # 带上 docker 的原因


def test_evaluate_code_without_docker_emits_dependency_error(tmp_path, monkeypatch):
    bench_dir, samples = _bench(tmp_path)
    writer = _Writer()

    def fake_run(command, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), writer)

    assert writer.failed["error"]["code"] == "DEPENDENCY_ERROR"
    assert "docker" in writer.failed["message"]


def test_evaluate_code_container_failure_emits_error(tmp_path, monkeypatch):
    """容器里 evalplus 非 0 退出 → emit_error，而不是裸 CalledProcessError。"""
    bench_dir, samples = _bench(tmp_path)
    writer = _Writer()

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        raise ec.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        ec.run_evaluate_code(_state(tmp_path, output_case_path=str(samples)), writer)

    assert writer.failed["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert "evalplus" in writer.failed["message"]


@pytest.mark.parametrize("task", ["humaneval", "mbpp"])
def test_dataset_dump_hint_command_produces_a_valid_dataset(tmp_path, task):
    """报错里给的导出命令必须真能用（MBPP+ 的反序列化输入不能过 json.dumps）。"""
    pytest.importorskip("evalplus")
    target = tmp_path / f"{task}_plus.jsonl"
    command = ec.dataset_dump_hint(task, str(target))
    # 命令里写的是 `python -c ...`，在测试里指到当前解释器（evalplus 装在它下面）
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}

    subprocess.run(["bash", "-c", command], check=True, env=env)

    assert ec.check_problem_file(str(target), task) == []


# ---------------------------------------------------------------------------
# 4. LiveCodeBench 分支（生成 + 判分都在容器里，产物回宿主机转契约）
# ---------------------------------------------------------------------------

def _lcb_problem_row(index: int, scenario: str = "codegeneration") -> dict:
    """一件 LCB 格式的题目：四个 scenario 的字段不一样。"""
    if scenario == "testoutputprediction":
        return {
            "question_id": str(2727 + index),
            "question_title": "number-of-senior-citizens",
            "question_content": "You are given a 0-indexed array of strings details...",
            "contest_id": "weekly-contest-345",
            "contest_date": "2023-05-13T00:00:00",
            "difficulty": "easy",
            "test": '[{"input": "[[\\"1\\"]]", "output": "1", "testtype": "functional"}]',
            "starter_code": "",
            "function_name": "solve",
            "test_id": 0,
        }
    if scenario == "codeexecution":
        return {
            "question_id": "2777",
            "id": f"sample_{index}",
            "contest_id": "weekly-contest-345",
            "contest_date": 1683417600000,          # 本地 jsonl 是 epoch 毫秒
            "difficulty": "easy",
            "function_name": "solve",
            "code": "def solve(x):\n    return x",
            "input": "1\n",
            "output": "1\n",
            "numsteps": 1,
            "problem_id": "2777",
        }
    return {
        "question_id": f"1873_{chr(ord('A') + index)}",
        "question_content": "There are three cards ...",
        "platform": "codeforces",
        "contest_date": "2023-08-21T00:00:00",
        "difficulty": "easy",
        "starter_code": "",
        "public_test_cases": '[{"input": "1\\n", "output": "YES", "testtype": "stdin"}]',
        "private_test_cases": "gASV...base64",
        "metadata": '{"func_name": null}',
    }


def _lcb_problem_rows(count: int, scenario: str = "codegeneration", **overrides) -> list:
    rows = []
    for index in range(count):
        row = _lcb_problem_row(index, scenario)
        row.update(overrides)
        rows.append(row)
    return rows


def _lcb_state(tmp_path: Path, problem_path: Path, scenario: str = "codegeneration",
               **judger) -> dict:
    return {"task_id": "t1", "output_dir": str(tmp_path / "outputs"), "version_id": "v1",
            "judger": {"bench_name": "livecodebench", "eval_task_type": "code",
                       "format_type": "livecodebench", "lcb_scenario": scenario,
                       "eval_model_name": "Qwen3-8B",
                       "eval_base_url": "http://localhost:8911/v1",
                       "eval_case_num": 1, "eval_temperature": 0.0,
                       "eval_top_p": 1.0, "eval_max_tokens": 2000,
                       "eval_problem_path": str(problem_path), **judger}}


def _fake_lcb_docker(monkeypatch, problems: int = 2, samples: int = 2,
                     write_eval_all: bool = True, image_present: bool = True):
    """拦掉 docker；每次「容器」运行都把该 scenario 的原生产物写回 bench 目录。

    ``image_present=False`` 模拟本地没有镜像：``docker image inspect`` 失败，
    ``_ensure_livecodebench_image`` 应该转去 ``docker build``（也记进 runs）。
    """
    runs: list = []

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0 if image_present else 1
            return _Inspect()
        if command[:2] == ["docker", "build"]:
            runs.append(list(command))

            class _Built:
                returncode = 0
            return _Built()
        if command[:2] == ["docker", "pull"]:
            class _Pull:
                returncode = 0
            return _Pull()
        runs.append(list(command))
        scenario = command[command.index("--scenario") + 1]
        host_dir = Path(command[command.index("-v") + 1].split(":")[0])
        native_dir = host_dir / "Qwen3-8B"
        native_dir.mkdir(parents=True, exist_ok=True)
        if write_eval_all:
            field = "code_list" if scenario in ("codegeneration", "selfrepair") else "pred_list"
            instances = []
            for index in range(problems):
                instance = {
                    "question_id": f"1873_{chr(ord('A') + index)}",
                    "output_list": [f"print({index}) #{s}" for s in range(samples)],
                    field: [f"print({index}) #{s}" for s in range(samples)],
                    "graded_list": [s == 0 for s in range(samples)],
                }
                if scenario == "testoutputprediction":
                    instance["question_id"] = str(2727 + index)
                    instance["test_id"] = 0
                elif scenario == "codeexecution":
                    instance["question_id"] = "2777"
                    instance["id"] = f"sample_{index}"
                instances.append(instance)
            prefix = f"Scenario.{scenario}_1_0.0"
            (native_dir / f"{prefix}_eval_all.json").write_text(
                json.dumps(instances), encoding="utf-8")
            (native_dir / f"{prefix}_eval.json").write_text(
                json.dumps([{"pass@1": 1 / problems, "detail": {}}]), encoding="utf-8")

        class _Done:
            returncode = 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    return runs


def _lcb_command(tmp_path, judge_overrides=None, scenario="codegeneration"):
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir(exist_ok=True)
    problem = tmp_path / "test.jsonl"
    problem.write_text("", encoding="utf-8")
    judger = _lcb_state(tmp_path, problem, scenario, **(judge_overrides or {}))["judger"]
    return ec._build_livecodebench_command(
        bench_dir, problem, judger, ec.LCB_EVAL_IMAGE, scenario)


def test_livecodebench_problem_file_accepts_upstream_shape(tmp_path):
    path = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(3))

    assert ec.check_problem_file(str(path), "livecodebench") == []


def test_livecodebench_problem_file_fields_depend_on_scenario(tmp_path):
    """三个 scenario 的题目字段不一样，串用要当场报出来（而不是跑完才发现）。"""
    codegen = _write_problems(tmp_path, "codegen.jsonl", _lcb_problem_rows(2))
    prediction = _write_problems(
        tmp_path, "prediction.jsonl", _lcb_problem_rows(2, "testoutputprediction"))

    assert ec.check_problem_file(str(prediction), "livecodebench",
                                 "testoutputprediction") == []
    assert ec.check_problem_file(str(codegen), "livecodebench", "selfrepair") == []

    # codegen 的数据喂给 testoutputprediction：缺 test / test_id / function_name
    problems = ec.check_problem_file(str(codegen), "livecodebench", "testoutputprediction")
    assert problems and all(field in problems[0] for field in ("test", "test_id"))


def test_livecodebench_problem_file_names_missing_fields(tmp_path):
    rows = _lcb_problem_rows(2)
    for row in rows:
        row.pop("private_test_cases")

    problems = ec.check_problem_file(str(_write_problems(tmp_path, "t.jsonl", rows)),
                                     "livecodebench")

    assert len(problems) == 1
    assert "private_test_cases" in problems[0]
    assert "LiveCodeBench" in problems[0]


def test_livecodebench_problem_file_does_not_pin_row_count(tmp_path):
    """题数随 release 版本变（v1=400 … v6=1055），不能像 evalplus 那样卡死行数。"""
    rows = _lcb_problem_rows(7)
    assert ec.check_problem_file(str(_write_problems(tmp_path, "t.jsonl", rows)),
                                 "livecodebench") == []
    assert ec.check_problem_file(str(_write_problems(tmp_path, "e.jsonl", [])),
                                 "livecodebench") == ["题目文件是空的"]


def test_livecodebench_dataset_dump_hint_depends_on_scenario():
    """codegeneration / selfrepair 是 curl 上游 test.jsonl，testoutputprediction 从 Hub 导。"""
    codegen = ec.dataset_dump_hint("livecodebench", "data/lcb/test.jsonl")
    assert codegen.startswith("curl -L -o data/lcb/test.jsonl")
    assert "code_generation_lite" in codegen

    prediction = ec.dataset_dump_hint("livecodebench", "data/lcb/prediction.jsonl",
                                      lcb_scenario="testoutputprediction")
    assert "livecodebench/test_generation" in prediction
    assert "to_json" in prediction and "data/lcb/prediction.jsonl" in prediction


def test_livecodebench_scenario_defaults_to_codegeneration():
    assert ec.lcb_scenario({}) == "codegeneration"
    assert ec.lcb_scenario({"lcb_scenario": " selfrepair "}) == "selfrepair"


def test_livecodebench_rejects_unknown_scenario():
    """写错 scenario 要当场报错并列出支持的几个，而不是当没配、默默跑 codegeneration。"""
    with pytest.raises(ValueError) as excinfo:
        ec.lcb_scenario({"lcb_scenario": "codereview"})

    message = str(excinfo.value)
    for scenario in ec.LCB_SCENARIOS:
        assert scenario in message


def test_livecodebench_command_hands_the_vllm_over_to_the_container(tmp_path):
    command = _lcb_command(tmp_path)
    bench_dir = Path(command[command.index("-v") + 1].split(":")[0])

    assert command[0] == "docker" and ec.LCB_EVAL_IMAGE in command
    # vLLM 在宿主机上，容器要能连过去；本机 daemon 是 bridge: none，只能用 host
    assert command[command.index("--network") + 1] == "host"
    mounts = [command[i + 1] for i, arg in enumerate(command) if arg == "-v"]
    assert f"{bench_dir}:/app/output" in mounts          # LCB 的产物目录挂回宿主机
    assert any(mount.endswith(":/data/livecodebench.jsonl:ro") for mount in mounts)
    # 生成 + 判分都在容器里：走 LCB 自己的 CLI，不再有 evalplus 那两个命令
    assert command[command.index("--vllm_base_url") + 1] == "http://localhost:8911/v1"
    assert command[command.index("--scenario") + 1] == "codegeneration"
    assert command[command.index("--local_dataset_path") + 1] == "/data/livecodebench.jsonl"
    assert command[command.index("--model") + 1] == "Qwen3-8B"
    assert command[-1] == "--evaluate"
    assert not any("evalplus" in arg for arg in command)
    # 没配 eval_enable_thinking 就不下发，让被服务模型用自己的默认模板
    assert "--enable_thinking" not in command


def test_livecodebench_builds_the_image_when_missing(monkeypatch, tmp_path):
    """LCB 没有官方镜像可拉：本地缺镜像就按仓库里的上下文现场构建，pip 源要透传进去。"""
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirrors.example.com/pypi/simple")
    runs = _fake_lcb_docker(monkeypatch, problems=1, samples=1, image_present=False)
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(1))

    ec.run_evaluate_livecodebench(_lcb_state(tmp_path, problem), _Writer())

    builds = [run for run in runs if run[:2] == ["docker", "build"]]
    assert len(builds) == 1, "缺镜像时必须构建一次（没有官方镜像可拉）"
    build = builds[0]
    assert ec.LCB_EVAL_CONTEXT.is_dir()
    assert build[-1] == str(ec.LCB_EVAL_CONTEXT)
    assert build[build.index("-t") + 1] == ec.LCB_EVAL_IMAGE
    # docker 的 bridge 网络经常出不了网：pip 源和代理照抄宿主机的环境变量
    assert "PIP_INDEX_URL=https://mirrors.example.com/pypi/simple" in build


def test_livecodebench_command_selfrepair_repairs_existing_samples(tmp_path):
    """selfrepair 的 --n 必须是 1，被修的样本数走 --codegen_n（= bench 的 case_num）。"""
    command = _lcb_command(tmp_path, {"eval_case_num": 4}, scenario="selfrepair")

    assert command[command.index("--scenario") + 1] == "selfrepair"
    assert command[command.index("--n") + 1] == "1"
    assert command[command.index("--codegen_n") + 1] == "4"


def test_livecodebench_command_testoutputprediction_has_no_codegen_n(tmp_path):
    command = _lcb_command(tmp_path, scenario="testoutputprediction")

    assert command[command.index("--scenario") + 1] == "testoutputprediction"
    assert "--codegen_n" not in command


@pytest.mark.parametrize("value,expected", [(False, "false"), (True, "true")])
def test_livecodebench_command_passes_the_thinking_switch(tmp_path, value, expected):
    """Qwen3 这类思考模型：开关不下发的话 max_tokens 会被思考链吃光，样本里没有代码。"""
    command = _lcb_command(tmp_path, {"eval_enable_thinking": value})

    assert command[command.index("--enable_thinking") + 1] == expected
    assert command[-1] == "--evaluate"


def test_livecodebench_command_requires_a_running_vllm(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        _lcb_command(tmp_path, {"eval_base_url": ""})

    assert "eval_base_url" in str(excinfo.value)


@pytest.mark.parametrize("scenario,raw,expected", [
    ("codegeneration", 0.53, 53.0),
    ("testoutputprediction", 0.15384615384615385, 15.38),
    ("codeexecution", 40.91858037578288, 40.92),   # 上游返回的已经是百分数
])
def test_livecodebench_pass_at_k_normalises_the_upstream_scale(scenario, raw, expected):
    assert ec._lcb_pass_at_k({"pass@1": raw}, scenario)["pass@1"] == pytest.approx(expected)


def test_livecodebench_codeexecution_summary_keeps_the_percent_scale(tmp_path):
    """回归：codeexecution 的上游 pass@1 已经是百分数，再乘 100 会写出 4091.86。"""
    instances = [{"question_id": "2777", "id": "sample_0", "output_list": ["print(1)"],
                  "pred_list": ["[-3, -1, 1, 3, 5]"], "graded_list": [True]}]

    result = ec._write_livecodebench_artifacts(
        tmp_path, "livecodebench_codeexe", instances,
        {"pass@1": 40.91858037578288}, "livecodebench:latest", "codeexecution")

    assert result["metrics"]["pass@1"] == pytest.approx(40.92)
    assert result["summary"]["pass@1"] == pytest.approx(40.92)
    assert result["summary"]["passed_samples"] == 1


def test_evaluate_livecodebench_writes_the_judger_contract(tmp_path, monkeypatch):
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(2))
    _fake_lcb_docker(monkeypatch, problems=2, samples=2)
    writer = _Writer()

    result = ec.run_evaluate_livecodebench(_lcb_state(tmp_path, problem), writer)

    # metrics 是百分数（和 evalplus 分支同口径）：2 题都只过第 1 个样本 → pass@1 = 50
    assert result["metrics"]["pass@1"] == pytest.approx(50.0)
    assert result["summary"]["problems"] == 2
    assert result["summary"]["samples"] == 4
    assert result["summary"]["passed_samples"] == 2
    assert result["summary"]["scenario"] == "Scenario.codegeneration"

    samples = [json.loads(line) for line in
               Path(result["sample_path"]).read_text(encoding="utf-8").splitlines()]
    assert [row["task_id"] for row in samples] == ["1873_A", "1873_A", "1873_B", "1873_B"]
    assert "question_content" not in samples[0]          # 样本只留契约字段，不搬题面

    sanitized = [json.loads(line) for line in
                 Path(result["sanitized_path"]).read_text(encoding="utf-8").splitlines()]
    assert sanitized[0]["solution"] == sanitized[0]["completion"]

    detail = [json.loads(line) for line in
              Path(result["result_path"]).read_text(encoding="utf-8").splitlines()]
    # Analyzer 判因直接读 passed，逐样本粒度
    assert [row["passed"] for row in detail] == [True, False, True, False]
    assert all(row["status"] in {"pass", "fail"} for row in detail)


def test_livecodebench_problem_file_accepts_codeexecution(tmp_path):
    path = _write_problems(tmp_path, "execution.jsonl", _lcb_problem_rows(2, "codeexecution"))

    assert ec.check_problem_file(str(path), "livecodebench", "codeexecution") == []


def test_evaluate_livecodebench_codeexecution_keeps_every_sample(tmp_path, monkeypatch):
    """codeexecution 一题多条输入（479 行只有 92 个 question_id）：task_id 必须带 id，
    solution 取 pred_list。"""
    problem = _write_problems(
        tmp_path, "execution.jsonl", _lcb_problem_rows(2, "codeexecution"))
    _fake_lcb_docker(monkeypatch, problems=2, samples=1)
    writer = _Writer()

    result = ec.run_evaluate_livecodebench(
        _lcb_state(tmp_path, problem, "codeexecution"), writer)

    assert result["summary"]["scenario"] == "Scenario.codeexecution"
    samples = [json.loads(line) for line in
               Path(result["sample_path"]).read_text(encoding="utf-8").splitlines()]
    assert [row["task_id"] for row in samples] == ["2777_sample_0", "2777_sample_1"]
    sanitized = [json.loads(line) for line in
                 Path(result["sanitized_path"]).read_text(encoding="utf-8").splitlines()]
    assert sanitized[0]["solution"] == "print(0) #0"


def test_evaluate_livecodebench_selfrepair_runs_codegeneration_first(tmp_path, monkeypatch):
    """selfrepair 要拿 codegen 的产物当输入：同一个 bench 目录先跑一遍 codegeneration。"""
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(2))
    runs = _fake_lcb_docker(monkeypatch, problems=2, samples=2)

    result = ec.run_evaluate_livecodebench(
        _lcb_state(tmp_path, problem, "selfrepair", eval_case_num=2), _Writer())

    scenarios = [command[command.index("--scenario") + 1] for command in runs]
    assert scenarios == ["codegeneration", "selfrepair"]
    # 两次都挂同一个 bench 目录，容器里才读得到上一轮的 Scenario.codegeneration_*_eval_all.json
    mounts = {command[command.index("-v") + 1] for command in runs}
    assert len(mounts) == 1
    # 产物取的是 selfrepair 那份，codegen 的产物只是输入（不参与打分）
    assert result["summary"]["scenario"] == "Scenario.selfrepair"
    assert result["summary"]["problems"] == 2


def test_evaluate_livecodebench_testoutputprediction_uses_pred_list(tmp_path, monkeypatch):
    """testoutputprediction 的代码放在 pred_list，且一题多个测试 —— task_id 要带 test_id。"""
    problem = _write_problems(tmp_path, "prediction.jsonl",
                              _lcb_problem_rows(2, "testoutputprediction"))
    _fake_lcb_docker(monkeypatch, problems=2, samples=2)

    result = ec.run_evaluate_livecodebench(
        _lcb_state(tmp_path, problem, "testoutputprediction"), _Writer())

    rows = [json.loads(line) for line in
            Path(result["result_path"]).read_text(encoding="utf-8").splitlines()]
    assert [row["task_id"] for row in rows] == ["2727_0", "2727_0", "2728_0", "2728_0"]
    assert rows[0]["solution"] == "print(0) #0"
    assert [row["passed"] for row in rows] == [True, False, True, False]
    assert result["summary"]["scenario"] == "Scenario.testoutputprediction"


def test_evaluate_livecodebench_clears_stale_outputs(tmp_path, monkeypatch):
    """上一次的分数不能变成这一次的结果：summary 和 LCB 原生目录都要先清掉。"""
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(1))
    state = _lcb_state(tmp_path, problem)
    bench_dir = (tmp_path / "outputs" / "t1" / "judger" / "v1" / "livecodebench")
    bench_dir.mkdir(parents=True)
    (bench_dir / "livecodebench_summary.json").write_text('{"pass@1": 99.0}',
                                                          encoding="utf-8")
    stale_native = bench_dir / "Qwen3-8B"
    stale_native.mkdir()
    (stale_native / "Scenario.codegeneration_1_0.0_eval_all.json").write_text(
        json.dumps([{"question_id": "old", "output_list": ["x"], "code_list": ["x"],
                     "graded_list": [True]}]), encoding="utf-8")

    seen = {}

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        host_dir = Path(command[command.index("-v") + 1].split(":")[0])
        seen["native_dir_exists"] = (host_dir / "Qwen3-8B").exists()
        (host_dir / "Qwen3-8B").mkdir(parents=True, exist_ok=True)
        prefix = "Scenario.codegeneration_1_0.0"
        (host_dir / "Qwen3-8B" / f"{prefix}_eval_all.json").write_text(
            json.dumps([{"question_id": "1873_A", "output_list": ["print(1)"],
                         "code_list": ["print(1)"], "graded_list": [False]}]),
            encoding="utf-8")
        (host_dir / "Qwen3-8B" / f"{prefix}_eval.json").write_text(
            json.dumps([{"pass@1": 0.0, "detail": {}}]), encoding="utf-8")

        class _Done:
            returncode = 0
        return _Done()

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    result = ec.run_evaluate_livecodebench(state, _Writer())

    assert seen["native_dir_exists"] is False        # 跑之前旧产物已经删了
    assert result["metrics"]["pass@1"] == pytest.approx(0.0)


def test_evaluate_livecodebench_without_outputs_emits_error(tmp_path, monkeypatch):
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(1))
    _fake_lcb_docker(monkeypatch, write_eval_all=False)
    writer = _Writer()

    with pytest.raises(SystemExit):
        ec.run_evaluate_livecodebench(_lcb_state(tmp_path, problem), writer)

    assert writer.failed["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert "Scenario.codegeneration_*_eval_all.json" in writer.failed["message"]


def test_evaluate_livecodebench_container_failure_emits_error(tmp_path, monkeypatch):
    problem = _write_problems(tmp_path, "test.jsonl", _lcb_problem_rows(1))
    writer = _Writer()

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "image"]:
            class _Inspect:
                returncode = 0
            return _Inspect()
        raise ec.subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        ec.run_evaluate_livecodebench(_lcb_state(tmp_path, problem), writer)

    assert writer.failed["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert "LiveCodeBench" in writer.failed["message"]


def test_evaluate_livecodebench_rejects_a_missing_problem_file(tmp_path, monkeypatch):
    _fake_lcb_docker(monkeypatch)
    with pytest.raises(FileNotFoundError):
        ec.run_evaluate_livecodebench(
            _lcb_state(tmp_path, tmp_path / "nope.jsonl"), _Writer())
