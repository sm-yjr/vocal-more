"""Workflow entry points. All writes to GitHub are confined to stage/commit."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import candidate, feed, publish
from .github import GitHub, named_asset
from .model import (
    REPOSITORY,
    SHA,
    ReleaseError,
    Version,
    file_hash,
    read_context,
    write_json,
)
from .state import baseline, baseline_key, channel_feed


def outputs(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as stream:
            for key, value in values.items():
                text = str(value).lower() if isinstance(value, bool) else str(value)
                if "\n" in text or "\r" in text:
                    raise ReleaseError("Invalid workflow output")
                stream.write(f"{key}={text}\n")
    print(json.dumps(values, ensure_ascii=False))


def summary(message: str) -> None:
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as stream:
            stream.write(message + "\n\n")


def context_for(root: Path, tag: str, source_sha: str = "") -> dict:
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if source_sha and (not SHA.fullmatch(source_sha) or actual != source_sha):
        raise ReleaseError("Checkout does not match requested full source SHA")
    subprocess.run(["git", "merge-base", "--is-ancestor", actual, "origin/main"], cwd=root, check=True)
    if os.environ.get("GITHUB_REPOSITORY", REPOSITORY) != REPOSITORY:
        raise ReleaseError("Signing/publication is restricted to the official repository")
    return read_context(root, actual, tag)


def fetch_published(api, directory: Path, context: dict) -> dict:
    release = api.release(context["release_tag"])
    asset = named_asset(release, "manifest.json")
    if not asset:
        raise ReleaseError("Existing release has no candidate snapshot; cannot replace legacy assets")
    raw = api.asset_bytes(asset)
    manifest = json.loads(raw)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ReleaseError("Candidate directory must be empty")
    (directory / "manifest.json").write_bytes(raw)
    for name in manifest["files"]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in {".", "..", "manifest.json"}:
            raise ReleaseError("Invalid published candidate path")
        asset = named_asset(release, name)
        if not asset:
            raise ReleaseError(f"Existing candidate snapshot is incomplete: {name}")
        (directory / name).write_bytes(api.asset_bytes(asset))
    result = candidate.validate(directory, context, allow_expired=not release["draft"])
    candidate.verify_producer(api, result)
    return result


def resolve(api, context: dict, mode: str) -> tuple[str, int, str]:
    existing = api.release(context["release_tag"])
    if existing and existing.get("assets") and mode == "rebuild":
        raise ReleaseError("Cannot rebuild a version with existing release assets; resume its candidate")
    if mode == "rebuild":
        return "full", 0, "EXPLICIT_REBUILD"
    if existing and named_asset(existing, "manifest.json"):
        return "resume", 0, "EXISTING_RELEASE_SNAPSHOT"
    if existing and not existing["draft"]:
        raise ReleaseError("Legacy published version has no candidate snapshot; publish a new version")
    artifacts = api.pages("actions/artifacts", "artifacts")
    artifacts = [a for a in artifacts if not a["expired"] and a["name"].startswith(candidate.artifact_prefix(context))]
    artifacts.sort(key=lambda a: (a["created_at"], a["id"]), reverse=True)
    for artifact in artifacts:
        run = api.api(f"actions/runs/{artifact['workflow_run']['id']}")
        path = run["path"].split("@", 1)[0]
        if path not in candidate.WORKFLOWS or run["status"] != "completed":
            continue
        if run["conclusion"] != "success" and path != ".github/workflows/release.yml":
            continue
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "candidate"
            try:
                manifest = candidate.download(api, artifact["id"], directory, context)
            except ReleaseError as exc:
                if str(exc) == "CANDIDATE_EXPIRED":
                    continue
                raise
            _, _, current_feed = channel_feed(api, context)
            if manifest["release_tag"] != context["release_tag"]:
                if mode == "require-ready":
                    raise ReleaseError("TAG_URL_CHANGED")
                return "refresh", artifact["id"], "TAG_URL_CHANGED"
            if current_feed == (directory / "appcast.xml").read_bytes():
                return "fast", artifact["id"], "ALREADY_PUBLISHED"
            current, _, _ = baseline(api, context)
            if baseline_key(current) != baseline_key(manifest["baseline"]):
                if mode == "require-ready":
                    raise ReleaseError("STALE_BASELINE")
                return "refresh", artifact["id"], "STALE_BASELINE"
            return "fast", artifact["id"], "READY"
    if mode == "require-ready":
        raise ReleaseError("CANDIDATE_NOT_READY")
    return "full", 0, "CANDIDATE_NOT_READY"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "resolve", "fetch", "prepare-feed", "stage", "commit"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tag", default="")
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--mode", choices=["auto", "require-ready", "rebuild"], default="auto")
    parser.add_argument("--artifact-id", type=int, default=0)
    parser.add_argument("--current-run", type=int, default=0)
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--directory", type=Path, default=Path("candidate"))
    parser.add_argument("--backup", type=Path, default=Path("release-backup"))
    args = parser.parse_args()
    try:
        api = GitHub()
        context = context_for(args.root, args.tag, args.source_sha)
        if args.command == "preflight":
            skip = False
            if args.prepare:
                for release in api.pages("releases"):
                    if release["draft"]:
                        continue
                    try:
                        released = Version.from_tag(release["tag_name"])
                    except ReleaseError:
                        continue
                    if released.text == context["version"]:
                        skip = True
                        break
            outputs(**context, skip=skip)
            return
        if args.command == "resolve":
            publish.assert_tag(api, context)
            route, artifact_id, reason = resolve(api, context, args.mode)
            outputs(**context, route=route, artifact_id=artifact_id, reason=reason)
            summary(f"发布通道：**{context['channel']}**；路径：**{route}**；原因：`{reason}`。")
            return
        if args.command == "fetch":
            if args.published:
                fetch_published(api, args.directory, context)
            else:
                candidate.download(api, args.artifact_id, args.directory, context, current_run=args.current_run)
            return
        if args.command == "prepare-feed":
            args.directory.mkdir(parents=True, exist_ok=True)
            origin = None
            build = None
            if args.artifact_id:
                with tempfile.TemporaryDirectory() as temp:
                    source = Path(temp) / "original"
                    original = candidate.download(api, args.artifact_id, source, context)
                    build = original["build"]
                    origin = {"artifact_id": args.artifact_id, "producer": original["producer"],
                              "manifest_sha256": file_hash(source / "manifest.json"), "expires_at": original["expires_at"]}
                    shutil.copy2(source / context["dmg_name"], args.directory / context["dmg_name"])
                    old_report = json.loads((source / "verification.json").read_text())
                    notary = Path(temp) / "notary.json"
                    write_json(notary, old_report["notarization"])
                    subprocess.run(["python3", str(args.root / "packaging/macos/verify_release_artifact.py"),
                                    str(args.directory / context["dmg_name"]), "--report", str(args.directory / "verification.json"),
                                    "--notary-result", str(notary)], check=True)
            else:
                shutil.copy2(args.root / "dist" / context["dmg_name"], args.directory / context["dmg_name"])
                shutil.copy2(args.root / "verification.json", args.directory / "verification.json")
            shutil.copy2(args.root / "docs/releases" / f"{context['version']}.md", args.directory / "release-notes.md")
            state = feed.prepare(api, context, args.directory, args.root)
            manifest = candidate.seal(args.directory, context, state, origin, build=build or candidate.build_metadata(args.root))
            name = candidate.artifact_prefix(context) + f"{manifest['producer']['run_id']}-{manifest['producer']['attempt']}"
            outputs(artifact_name=name, **context)
            summary(f"候选验证通过：`{context['version']}` / `{context['source_sha']}`。上传和 seal-candidate gate 成功后可发布。")
            return
        started = time.monotonic()
        if args.command == "stage":
            publish.stage(api, args.directory, context, args.backup)
            summary("Release 资产已暂存并校验；下一步备份旧 feed，再公开发布。")
        else:
            receipt = publish.commit(api, args.directory, context)
            receipt["commit_execution_seconds"] = round(time.monotonic() - started, 3)
            receipt["route"] = os.environ.get("ROUTE")
            receipt["reason"] = os.environ.get("ROUTE_REASON")
            receipt["candidate_artifact_id"] = os.environ.get("CANDIDATE_ID")
            receipt["publication_run_id"] = os.environ.get("GITHUB_RUN_ID")
            end = datetime.fromisoformat(receipt["published_at"])
            if started_at := os.environ.get("PUBLISH_STARTED_AT"):
                receipt["publish_execution_seconds"] = round((end - datetime.fromisoformat(started_at)).total_seconds(), 3)
            sealed = json.loads((args.directory / "manifest.json").read_text())
            if build_start := sealed["build"].get("started_at"):
                receipt["prepare_seconds"] = round((datetime.fromisoformat(sealed["build"]["completed_at"]) - datetime.fromisoformat(build_start)).total_seconds(), 3)
                receipt["prepare_to_published_seconds"] = round((end - datetime.fromisoformat(build_start)).total_seconds(), 3)
            # A metrics lookup failure must not turn a verified publication into a failure.
            try:
                run = api.api(f"actions/runs/{os.environ['GITHUB_RUN_ID']}")
                created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                receipt["run_to_published_seconds"] = round((end - created).total_seconds(), 3)
                if run["event"] == "push":
                    receipt["tag_trigger_to_published_seconds"] = receipt["run_to_published_seconds"]
                run_start = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
                receipt["initial_queue_seconds"] = max(0, round((run_start - created).total_seconds(), 3))
            except (ReleaseError, KeyError, TypeError, ValueError) as exc:
                receipt["metrics_error"] = str(exc)
            write_json(args.root / "release-receipt.json", receipt)
            summary(f"发布完成：[{context['release_tag']}](https://github.com/{REPOSITORY}/releases/tag/{context['release_tag']})，通道 **{context['channel']}**；公开资产和签名 feed 已读回验证。")
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        summary(f"发布停止：`{exc}`")
        parser.exit(1, f"{exc}\n")
