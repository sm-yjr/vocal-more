#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""在真实 OS pipe 上比较读取同一历史消息的两种方式，不涉及产品数据。"""
import argparse
import io
import json
import os
from pathlib import Path
import statistics
import threading
import time

class CountedPipe(io.FileIO):
    calls = 0
    def read(self, size=-1):
        self.calls += 1
        return super().read(size)
    def readinto(self, buffer):
        self.calls += 1
        return super().readinto(buffer)

def measure(payload, buffered):
    read_fd, write_fd = os.pipe()
    raw = CountedPipe(read_fd, "rb", closefd=True)
    reader = io.BufferedReader(raw, 64 * 1024) if buffered else raw
    def send():
        try:
            remaining = memoryview(payload)
            while remaining:
                remaining = remaining[os.write(write_fd, remaining):]
        finally:
            os.close(write_fd)
    writer = threading.Thread(target=send)
    start = time.perf_counter()
    writer.start()
    received = reader.readline(40 * 1024 * 1024 + 1)
    elapsed = (time.perf_counter() - start) * 1000
    assert received == payload
    reads = raw.calls
    reader.close()
    writer.join()
    return {"ms": elapsed, "raw_read_calls": reads}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = (json.dumps({"method": "recordings_changed", "params": {"recordings": [
        {"id": i, "transcript": "长文本 Rust 测试🙂" * 100} for i in range(30)
    ]}}, ensure_ascii=False) + "\n").encode()
    rows = {"unbuffered": [], "buffered": []}
    for turn in range(10):
        for kind in (rows if turn % 2 else reversed(rows)):
            rows[kind].append(measure(payload, kind == "buffered"))
    result = {"payload_bytes": len(payload), "rounds": 10, "scope": "same real pipe message; includes writer thread startup; excludes app rendering", "runs": rows,
        "summary": {kind: {"p50_ms": statistics.median(r["ms"] for r in values),
            "median_raw_read_calls": statistics.median(r["raw_read_calls"] for r in values)} for kind, values in rows.items()}}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"]))

if __name__ == "__main__":
    main()
