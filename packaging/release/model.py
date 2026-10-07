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
# pyproject.toml [project].version: PEP 440 stable or beta. Alpha is retired.
PROJECT = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:b([1-9]\d*))?")
# Product version shown to users and stored in VocalMoreVersion: X.Y.Z+B or X.Y.Z-beta.N+B.
DISPLAY = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-beta\.([1-9]\d*))?\+([1-9]\d*)")
# Releases before 0.6.0 carried no build number: X.Y.Z, X.Y.ZaN or X.Y.ZbN.
LEGACY = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:([ab])([1-9]\d*))?")
SHA = re.compile(r"[0-9a-f]{40}")
# One signed feed serves every build; beta items carry <sparkle:channel>beta</sparkle:channel>.
FEED_TAG = "sparkle-feed"
# Feeds that pre-0.6 alpha/beta builds still poll. They mirror the main feed,
# whose beta items those builds ignore, so they upgrade straight to stable.
LEGACY_FEED_TAGS = ("sparkle-feed-alpha", "sparkle-feed-beta")
BETA = "beta"


class ReleaseError(RuntimeError):
    """A release must stop, rather than silently select another artifact."""


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    stage: str = ""  # "" stable, "b" beta; "a" only for retired pre-0.6 alphas
    number: int = 0
    build: int = 0  # 0 only for pre-0.6 releases and versions read from tags

    @classmethod
    def project(cls, value: str, build: object) -> Version:
        """The version pyproject.toml declares, with its build number."""
        match = PROJECT.fullmatch(value)
        if not match:
            raise ReleaseError(f"Unsupported project version: {value!r}; use X.Y.Z or X.Y.ZbN (alpha is retired)")
        if type(build) is not int or build < 1:
            raise ReleaseError("[tool.vocal-more].build must be a positive integer")
        major, minor, patch, number = match.groups()
        return cls._checked(int(major), int(minor), int(patch), "b" if number else "", int(number or 0), build)

    @classmethod
    def parse(cls, value: str) -> Version:
        """A product version: X.Y.Z+B or X.Y.Z-beta.N+B."""
        match = DISPLAY.fullmatch(value)
        if not match:
            raise ReleaseError(f"Unsupported product version: {value!r}; use X.Y.Z+B or X.Y.Z-beta.N+B")
        major, minor, patch, number, build = match.groups()
        return cls._checked(int(major), int(minor), int(patch), "b" if number else "", int(number or 0), int(build))

    @classmethod
    def legacy(cls, value: str) -> Version:
        """A pre-0.6 version, which had no build number."""
        match = LEGACY.fullmatch(value)
        if not match:
            raise ReleaseError(f"Unsupported legacy version: {value!r}")
        major, minor, patch, stage, number = match.groups()
        return cls._checked(int(major), int(minor), int(patch), stage or "", int(number or 0), 0)

    @classmethod
    def any(cls, value: str) -> Version:
        return cls.parse(value) if "+" in value else cls.legacy(value)

    @classmethod
    def from_tag(cls, tag: str) -> Version:
        """Release identity from a tag; tags carry no build number."""
        value = tag.removeprefix("v")
        value = re.sub(r"-alpha\.([1-9]\d*)$", r"a\1", value)
        value = re.sub(r"-beta\.([1-9]\d*)$", r"b\1", value)
        return cls.legacy(value)

    @classmethod
    def _checked(cls, major, minor, patch, stage, number, build) -> Version:
        if number > 255:
            raise ReleaseError("Beta sequence must be in 1..255")
        return cls(major, minor, patch, stage, number, build)

    @property
    def base(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def pep440(self) -> str:
        return self.base + (f"{self.stage}{self.number}" if self.stage else "")

    @property
    def semver(self) -> str:
        return self.base + (f"-{self.channel}.{self.number}" if self.stage else "")

    @property
    def text(self) -> str:
        return f"{self.semver}+{self.build}" if self.build else self.pep440

    @property
    def release(self) -> tuple:
        """Identity without the build number, for comparing with tags."""
        return self.major, self.minor, self.patch, self.stage, self.number

    @property
    def channel(self) -> str:
        return {"": "stable", "a": "alpha", "b": BETA}[self.stage]

    @property
    def tag(self) -> str:
        return f"v{self.semver}"

    @property
    def key(self) -> tuple[int, ...]:
        return self.major, self.minor, self.patch, {"a": 0, "b": 1, "": 2}[self.stage], self.number

    @property
    def order(self) -> tuple:
        """Feed order: Sparkle compares build numbers; pre-0.6 releases sort first."""
        return self.build, self.key

    @property
    def bundle_version(self) -> str:
        """CFBundleVersion and sparkle:version."""
        return str(self.build) if self.build else self.pep440

    @property
    def dmg_name(self) -> str:
        return f"Vocal-More-{self.semver if self.build else self.pep440}.dmg"

    @property
    def notes_name(self) -> str:
        return f"{self.semver}.md"

    @property
    def feed_tag(self) -> str:
        return FEED_TAG

    @property
    def feed_url(self) -> str:
        return feed_url(FEED_TAG)


def feed_url(tag: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/appcast.xml"


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
    version = Version.project(project["project"]["version"], project.get("tool", {}).get("vocal-more", {}).get("build"))
    if project["project"].get("license") != "GPL-3.0-only":
        raise ReleaseError("Release license must remain GPL-3.0-only")
    tag = tag or version.tag
    if Version.from_tag(tag).release != version.release:
        raise ReleaseError(f"Tag {tag} does not match project {version.text}")
    lock = tomllib.loads((root / "uv.lock").read_text())
    packages = [p for p in lock["package"] if p["name"] == "vocal-more"]
    if len(packages) != 1 or packages[0]["version"] != version.pep440:
        raise ReleaseError("uv.lock project version does not match pyproject.toml")
    notes = root / "docs" / "releases" / version.notes_name
    if not notes.is_file() or not notes.read_text().strip():
        raise ReleaseError(f"Missing or empty release notes: {notes}")
    return {
        "repository": REPOSITORY, "source_sha": source_sha,
        "version": version.text, "release_tag": tag, "channel": version.channel,
        "feed_tag": version.feed_tag, "notes_sha256": file_hash(notes),
        "dmg_name": version.dmg_name,
    }


def read_baseline(path: Path) -> bytes:
    data = path.read_bytes()
    return b"" if data == EMPTY_BASELINE else data
