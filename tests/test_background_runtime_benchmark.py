"""Tests for the macOS background resource benchmark."""

from argparse import Namespace
from types import SimpleNamespace

from scripts import benchmark_background_runtime as benchmark


def test_resource_snapshot_parses_footprint_sockets_and_threads(monkeypatch):
    outputs = {
        "footprint": "Auxiliary data:\n    phys_footprint: 96.5 MB\n",
        "lsof": (
            "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
            "Vocal 42 user 4u IPv4 0t0 TCP 127.0.0.1:1->1.1.1.1:443 (ESTABLISHED)\n"
            "Vocal 42 user 5u IPv4 0t0 TCP 127.0.0.1:2->1.1.1.1:443 (CLOSE_WAIT)\n"
        ),
        "ps": "USER PID COMMAND\nuser 42 Vocal\nuser 42 \nuser 42 \n",
    }

    def fake_run(command, **_kwargs):
        return SimpleNamespace(stdout=outputs[command[0]], returncode=0)

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)

    assert benchmark._resource_snapshot(42) == {
        "physical_footprint_mib": 96.5,
        "socket_count": 2,
        "socket_states": {"CLOSE_WAIT": 1, "ESTABLISHED": 1},
        "thread_count": 3,
    }


def test_limit_violations_cover_memory_socket_thread_and_growth():
    report = {
        "rss_mib": {"growth": 7.0},
        "resources": {
            "end": {
                "physical_footprint_mib": 101.0,
                "socket_count": 5,
                "thread_count": 21,
            }
        },
    }
    args = Namespace(
        max_physical_footprint_mib=100.0,
        max_socket_count=4,
        max_thread_count=20,
        max_rss_growth_mib=5.0,
    )

    violations = benchmark._limit_violations(report, args)

    assert len(violations) == 4
    assert all("exceeds" in violation for violation in violations)
