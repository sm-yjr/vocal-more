#!/usr/bin/env python3
"""Collect notices from the locked Rust dependency graph for the macOS product."""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def notices(toolchain: str) -> str:
    command = ["cargo", f"+{toolchain}", "metadata", "--locked", "--format-version=1", "--filter-platform", "aarch64-apple-darwin", "--manifest-path", str(ROOT / "rust/Cargo.toml")]
    metadata = json.loads(subprocess.check_output(command, text=True))
    packages = {package["id"]: package for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    todo = [package["id"] for package in packages.values() if package["name"] in {"vocal-more-desktop", "vocal-more-backend"}]
    reachable = set()
    while todo:
        identity = todo.pop()
        if identity in reachable:
            continue
        reachable.add(identity)
        todo.extend(dependency["pkg"] for dependency in nodes[identity]["deps"])
    sections = ["Vocal More Rust distribution - third-party notices\n\nThe project license is provided separately in LICENSE.txt.\n"]
    for package in sorted((packages[p] for p in reachable if packages[p]["source"]), key=lambda p: (p["name"], p["version"])):
        root = Path(package["manifest_path"]).parent
        files = sorted(path for path in root.iterdir() if path.is_file() and path.name.lower().startswith(("license", "licence", "copying", "notice")))
        if package.get("license_file"):
            files = sorted(set(files) | {root / package["license_file"]})
        sections.append(f"\n{'=' * 72}\n{package['name']} {package['version']}\nLicense: {package.get('license') or 'see license file'}\nRepository: {package.get('repository') or ''}\n")
        for path in files:
            sections.append(f"\n--- {path.name} ---\n{path.read_text(errors='replace')}\n")
    return "".join(sections)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", default="1.98.1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(notices(args.toolchain), encoding="utf-8")
