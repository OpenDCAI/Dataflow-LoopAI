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


def test_example_config_resolves_to_the_evalplus_datasets():
    """样例配置里的 format_type 必须真的能被识别（写错就是起完 vLLM 才发现）。"""
    config = json.loads(
        (Path(__file__).resolve().parents[1]
         / "examples" / "config" / "code_bench_gov.json").read_text(encoding="utf-8"))

    resolved = {
        bench["name"]: ec.resolve_code_task({"format_type": bench["format_type"]})
        for bench in config["judger"]["benchlist"]
    }

    assert resolved == {"humaneval": "humaneval", "mbpp": "mbpp"}


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
