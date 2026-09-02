"""Sample macOS process CPU and memory with a repeatable state label."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from pathlib import Path


def _process_cpu_seconds(value: str) -> float:
    fields = value.strip().split(":")
    if len(fields) == 2:
        minutes, seconds = fields
        return float(minutes) * 60 + float(seconds)
    if len(fields) == 3:
        hours, minutes, seconds = fields
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    raise ValueError(f"Unsupported ps time value: {value!r}")


def _snapshot(pid: int) -> dict[str, float | int]:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "time=,rss=,vsz="],
        check=True,
        capture_output=True,
        text=True,
    )
    cpu_time, rss_kib, vsz_kib = result.stdout.split()
    return {
        "at": time.monotonic(),
        "cpu_seconds": _process_cpu_seconds(cpu_time),
        "rss_mib": int(rss_kib) / 1024,
        "vsz_mib": int(vsz_kib) / 1024,
    }


def _scaled_mib(value: str, unit: str) -> float:
    amount = float(value)
    return amount * {"KB": 1 / 1024, "MB": 1, "GB": 1024}[unit]


def _physical_footprint_mib(pid: int) -> float:
    result = subprocess.run(
        ["footprint", "--pid", str(pid), "--noCategories"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(
        r"phys_footprint:\s+([0-9.]+)\s+(KB|MB|GB)",
        result.stdout,
    )
    if match is None:
        raise ValueError("footprint output did not contain phys_footprint")
    return _scaled_mib(match.group(1), match.group(2))


def _socket_snapshot(pid: int) -> tuple[int, dict[str, int]]:
    result = subprocess.run(
        ["lsof", "-nP", "-a", "-p", str(pid), "-i"],
        check=False,
        capture_output=True,
        text=True,
    )
    rows = [line for line in result.stdout.splitlines()[1:] if line.strip()]
    states: dict[str, int] = {}
    for row in rows:
        match = re.search(r"\(([^()]+)\)\s*$", row)
        state = match.group(1) if match else "UNSPECIFIED"
        states[state] = states.get(state, 0) + 1
    return len(rows), dict(sorted(states.items()))


def _thread_count(pid: int) -> int:
    result = subprocess.run(
        ["ps", "-M", "-p", str(pid)],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(0, len([line for line in result.stdout.splitlines() if line.strip()]) - 1)


def _resource_snapshot(pid: int) -> dict:
    socket_count, socket_states = _socket_snapshot(pid)
    return {
        "physical_footprint_mib": round(_physical_footprint_mib(pid), 3),
        "socket_count": socket_count,
        "socket_states": socket_states,
        "thread_count": _thread_count(pid),
    }


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * ratio)
    return ordered[index]


def sample(pid: int, *, state: str, duration: float, interval: float) -> dict:
    first = _snapshot(pid)
    first_resources = _resource_snapshot(pid)
    samples = []
    deadline = first["at"] + duration
    previous = first
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        current = _snapshot(pid)
        wall_delta = current["at"] - previous["at"]
        cpu_delta = current["cpu_seconds"] - previous["cpu_seconds"]
        current["cpu_percent"] = max(0.0, cpu_delta / wall_delta * 100)
        samples.append(current)
        previous = current

    cpu = [float(item["cpu_percent"]) for item in samples]
    rss = [float(item["rss_mib"]) for item in samples]
    wall = max(0.001, float(previous["at"]) - float(first["at"]))
    final_resources = _resource_snapshot(pid)
    return {
        "schema_version": 1,
        "platform": "macOS",
        "pid": pid,
        "state": state,
        "duration_seconds": round(wall, 3),
        "interval_seconds": interval,
        "sample_count": len(samples),
        "cpu_percent": {
            "mean": round(statistics.fmean(cpu), 3),
            "p95": round(_percentile(cpu, 0.95), 3),
            "max": round(max(cpu), 3),
        },
        "rss_mib": {
            "start": round(float(first["rss_mib"]), 3),
            "end": round(float(previous["rss_mib"]), 3),
            "p95": round(_percentile(rss, 0.95), 3),
            "max": round(max(rss), 3),
            "growth": round(float(previous["rss_mib"]) - float(first["rss_mib"]), 3),
        },
        "resources": {
            "start": first_resources,
            "end": final_resources,
            "growth": {
                "physical_footprint_mib": round(
                    final_resources["physical_footprint_mib"]
                    - first_resources["physical_footprint_mib"],
                    3,
                ),
                "socket_count": (
                    final_resources["socket_count"] - first_resources["socket_count"]
                ),
                "thread_count": (
                    final_resources["thread_count"] - first_resources["thread_count"]
                ),
            },
        },
    }


def _limit_violations(report: dict, args: argparse.Namespace) -> list[str]:
    violations = []
    end_resources = report["resources"]["end"]
    checks = (
        ("physical_footprint_mib", args.max_physical_footprint_mib),
        ("socket_count", args.max_socket_count),
        ("thread_count", args.max_thread_count),
    )
    for field, limit in checks:
        if limit is not None and end_resources[field] > limit:
            violations.append(f"{field}={end_resources[field]} exceeds {limit}")
    if (
        args.max_rss_growth_mib is not None
        and report["rss_mib"]["growth"] > args.max_rss_growth_mib
    ):
        violations.append(
            f"rss_growth_mib={report['rss_mib']['growth']} "
            f"exceeds {args.max_rss_growth_mib}"
        )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-physical-footprint-mib", type=float)
    parser.add_argument("--max-socket-count", type=int)
    parser.add_argument("--max-thread-count", type=int)
    parser.add_argument("--max-rss-growth-mib", type=float)
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0:
        parser.error("--duration and --interval must be positive")

    report = sample(
        args.pid,
        state=args.state,
        duration=args.duration,
        interval=args.interval,
    )
    violations = _limit_violations(report, args)
    report["limits"] = {"passed": not violations, "violations": violations}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
