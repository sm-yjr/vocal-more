#!/usr/bin/env python3
"""Normalize generated Sparkle enclosure URLs for GitHub Release assets."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


_TAG = r"v?\d+\.\d+\.\d+(?:[ab]\d+|-(?:alpha|beta)\.\d+)?"
_RELEASE_ASSET_URL = re.compile(
    r"(?P<prefix>https://github\.com/[^/]+/[^/]+/releases/download/)"
    rf"(?P<tag>{_TAG})/"
    r"(?P<name>Vocal(?:-More-|%20More|\.More| More)\d[^\"<]*)"
)
# DMGs name the SemVer (0.6+) or PEP 440 (pre-0.6) version; Sparkle names
# deltas after CFBundleVersion, which is the bare build number since 0.6.
_ASSET_VERSION = re.compile(
    r"^Vocal(?:-More-|%20More|\.More| More)"
    r"(?P<version>\d+\.\d+\.\d+(?:[ab]\d+|-beta\.\d+)?|\d+(?=-))"
)


def normalize_appcast_urls(xml: str, *, tag_map: dict[str, str] | None = None) -> str:
    """Point each enclosure at its own release and GitHub-normalized name."""

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        version_match = _ASSET_VERSION.match(name)
        if version_match is None:
            return match.group(0)
        normalized_name = name.replace("%20", ".").replace(" ", ".")
        version = version_match.group("version")
        # An unknown bare build belongs to an older item: keep its own release.
        default = f"v{version}" if "." in version else match.group("tag")
        tag = (tag_map or {}).get(version, default)
        if not re.fullmatch(_TAG, tag):
            raise ValueError(f"Invalid release tag: {tag}")
        return f"{match.group('prefix')}{tag}/{normalized_name}"

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
