#!/usr/bin/env python3
"""Monitor one long-running DataMixer web campaign and stop on hard faults."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def process_matches(pid: int, campaign_id: str) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return campaign_id.encode() in cmdline and b"webagent campaign" in cmdline


def rss_mib(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024.0, 2)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def health_status(url: str, timeout: float) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return "ok" if 200 <= int(response.status) < 300 else "http_error"
    except (TimeoutError, socket.timeout):
        return "busy_timeout"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "busy_timeout"
        return "unreachable"
    except Exception:
        return "error"


def pipeline_counts(queue_path: Path, campaign_id: str) -> dict[str, dict[str, int]]:
    connection = sqlite3.connect(f"file:{queue_path}?mode=ro", uri=True, timeout=5)
    try:
        stages = {
            int(index): str(name)
            for index, name in connection.execute(
                "SELECT stage_index,name FROM pipeline_stages WHERE run_id=?",
                (campaign_id,),
            )
        }
        counts: dict[str, dict[str, int]] = {
            name: {} for name in stages.values()
        }
        for index, status, count in connection.execute(
            "SELECT stage_index,status,COUNT(*) FROM pipeline_jobs "
            "WHERE run_id=? GROUP BY stage_index,status",
            (campaign_id,),
        ):
            counts.setdefault(stages.get(int(index), str(index)), {})[
                str(status)
            ] = int(count)
        return counts
    finally:
        connection.close()


def failure_kinds(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not path.exists():
        return counts
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                error = str(json.loads(line).get("error") or "").lower()
            except (json.JSONDecodeError, AttributeError):
                counts["invalid_ledger_row"] += 1
                continue
            if "robots.txt disallows" in error:
                counts["robots_skip"] += 1
            elif "2001::1" in error:
                counts["dns_sink_2001"] += 1
            elif "targetclosederror" in error:
                counts["playwright_target_closed"] += 1
            elif "http 403" in error:
                counts["http_403"] += 1
            elif "http 404" in error:
                counts["http_404"] += 1
            elif "http 405" in error:
                counts["http_405"] += 1
            elif "timed out" in error or "timeout" in error:
                counts["timeout"] += 1
            elif "cannot resolve" in error or "non-public address" in error:
                counts["dns_other"] += 1
            else:
                counts["other"] += 1
    return counts


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def safe_interrupt(pid: int, campaign_id: str, log_path: Path, reason: str) -> None:
    append_event(log_path, {
        "timestamp": timestamp(),
        "event": "stop_requested",
        "pid": pid,
        "reason": reason,
    })
    if not process_matches(pid, campaign_id):
        return
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not process_matches(pid, campaign_id):
            return
        time.sleep(0.5)
    if process_matches(pid, campaign_id):
        os.kill(pid, signal.SIGINT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--crawl-run-id", required=True)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--health-timeout", type=float, default=15.0)
    parser.add_argument("--busy-timeout-limit", type=int, default=8)
    parser.add_argument("--unreachable-limit", type=int, default=3)
    parser.add_argument("--max-pipeline-terminal-failures", type=int, default=20)
    parser.add_argument(
        "--max-pipeline-terminal-failure-ratio", type=float, default=0.25
    )
    parser.add_argument("--max-rss-mib", type=float, default=2048.0)
    parser.add_argument("--min-free-gib", type=float, default=100.0)
    args = parser.parse_args()

    warehouse = args.warehouse.resolve()
    queue_path = warehouse / "pipeline_queue.sqlite"
    crawl_dir = warehouse / "webcrawler_dm_runs" / args.crawl_run_id
    progress_path = crawl_dir / "progress.json"
    failure_path = crawl_dir / "failures.jsonl"
    log_path = (
        warehouse / "watchdog" / f"{args.campaign_id}.{args.crawl_run_id}.jsonl"
    )
    health_failures = {"qwen": 0, "mineru": 0, "proxy": 0}
    busy_timeouts = {"qwen": 0, "mineru": 0, "proxy": 0}

    while process_matches(args.pid, args.campaign_id):
        reasons = []
        try:
            stages = pipeline_counts(queue_path, args.campaign_id)
        except Exception as exc:
            stages = {}
            reasons.append(f"pipeline_queue_unreadable:{type(exc).__name__}")
        terminal_failures = sum(int(counts.get("failed", 0)) for counts in stages.values())
        stage_failure_ratios = (
            int(counts.get("failed", 0))
            / sum(
                int(counts.get(status, 0))
                for status in ("succeeded", "dropped", "failed")
            )
            for counts in stages.values()
            if sum(
                int(counts.get(status, 0))
                for status in ("succeeded", "dropped", "failed")
            )
        )
        terminal_failure_ratio = max(stage_failure_ratios, default=0.0)
        failure_limit = max(1, args.max_pipeline_terminal_failures)
        ratio_limit = max(0.0, args.max_pipeline_terminal_failure_ratio)
        if (
            terminal_failures >= failure_limit
            and terminal_failure_ratio >= ratio_limit
        ):
            reasons.append(
                "pipeline_systemic_failures:"
                f"{terminal_failures}(max_stage_ratio={terminal_failure_ratio:.4f})"
            )

        failures = failure_kinds(failure_path)
        if failures["dns_sink_2001"]:
            reasons.append(f"dns_sink_2001:{failures['dns_sink_2001']}")
        if failures["playwright_target_closed"]:
            reasons.append(
                f"playwright_target_closed:{failures['playwright_target_closed']}"
            )

        service_urls = {
            "qwen": "http://127.0.0.1:8000/v1/models",
            "mineru": "http://127.0.0.1:7986/health",
            "proxy": "http://127.0.0.1:8855/responseProxy/health",
        }
        service_health = {}
        service_health_detail = {}
        for name, url in service_urls.items():
            detail = health_status(url, max(1.0, args.health_timeout))
            service_health_detail[name] = detail
            service_health[name] = detail == "ok"
            if detail == "ok":
                health_failures[name] = 0
                busy_timeouts[name] = 0
            elif detail == "busy_timeout":
                health_failures[name] = 0
                busy_timeouts[name] += 1
                if busy_timeouts[name] >= max(1, args.busy_timeout_limit):
                    reasons.append(
                        f"{name}_busy_timeout_{busy_timeouts[name]}_times"
                    )
            else:
                busy_timeouts[name] = 0
                health_failures[name] += 1
                if health_failures[name] >= max(1, args.unreachable_limit):
                    reasons.append(
                        f"{name}_unreachable_{health_failures[name]}_times"
                    )

        current_rss = rss_mib(args.pid)
        if current_rss > args.max_rss_mib:
            reasons.append(f"rss_mib:{current_rss}")
        free_gib = round(shutil.disk_usage(warehouse).free / (1024 ** 3), 2)
        if free_gib < args.min_free_gib:
            reasons.append(f"free_gib:{free_gib}")

        try:
            progress = json.loads(progress_path.read_text())
        except (OSError, json.JSONDecodeError):
            progress = {}
        event = {
            "timestamp": timestamp(),
            "event": "sample",
            "pid": args.pid,
            "campaign_id": args.campaign_id,
            "crawl_run_id": args.crawl_run_id,
            "crawl": {
                key: progress.get(key)
                for key in (
                    "status", "phase", "current_depth", "pages_fetched",
                    "pages_ingested", "pages_failed", "queue_size", "visited",
                )
            },
            "failure_kinds": dict(failures),
            "pipeline": stages,
            "pipeline_terminal_failures": terminal_failures,
            "pipeline_terminal_failure_ratio": round(terminal_failure_ratio, 6),
            "service_health": service_health,
            "service_health_detail": service_health_detail,
            "rss_mib": current_rss,
            "free_gib": free_gib,
            "reasons": reasons,
        }
        append_event(log_path, event)
        if reasons:
            safe_interrupt(
                args.pid, args.campaign_id, log_path, ";".join(reasons)
            )
            return 2
        time.sleep(max(5.0, args.interval))

    append_event(log_path, {
        "timestamp": timestamp(),
        "event": "campaign_process_exited",
        "pid": args.pid,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
