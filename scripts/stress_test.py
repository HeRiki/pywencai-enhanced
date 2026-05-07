#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
SRC_DIR_TEXT = str(SRC_DIR)
sys.path = [entry for entry in sys.path if entry != SRC_DIR_TEXT]
sys.path.insert(0, SRC_DIR_TEXT)

import pandas as pd
import requests

import pywencai
from pywencai import headers as headers_module
from pywencai import wencai as wencai_module
from pywencai.convert import ConvertError
from pywencai.wencai import WencaiEmptyDataError, WencaiUnexpectedResponseError


@dataclass(frozen=True)
class PhaseConfig:
    name: str
    rate: float
    duration_seconds: int
    concurrency: int


def _parse_phase(raw: str) -> PhaseConfig:
    parts = [segment.strip() for segment in raw.split(":")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "phase must be name:rate:duration_seconds:concurrency"
        )
    name, rate_text, duration_text, concurrency_text = parts
    try:
        rate = float(rate_text)
        duration_seconds = int(duration_text)
        concurrency = int(concurrency_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if rate <= 0:
        raise argparse.ArgumentTypeError("phase rate must be > 0")
    if duration_seconds <= 0:
        raise argparse.ArgumentTypeError("phase duration must be > 0")
    if concurrency <= 0:
        raise argparse.ArgumentTypeError("phase concurrency must be > 0")
    return PhaseConfig(
        name=name,
        rate=rate,
        duration_seconds=duration_seconds,
        concurrency=concurrency,
    )


def _load_yaml_cookie(path: Path, dotted_key: str) -> str:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("PyYAML is required for --cookie-config") from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    current: Any = data
    for key in dotted_key.split("."):
        if not isinstance(current, dict) or key not in current:
            raise KeyError(f"missing config key: {dotted_key}")
        current = current[key]
    cookie = str(current or "").strip()
    if not cookie:
        raise RuntimeError(f"config key is empty: {dotted_key}")
    return cookie


def _resolve_cookie(args) -> str:
    if args.cookie_env:
        cookie = os.environ.get(args.cookie_env, "").strip()
        if not cookie:
            raise RuntimeError(f"environment variable is empty: {args.cookie_env}")
        return cookie
    if args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        if not cookie:
            raise RuntimeError(f"cookie file is empty: {args.cookie_file}")
        return cookie
    if args.cookie_config:
        config_path = Path(args.cookie_config).expanduser().resolve()
        return _load_yaml_cookie(config_path, args.cookie_key)
    raise RuntimeError("one of --cookie-env, --cookie-file, or --cookie-config is required")


def _percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _classify_exception(exc: BaseException) -> Dict[str, Any]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(exc, requests.Timeout):
        category = "timeout"
    elif isinstance(exc, requests.ConnectionError):
        category = "connection_error"
    elif isinstance(exc, requests.HTTPError):
        category = "http_error"
    elif isinstance(exc, WencaiUnexpectedResponseError):
        category = "unexpected_response"
    elif isinstance(exc, WencaiEmptyDataError):
        category = "empty_response"
    elif isinstance(exc, ConvertError):
        category = "convert_error"
    else:
        category = "other_error"
    return {
        "category": category,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "status_code": status_code,
    }


def _counter_delta(after: Dict[str, int], before: Dict[str, int]) -> Dict[str, int]:
    delta = Counter(after or {})
    delta.subtract(Counter(before or {}))
    return {key: int(value) for key, value in delta.items() if value}


def _recent_event_delta(after_events: List[Dict[str, Any]], before_total: int) -> List[Dict[str, Any]]:
    return [event for event in (after_events or []) if int(event.get("event_id", 0)) > int(before_total or 0)]


def _exercise_request(
    *,
    query: str,
    cookie: str,
    query_type: str,
    user_agent: str | None,
    strict: bool,
    loop: bool,
    request_params: Dict[str, Any],
) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        frame = pywencai.get(
            query=query,
            cookie=cookie,
            query_type=query_type,
            user_agent=user_agent,
            strict=strict,
            loop=loop,
            log=False,
            request_params=request_params,
        )
        elapsed = time.perf_counter() - started
        rows = int(len(frame.index)) if isinstance(frame, pd.DataFrame) else 0
        return {
            "ok": True,
            "elapsed_seconds": elapsed,
            "rows": rows,
            "empty": rows == 0,
        }
    except BaseException as exc:
        elapsed = time.perf_counter() - started
        details = _classify_exception(exc)
        details.update(
            {
                "ok": False,
                "elapsed_seconds": elapsed,
                "rows": 0,
                "empty": False,
            }
        )
        return details


def _summarize_phase(
    phase: PhaseConfig,
    results: List[Dict[str, Any]],
    wall_seconds: float,
    metrics_before: Dict[str, Any],
    metrics_after: Dict[str, Any],
) -> Dict[str, Any]:
    total = len(results)
    successes = [item for item in results if item["ok"]]
    non_empty = [item for item in successes if not item["empty"]]
    empty = [item for item in successes if item["empty"]]
    failures = [item for item in results if not item["ok"]]
    latencies = [float(item["elapsed_seconds"]) for item in results]
    status_counts = Counter(
        str(item["status_code"])
        for item in failures
        if item.get("status_code") is not None
    )
    error_counts = Counter(item.get("error_type", "unknown") for item in failures)

    bucket_delta = _counter_delta(
        metrics_after["token_bucket_usage"],
        metrics_before["token_bucket_usage"],
    )
    request_outcomes = _counter_delta(
        metrics_after["request_outcomes"],
        metrics_before["request_outcomes"],
    )
    request_outcomes_by_reason = _counter_delta(
        metrics_after["request_outcomes_by_reason"],
        metrics_before["request_outcomes_by_reason"],
    )
    request_bucket_outcomes = _counter_delta(
        metrics_after["request_bucket_outcomes"],
        metrics_before["request_bucket_outcomes"],
    )
    recent_request_events = _recent_event_delta(
        metrics_after["recent_request_events"],
        metrics_before["request_event_total"],
    )

    return {
        "phase": phase.name,
        "rate": phase.rate,
        "duration_seconds": phase.duration_seconds,
        "concurrency": phase.concurrency,
        "scheduled_requests": total,
        "wall_seconds": round(wall_seconds, 3),
        "achieved_rps": round(total / wall_seconds, 3) if wall_seconds > 0 else 0.0,
        "success_non_empty": len(non_empty),
        "success_empty": len(empty),
        "failures": len(failures),
        "success_rate": round((len(successes) / total), 4) if total else 0.0,
        "latency_seconds": {
            "min": round(min(latencies), 4) if latencies else 0.0,
            "avg": round(statistics.mean(latencies), 4) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 4) if latencies else 0.0,
            "p95": round(_percentile(latencies, 0.95), 4) if latencies else 0.0,
            "p99": round(_percentile(latencies, 0.99), 4) if latencies else 0.0,
            "max": round(max(latencies), 4) if latencies else 0.0,
        },
        "http_status_counts": dict(status_counts),
        "error_counts": dict(error_counts),
        "token_calls": metrics_after["token_total_calls"] - metrics_before["token_total_calls"],
        "token_cache_hits": metrics_after["token_cache_hits"] - metrics_before["token_cache_hits"],
        "token_force_refresh_calls": (
            metrics_after["token_force_refresh_calls"]
            - metrics_before["token_force_refresh_calls"]
        ),
        "token_force_refresh_reasons": _counter_delta(
            metrics_after["token_force_refresh_reasons"],
            metrics_before["token_force_refresh_reasons"],
        ),
        "token_cache_policy_usage": _counter_delta(
            metrics_after["token_cache_policy_usage"],
            metrics_before["token_cache_policy_usage"],
        ),
        "token_generation_modes": _counter_delta(
            metrics_after["token_generation_modes"],
            metrics_before["token_generation_modes"],
        ),
        "session_reset_calls": (
            metrics_after["session_reset_calls"] - metrics_before["session_reset_calls"]
        ),
        "session_reset_reasons": _counter_delta(
            metrics_after["session_reset_reasons"],
            metrics_before["session_reset_reasons"],
        ),
        "request_outcomes": request_outcomes,
        "request_outcomes_by_reason": request_outcomes_by_reason,
        "request_bucket_outcomes": request_bucket_outcomes,
        "request_event_dropped": (
            metrics_after["request_event_dropped"] - metrics_before["request_event_dropped"]
        ),
        "recent_request_events": recent_request_events[-20:],
        "token_bucket_usage": bucket_delta,
    }


def _run_phase(
    phase: PhaseConfig,
    *,
    query: str,
    cookie: str,
    query_type: str,
    user_agent: str | None,
    strict: bool,
    loop: bool,
    request_params: Dict[str, Any],
) -> Dict[str, Any]:
    interval = 1.0 / phase.rate
    semaphore = threading.Semaphore(phase.concurrency)
    metrics_before = headers_module.get_runtime_metrics()
    results: List[Dict[str, Any]] = []
    started = time.perf_counter()

    def task_wrapper() -> Dict[str, Any]:
        try:
            return _exercise_request(
                query=query,
                cookie=cookie,
                query_type=query_type,
                user_agent=user_agent,
                strict=strict,
                loop=loop,
                request_params=request_params,
            )
        finally:
            semaphore.release()

    with concurrent.futures.ThreadPoolExecutor(max_workers=phase.concurrency) as executor:
        futures = []
        next_launch = time.perf_counter()
        deadline = next_launch + phase.duration_seconds
        while True:
            now = time.perf_counter()
            if now >= deadline:
                break
            sleep_seconds = max(0.0, next_launch - now)
            if sleep_seconds:
                time.sleep(sleep_seconds)
            remaining = max(0.0, deadline - time.perf_counter())
            acquired = semaphore.acquire(timeout=remaining)
            if not acquired:
                break
            futures.append(executor.submit(task_wrapper))
            next_launch += interval
        for future in futures:
            results.append(future.result())

    wall_seconds = time.perf_counter() - started
    metrics_after = headers_module.get_runtime_metrics()
    return _summarize_phase(
        phase=phase,
        results=results,
        wall_seconds=wall_seconds,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
    )


def _build_report(
    phases: Iterable[Dict[str, Any]],
    *,
    query: str,
    query_type: str,
    strict: bool,
    loop: bool,
) -> Dict[str, Any]:
    phase_list = list(phases)
    aggregated = {
        "scheduled_requests": sum(int(item["scheduled_requests"]) for item in phase_list),
        "success_non_empty": sum(int(item["success_non_empty"]) for item in phase_list),
        "success_empty": sum(int(item["success_empty"]) for item in phase_list),
        "failures": sum(int(item["failures"]) for item in phase_list),
        "token_calls": sum(int(item["token_calls"]) for item in phase_list),
        "token_cache_hits": sum(int(item["token_cache_hits"]) for item in phase_list),
        "token_force_refresh_calls": sum(int(item["token_force_refresh_calls"]) for item in phase_list),
        "session_reset_calls": sum(int(item["session_reset_calls"]) for item in phase_list),
        "http_status_counts": dict(sum((Counter(item["http_status_counts"]) for item in phase_list), Counter())),
        "error_counts": dict(sum((Counter(item["error_counts"]) for item in phase_list), Counter())),
        "token_cache_policy_usage": dict(
            sum((Counter(item["token_cache_policy_usage"]) for item in phase_list), Counter())
        ),
        "token_force_refresh_reasons": dict(
            sum((Counter(item["token_force_refresh_reasons"]) for item in phase_list), Counter())
        ),
        "token_generation_modes": dict(
            sum((Counter(item["token_generation_modes"]) for item in phase_list), Counter())
        ),
        "session_reset_reasons": dict(
            sum((Counter(item["session_reset_reasons"]) for item in phase_list), Counter())
        ),
        "request_outcomes": dict(
            sum((Counter(item["request_outcomes"]) for item in phase_list), Counter())
        ),
        "request_outcomes_by_reason": dict(
            sum((Counter(item["request_outcomes_by_reason"]) for item in phase_list), Counter())
        ),
        "request_bucket_outcomes": dict(
            sum((Counter(item["request_bucket_outcomes"]) for item in phase_list), Counter())
        ),
        "request_event_dropped": sum(int(item["request_event_dropped"]) for item in phase_list),
        "recent_request_events": [
            event
            for item in phase_list
            for event in item["recent_request_events"]
        ][-20:],
    }
    total = aggregated["scheduled_requests"]
    total_successes = aggregated["success_non_empty"] + aggregated["success_empty"]
    aggregated["success_rate"] = round((total_successes / total), 4) if total else 0.0
    return {
        "query": query,
        "query_type": query_type,
        "strict": strict,
        "loop": loop,
        "phases": phase_list,
        "aggregate": aggregated,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _default_phases() -> List[PhaseConfig]:
    return [
        PhaseConfig(name="warmup", rate=1.0, duration_seconds=15, concurrency=1),
        PhaseConfig(name="medium", rate=2.0, duration_seconds=15, concurrency=2),
        PhaseConfig(name="hot", rate=4.0, duration_seconds=10, concurrency=4),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live pywencai stress checks.")
    parser.add_argument("--query", default="平安银行", help="iwencai query text")
    parser.add_argument("--query-type", default="stock", help="iwencai query_type")
    parser.add_argument("--user-agent", default=None, help="optional fixed User-Agent")
    parser.add_argument("--cookie-env", default=None, help="environment variable containing the iwencai cookie")
    parser.add_argument("--cookie-file", default=None, help="path to a text file containing the iwencai cookie")
    parser.add_argument("--cookie-config", default=None, help="path to a YAML config file containing the iwencai cookie")
    parser.add_argument("--cookie-key", default="data.cookie", help="dotted YAML key for --cookie-config")
    parser.add_argument(
        "--phase",
        action="append",
        type=_parse_phase,
        default=[],
        help="phase config in name:rate:duration_seconds:concurrency form; can be passed multiple times",
    )
    parser.add_argument("--strict", action="store_true", default=True, help="call pywencai.get(..., strict=True)")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="call pywencai.get(..., strict=False)")
    parser.add_argument("--loop", action="store_true", help="enable loop=True during the stress run")
    parser.add_argument("--output-json", default=None, help="optional path to write the JSON report")
    args = parser.parse_args()

    cookie = _resolve_cookie(args)
    phases = args.phase or _default_phases()
    request_params: Dict[str, Any] = {}

    pywencai.reset_logger()
    headers_module.clear_runtime_cache()
    headers_module.clear_runtime_metrics()
    wencai_module.clear_runtime_state()

    try:
        phase_reports = []
        for phase in phases:
            print(
                f"[phase] name={phase.name} rate={phase.rate} duration={phase.duration_seconds}s "
                f"concurrency={phase.concurrency}",
                flush=True,
            )
            phase_reports.append(
                _run_phase(
                    phase,
                    query=args.query,
                    cookie=cookie,
                    query_type=args.query_type,
                    user_agent=args.user_agent,
                    strict=args.strict,
                    loop=args.loop,
                    request_params=request_params,
                )
            )
        report = _build_report(
            phase_reports,
            query=args.query,
            query_type=args.query_type,
            strict=args.strict,
            loop=args.loop,
        )
    finally:
        wencai_module.clear_runtime_state()

    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(report_text)
    if args.output_json:
        output_path = Path(args.output_json).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_text + "\n", encoding="utf-8")
        print(f"[saved] {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
