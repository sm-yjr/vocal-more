"""Sample macOS process CPU and memory with a repeatable state label."""

from __future__ import annotations

import argparse
import json
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


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * ratio)
    return ordered[index]


def sample(pid: int, *, state: str, duration: float, interval: float) -> dict:
    first = _snapshot(pid)
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
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0:
        parser.error("--duration and --interval must be positive")

    report = sample(
        args.pid,
        state=args.state,
        duration=args.duration,
        interval=args.interval,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
