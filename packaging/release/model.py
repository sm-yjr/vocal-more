"""Strict version/channel and candidate identity contracts (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tomllib

REPOSITORY = "sm-yjr/vocal-more"
EMPTY_BASELINE = b"<!-- No previous Vocal More channel feed. -->\n"
POLICY = 1
SCHEMA = 1
SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:([ab])([1-9]\d*))?")
SHA = re.compile(r"[0-9a-f]{40}")


class ReleaseError(RuntimeError):
    """A release must stop, rather than silently select another artifact."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    stage: str = ""
    number: int = 0

    @classmethod
    def parse(cls, value: str) -> Version:
        match = VERSION.fullmatch(value)
        if not match:
            raise ReleaseError(f"Unsupported project version: {value!r}; use X.Y.Z, X.Y.ZaN or X.Y.ZbN")
        major, minor, patch, stage, number = match.groups()
        result = cls(int(major), int(minor), int(patch), stage or "", int(number or 0))
        if result.number > 255:
            raise ReleaseError("Alpha/beta sequence must be in 1..255 for CFBundleVersion")
        return result

    @classmethod
    def from_tag(cls, tag: str) -> Version:
        value = tag.removeprefix("v")
        value = re.sub(r"-alpha\.([1-9]\d*)$", r"a\1", value)
        value = re.sub(r"-beta\.([1-9]\d*)$", r"b\1", value)
        return cls.parse(value)

    @property
    def base(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def text(self) -> str:
        return self.base + (f"{self.stage}{self.number}" if self.stage else "")

    @property
    def channel(self) -> str:
        return {"": "stable", "a": "alpha", "b": "beta"}[self.stage]

    @property
    def tag(self) -> str:
        suffix = f"-{self.channel}.{self.number}" if self.stage else ""
        return f"v{self.base}{suffix}"

    @property
    def key(self) -> tuple[int, ...]:
        return self.major, self.minor, self.patch, {"a": 0, "b": 1, "": 2}[self.stage], self.number

    @property
    def feed_tag(self) -> str:
        return "sparkle-feed" + (f"-{self.channel}" if self.stage else "")

    @property
    def feed_url(self) -> str:
        return f"https://github.com/{REPOSITORY}/releases/download/{self.feed_tag}/appcast.xml"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_context(root: Path, source_sha: str, tag: str = "") -> dict:
    if not SHA.fullmatch(source_sha):
        raise ReleaseError("source_sha must be a full commit SHA")
    project = tomllib.loads((root / "pyproject.toml").read_text())
    version = Version.parse(project["project"]["version"])
    if project["project"].get("license") != "GPL-3.0-only":
        raise ReleaseError("Release license must remain GPL-3.0-only")
    tag = tag or version.tag
    if Version.from_tag(tag) != version:
        raise ReleaseError(f"Tag {tag} does not match project {version.text}")
    lock = tomllib.loads((root / "uv.lock").read_text())
    packages = [p for p in lock["package"] if p["name"] == "vocal-more"]
    if len(packages) != 1 or packages[0]["version"] != version.text:
        raise ReleaseError("uv.lock project version does not match pyproject.toml")
    notes = root / "docs" / "releases" / f"{version.text}.md"
    if not notes.is_file() or not notes.read_text().strip():
        raise ReleaseError(f"Missing or empty release notes: {notes}")
    return {
        "repository": REPOSITORY, "source_sha": source_sha,
        "version": version.text, "release_tag": tag, "channel": version.channel,
        "feed_tag": version.feed_tag, "notes_sha256": file_hash(notes),
        "dmg_name": f"Vocal-More-{version.text}.dmg",
    }


def read_baseline(path: Path) -> bytes:
    data = path.read_bytes()
    return b"" if data == EMPTY_BASELINE else data
