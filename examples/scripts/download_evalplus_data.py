#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下载 evalplus 官方数据集（HumanEval+ / MBPP+），按 Judger 的 ``problem_path`` 格式落盘。

判分走 ``ganler/evalplus`` 官方镜像（数据集烤在镜像里），但**生成**在宿主机上跑、要读
题目 prompt，所以宿主机也得有一份同样的数据集。两份数据的官方来源是 evalplus 的
GitHub release（也正是 evalplus 自己 `get_*_plus()` 会下的那两个文件）：

    HumanEval+ v0.1.10  https://github.com/evalplus/humanevalplus_release/releases/download/v0.1.10/HumanEvalPlus.jsonl.gz
    MBPP+      v0.2.0   https://github.com/evalplus/mbppplus_release/releases/download/v0.2.0/MbppPlus.jsonl.gz

用法：

    python examples/scripts/download_evalplus_data.py                # 两个都下（默认 data/evalplus）
    python examples/scripts/download_evalplus_data.py --dataset mbpp # 只下 MBPP+
    python examples/scripts/download_evalplus_data.py --from-cache   # 离线：直接复制本机 evalplus 缓存
    EVALPLUS_RELEASE_PROXY=https://ghproxy.net/ python examples/scripts/download_evalplus_data.py

下完会核对 md5（就是镜像判分结果里的 ``dataset_hash``），对不上直接报错，不会悄悄用错数据。

⚠️ 别用 HF 上的 ``evalplus/humanevalplus`` / ``evalplus/mbppplus``：那不是 +版本数据集
（前者没有 ``base_input/plus_input/atol``，后者 ``task_id`` 是裸数字 ``2`` 而不是
``Mbpp/2``、也没有 ``assertion``），判分和这道流水线都对不上。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 数据集名 -> (release 包名, 版本, 默认文件名, 题数, 官方 md5)
# md5 取自 evalplus 的 get_human_eval_plus_hash() / get_mbpp_plus_hash()，也和官方镜像一致。
_DATASETS = {
    "humaneval": {
        "release": "HumanEvalPlus", "version": "v0.1.10", "rows": 164,
        "filename": "humaneval_plus.jsonl",
        "md5": "fe585eb4df8c88d844eeb463ea4d0302",
        "cache_copier": "evalplus.data.humaneval:_ready_human_eval_plus_path",
    },
    "mbpp": {
        "release": "MbppPlus", "version": "v0.2.0", "rows": 378,
        "filename": "mbpp_plus.jsonl",
        "md5": "ee43ecabebf20deef4bb776a405ac5b1",
        "cache_copier": "evalplus.data.mbpp:_ready_mbpp_plus_path",
    },
}


def _release_url(spec: dict) -> str:
    repo = f"{spec['release'].lower()}_release"
    return (f"https://github.com/evalplus/{repo}/releases/download/"
            f"{spec['version']}/{spec['release']}.jsonl.gz")


def _read_cache(spec: dict) -> bytes:
    """离线路线：evalplus 自己缓存的就是 release 解压后的原文。"""
    module_name, func_name = spec["cache_copier"].split(":")
    module = __import__(module_name, fromlist=[func_name])
    return Path(getattr(module, func_name)()).read_bytes()


def _download(spec: dict) -> bytes:
    url = _release_url(spec)
    proxy = os.getenv("EVALPLUS_RELEASE_PROXY", "")
    request = urllib.request.Request(proxy + url if proxy else url,
                                     headers={"User-Agent": "loopai-judger"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return gzip.decompress(response.read())


def _fetch(name: str, spec: dict, use_cache: bool) -> bytes:
    if use_cache:
        return _read_cache(spec)
    try:
        return _download(spec)
    except Exception as exc:
        hint = (f"下载 {_release_url(spec)} 失败（{type(exc).__name__}: {exc}）。"
                "可以先用官方镜像/别的机器下好再拷过来，或加 --from-cache 直接用本机 "
                "evalplus 缓存，或设 EVALPLUS_RELEASE_PROXY 走代理。")
        raise SystemExit(hint) from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", choices=(*_DATASETS, "all"), default="all",
                        help="下哪个数据集，默认全下")
    parser.add_argument("--out-dir", default=str(_REPO_ROOT / "data" / "evalplus"),
                        help="输出目录，默认 data/evalplus")
    parser.add_argument("--from-cache", action="store_true",
                        help="不联网，直接复制本机 evalplus 缓存的官方数据集")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    names = list(_DATASETS) if args.dataset == "all" else [args.dataset]
    for name in names:
        spec = _DATASETS[name]
        payload = _fetch(name, spec, args.from_cache)
        actual = hashlib.md5(payload).hexdigest()
        if actual != spec["md5"]:
            raise SystemExit(
                f"{name}: md5 对不上（拿到 {actual}，官方是 {spec['md5']}），"
                "这份数据不能用，请检查下载源是否为 evalplus 的 GitHub release。")
        if payload.count(b"\n") != spec["rows"]:
            raise SystemExit(f"{name}: 行数不是 {spec['rows']}，数据不完整")

        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / spec["filename"]
        target.write_bytes(payload)
        print(f"{name}: {spec['rows']} 题 -> {target}（md5 {actual}，与官方镜像一致）")


if __name__ == "__main__":
    main()
