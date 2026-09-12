"""Seal and verify immutable candidates, including their GitHub provenance."""

from __future__ import annotations

import io
import json
import os
import platform
import re
import stat
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from .model import (
    POLICY,
    REPOSITORY,
    SCHEMA,
    ReleaseError,
    Version,
    file_hash,
    read_baseline,
    sha256,
    write_json,
)

WORKFLOWS = {".github/workflows/release-prepare.yml", ".github/workflows/release.yml"}
REQUIRED = {"release-notes.md", "verification.json", "appcast.xml", "baseline-appcast.xml"}


def artifact_prefix(context: dict) -> str:
    return f"release-candidate-{context['version']}-{context['source_sha']}-"


def build_metadata(root: Path) -> dict:
    paths = ["pyproject.toml", "uv.lock", "frontend/settings/package-lock.json", "rust/Cargo.lock", "rust/rust-toolchain.toml", "scripts/build_rust_host.sh", "scripts/build_native_audio.sh",
             ".github/workflows/release.yml", ".github/workflows/release-prepare.yml", ".github/workflows/_release-candidate.yml"]
    paths += [p.relative_to(root).as_posix() for p in (root / "packaging").rglob("*")
              if p.is_file() and p.suffix in {".py", ".sh"} and not any(part.startswith(".") for part in p.relative_to(root).parts)]
    versions = {}
    for name, command in {
        "python": [os.environ.get("VOCAL_MORE_BUILD_PYTHON", "python3"), "--version"],
        "uv": ["uv", "--version"], "rust": ["rustc", "--version"], "node": ["node", "--version"],
    }.items():
        versions[name] = subprocess.check_output(command, text=True).strip()
    return {
        "runner_os": os.environ.get("RUNNER_OS"), "image_version": os.environ.get("ImageVersion"),
        "architecture": platform.machine(), "minimum_macos": "14.0", "sparkle": "2.9.4",
        "tools": versions, "inputs_sha256": {name: file_hash(root / name) for name in sorted(paths) if (root / name).is_file()},
        "started_at": os.environ.get("PREPARE_STARTED_AT"),
    }


def seal(directory: Path, context: dict, baseline: dict, origin: dict | None = None, *, build: dict | None = None) -> dict:
    report = json.loads((directory / "verification.json").read_text())
    if (report.get("status") != "passed" or report.get("dmg_sha256") != file_hash(directory / context["dmg_name"])
            or report.get("notarization", {}).get("status") != "Accepted" or not report.get("notarization", {}).get("id")):
        raise ReleaseError("Candidate lacks successful final DMG verification")
    if report.get("version") != context["version"] or report.get("channel") != context["channel"]:
        raise ReleaseError("Verified bundle version/channel differs from source")
    workflow_ref = os.environ["GITHUB_WORKFLOW_REF"]
    workflow_path = workflow_ref.split("@", 1)[0].removeprefix(REPOSITORY + "/")
    if workflow_path not in WORKFLOWS or os.environ["GITHUB_REPOSITORY"] != REPOSITORY:
        raise ReleaseError("Candidate must be produced by an approved repository workflow")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=14)
    if origin:
        expires = min(expires, datetime.fromisoformat(origin["expires_at"]))
    files = {}
    for path in sorted(directory.iterdir()):
        if path.name == "manifest.json":
            continue
        if path.is_symlink() or not path.is_file():
            raise ReleaseError("Candidate payload must contain only regular, flat files")
        files[path.name] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    build = dict(build or {})
    build.setdefault("completed_at", now.isoformat())
    manifest = {
        "schema_version": SCHEMA, "policy": POLICY, **context,
        "producer": {"workflow_path": workflow_path, "workflow_head_sha": os.environ["GITHUB_SHA"],
                     "run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                     "event": os.environ["GITHUB_EVENT_NAME"]},
        "prepared_at": now.isoformat(), "expires_at": expires.isoformat(),
        "baseline": baseline, "files": files, "origin_candidate": origin,
        "build": build,
    }
    write_json(directory / "manifest.json", manifest)
    validate(directory, context)
    return manifest


def validate(directory: Path, context: dict, *, allow_expired: bool = False) -> dict:
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["schema_version"] != SCHEMA or manifest["policy"] != POLICY:
            raise ReleaseError("Unsupported candidate schema/verification policy")
        for key in ("repository", "source_sha", "version", "channel", "feed_tag", "dmg_name", "notes_sha256"):
            if manifest[key] != context[key]:
                raise ReleaseError(f"Candidate identity mismatch: {key}")
        version = Version.parse(manifest["version"])
        if Version.from_tag(manifest["release_tag"]) != version:
            raise ReleaseError("Candidate tag/version mismatch")
        if manifest["feed_tag"] != version.feed_tag:
            raise ReleaseError("Candidate channel/feed mismatch")
        expires = datetime.fromisoformat(manifest["expires_at"])
        prepared = datetime.fromisoformat(manifest["prepared_at"])
        now = datetime.now(timezone.utc)
        if prepared > now + timedelta(minutes=5) or expires > prepared + timedelta(days=14):
            raise ReleaseError("Invalid candidate lifetime")
        if not allow_expired and now >= expires:
            raise ReleaseError("CANDIDATE_EXPIRED")
        files = manifest["files"]
        required = REQUIRED | {context["dmg_name"]}
        if not required.issubset(files):
            raise ReleaseError("Candidate is missing required payload files")
        expected = set(files) | {"manifest.json"}
        if {p.name for p in directory.iterdir()} != expected:
            raise ReleaseError("Unlisted candidate files")
        for name, entry in files.items():
            if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in {".", "..", "manifest.json"}:
                raise ReleaseError("Invalid candidate path")
            if name not in required and not name.endswith(".delta"):
                raise ReleaseError("Unexpected candidate file role")
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"] or file_hash(path) != entry["sha256"]:
                raise ReleaseError(f"Candidate file digest mismatch: {name}")
        if files["release-notes.md"]["sha256"] != context["notes_sha256"]:
            raise ReleaseError("Release notes differ from tagged source")
        if sha256(read_baseline(directory / "baseline-appcast.xml")) != manifest["baseline"]["feed_sha256"]:
            raise ReleaseError("Baseline feed digest mismatch")
        report = json.loads((directory / "verification.json").read_text())
        if (report.get("status") != "passed" or report.get("version") != version.text
                or report.get("channel") != version.channel
                or report.get("notarization", {}).get("status") != "Accepted" or not report.get("notarization", {}).get("id")
                or report.get("dmg_sha256") != files[context["dmg_name"]]["sha256"]):
            raise ReleaseError("Candidate verification report mismatch")
        from .feed import validate_appcast
        validate_appcast(directory, manifest)
        return manifest
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ReleaseError(f"Malformed candidate: {exc}") from exc


def verify_producer(api, manifest: dict, *, current_run: int = 0) -> None:
    producer = manifest["producer"]
    run = api.api(f"actions/runs/{producer['run_id']}/attempts/{producer['attempt']}")
    if run["head_repository"]["full_name"] != REPOSITORY or run["path"].split("@", 1)[0] not in WORKFLOWS:
        raise ReleaseError("Untrusted candidate workflow")
    if (run["path"].split("@", 1)[0] != producer["workflow_path"]
            or run["head_sha"] != producer["workflow_head_sha"]
            or run["event"] != producer["event"]
            or run["event"] not in {"push", "workflow_dispatch"}
            or run["run_attempt"] != producer["attempt"]):
        raise ReleaseError("Candidate run identity mismatch")
    if int(run["id"]) == current_run or (run["path"].split("@", 1)[0] == ".github/workflows/release.yml" and run["status"] == "completed"):
        # A fallback producer can finish with publication failure after its build gate
        # succeeded. Its immutable candidate remains eligible for a later resume.
        jobs = api.pages(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
        if not any(j["name"].endswith("seal-candidate") and j["conclusion"] == "success" for j in jobs):
            raise ReleaseError("Current run has no successful candidate gate")
    elif run["status"] != "completed" or run["conclusion"] != "success":
        raise ReleaseError("Candidate workflow has not completed successfully")


def unpack(data: bytes, digest: str, directory: Path) -> None:
    if digest != "sha256:" + sha256(data):
        raise ReleaseError("Artifact archive digest mismatch")
    if directory.exists() and any(directory.iterdir()):
        raise ReleaseError("Candidate download directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        seen = set()
        total = 0
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (entry.is_dir() or len(path.parts) != 1 or path.name != entry.filename or "\\" in entry.filename
                    or path.is_absolute() or entry.filename in {".", ".."}
                    or entry.filename in seen or stat.S_ISLNK(mode)):
                raise ReleaseError("Unsafe or duplicate artifact entry")
            seen.add(entry.filename)
            total += entry.file_size
            if total > 1024 * 1024 * 1024:
                raise ReleaseError("Candidate exceeds 1 GiB size limit")
        for entry in archive.infolist():
            (directory / entry.filename).write_bytes(archive.read(entry))


def download(api, artifact_id: int, directory: Path, context: dict, *, current_run: int = 0) -> dict:
    artifact = api.api(f"actions/artifacts/{artifact_id}")
    if artifact["expired"]:
        raise ReleaseError("CANDIDATE_EXPIRED")
    if not artifact["name"].startswith(artifact_prefix(context)):
        raise ReleaseError("Artifact name does not match candidate identity")
    data = api.api(f"actions/artifacts/{artifact_id}/zip", binary=True)
    unpack(data, artifact.get("digest", ""), directory)
    manifest = validate(directory, context)
    producer = manifest["producer"]
    expected_name = artifact_prefix(context) + f"{producer['run_id']}-{producer['attempt']}"
    if artifact["name"] != expected_name or artifact["workflow_run"]["id"] != producer["run_id"]:
        raise ReleaseError("Artifact is not owned by its claimed producer")
    verify_producer(api, manifest, current_run=current_run)
    return manifest
