"""Process-level proxy routing for legacy Python network clients."""

from __future__ import annotations

import os
from collections.abc import Iterable

_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
_ORIGINAL_PROXY_ENV = {name: os.environ.get(name) for name in _PROXY_ENV_VARS}


def configure_network_proxy(proxy_url: str, direct_hosts: Iterable[str]) -> None:
    """Apply an explicit app proxy or preserve the historical direct route."""
    hosts = {host.casefold() for host in direct_hosts if host}
    if proxy_url:
        for name in _PROXY_ENV_VARS:
            os.environ[name] = proxy_url
        for name in ("no_proxy", "NO_PROXY"):
            entries = [
                item.strip()
                for item in os.environ.get(name, "").split(",")
                if item.strip() and item.strip().casefold() not in hosts
            ]
            os.environ[name] = ",".join(entries)
        return

    for name, original in _ORIGINAL_PROXY_ENV.items():
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original

    for name in ("no_proxy", "NO_PROXY"):
        entries = [
            item.strip()
            for item in os.environ.get(name, "").split(",")
            if item.strip()
        ]
        known = {item.casefold() for item in entries}
        for host in direct_hosts:
            if host and host.casefold() not in known:
                entries.append(host)
                known.add(host.casefold())
        os.environ[name] = ",".join(entries)
