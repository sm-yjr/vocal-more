from __future__ import annotations

import os

from vocal_more.infrastructure import network_proxy


def test_explicit_proxy_updates_all_common_environment_variables(monkeypatch):
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,dashscope.aliyuncs.com")
    monkeypatch.setenv("no_proxy", "localhost,dashscope.aliyuncs.com")

    network_proxy.configure_network_proxy(
        "http://127.0.0.1:7890",
        ["dashscope.aliyuncs.com"],
    )

    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        assert os.environ[name] == "http://127.0.0.1:7890"
    assert os.environ["NO_PROXY"] == "localhost"
    assert os.environ["no_proxy"] == "localhost"


def test_blank_proxy_keeps_dashscope_on_direct_route(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("no_proxy", "")

    network_proxy.configure_network_proxy("", ["dashscope.aliyuncs.com"])

    assert os.environ["NO_PROXY"] == "localhost,dashscope.aliyuncs.com"
    assert os.environ["no_proxy"] == "dashscope.aliyuncs.com"


def test_clearing_proxy_removes_app_override(monkeypatch):
    monkeypatch.setattr(
        network_proxy,
        "_ORIGINAL_PROXY_ENV",
        {name: None for name in network_proxy._PROXY_ENV_VARS},
    )
    for name in network_proxy._PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://before.test:8080")
    network_proxy.configure_network_proxy("socks5://127.0.0.1:1080", [])
    network_proxy.configure_network_proxy("", [])

    assert all(name not in os.environ for name in network_proxy._PROXY_ENV_VARS)
