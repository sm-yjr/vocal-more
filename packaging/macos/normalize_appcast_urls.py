#!/usr/bin/env python3
"""Normalize generated Sparkle enclosure URLs for GitHub Release assets."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


_RELEASE_ASSET_URL = re.compile(
    r"(?P<prefix>https://github\.com/[^/]+/[^/]+/releases/download/)"
    r"(?P<tag>v?\d+\.\d+\.\d+)/"
    r"(?P<name>Vocal(?:-More-|%20More|\.More| More)\d+\.\d+\.\d+[^\"<]*)"
)
_ASSET_VERSION = re.compile(
    r"^Vocal(?:-More-|%20More|\.More| More)(?P<version>\d+\.\d+\.\d+)"
)


def normalize_appcast_urls(xml: str) -> str:
    """Point each enclosure at its own release and GitHub-normalized name."""

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        version_match = _ASSET_VERSION.match(name)
        if version_match is None:
            return match.group(0)
        normalized_name = name.replace("%20", ".").replace(" ", ".")
        version = version_match.group("version")
        return f"{match.group('prefix')}v{version}/{normalized_name}"

    return _RELEASE_ASSET_URL.sub(replace, xml)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("appcast", type=Path)
    args = parser.parse_args()
    xml = args.appcast.read_text(encoding="utf-8")
    args.appcast.write_text(normalize_appcast_urls(xml), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
