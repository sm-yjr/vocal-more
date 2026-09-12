"""Idempotent, channel-scoped publication with recoverable feed replacement."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from .candidate import validate
from .github import named_asset
from .model import ReleaseError, Version, read_baseline, sha256, timestamp, write_json
from .state import baseline, baseline_key, channel_feed, feed_versions


def assert_tag(api, context: dict) -> None:
    if api.tag_sha(context["release_tag"]) != context["source_sha"]:
        raise ReleaseError("Release tag moved or points to another source SHA")


def ensure_asset(api, tag: str, path: Path) -> None:
    release = api.release(tag)
    asset = named_asset(release, path.name)
    if asset is None:
        try:
            api.upload(tag, path)
        except Exception:
            # A timed-out upload may have succeeded. Never blindly repeat or clobber it.
            asset = named_asset(api.release(tag), path.name)
            if asset is None:
                raise
    asset = named_asset(api.release(tag), path.name)
    if asset is None:
        raise ReleaseError(f"Upload not visible: {path.name}")
    api.verify_asset(asset, path)


def public_read(url: str, expected_hash: str | None = None) -> None:
    error = None
    for delay in (0, 1, 2, 4):
        if delay:
            time.sleep(delay)
        try:
            headers = {"Cache-Control": "no-cache"}
            if expected_hash is None:
                headers["Range"] = "bytes=0-0"
            with urlopen(Request(url, headers=headers), timeout=10) as response:
                data = response.read() if expected_hash else response.read(1)
            if not data or (expected_hash and sha256(data) != expected_hash):
                raise ReleaseError("Public URL content has not converged")
            return
        except (OSError, ReleaseError) as exc:
            error = exc
    raise ReleaseError(f"Public asset not ready: {url}: {error}")


def verify_remote_payload(api, directory: Path, context: dict, manifest: dict) -> dict:
    release = api.release(context["release_tag"])
    if release is None:
        raise ReleaseError("Target release is missing")
    for name in ["manifest.json", *manifest["files"]]:
        asset = named_asset(release, name)
        if asset is None:
            raise ReleaseError(f"Published candidate snapshot is incomplete: {name}")
        api.verify_asset(asset, directory / name)
    if bool(release["prerelease"]) != (context["channel"] != "stable"):
        raise ReleaseError("Release channel flag mismatch")
    return release


def check_baseline(api, directory: Path, context: dict, manifest: dict) -> bool:
    _, _, data = channel_feed(api, context)
    expected = manifest["files"]["appcast.xml"]["sha256"]
    if data and sha256(data) == expected:
        return True
    versions = feed_versions(data, context["channel"])
    target = Version.parse(context["version"])
    if versions and max(versions, key=lambda v: v.key).key >= target.key:
        raise ReleaseError("SUPERSEDED_OR_CONFLICTING_FEED")
    release = api.release(context["release_tag"])
    recovery = None
    if not data and release and not release["draft"]:
        # A killed publisher may have deleted the old feed. Prove ownership from
        # the complete immutable snapshot before using its backup to recover.
        verify_remote_payload(api, directory, context, manifest)
        recovery = read_baseline(directory / "baseline-appcast.xml")
    current, _, _ = baseline(api, context, recovery_feed=recovery)
    if baseline_key(current) != baseline_key(manifest["baseline"]):
        raise ReleaseError("STALE_BASELINE")
    return False


def stage(api, directory: Path, context: dict, backup: Path) -> dict:
    existing = api.release(context["release_tag"])
    manifest = validate(directory, context, allow_expired=bool(existing and not existing["draft"]))
    if manifest["release_tag"] != context["release_tag"]:
        raise ReleaseError("Candidate was signed for a different tag URL")
    assert_tag(api, context)
    check_baseline(api, directory, context, manifest)
    if existing is None:
        api.api("releases", method="POST", data={
            "tag_name": context["release_tag"], "target_commitish": context["source_sha"],
            "name": f"Vocal More {context['version']}", "body": (directory / "release-notes.md").read_text(),
            "draft": True, "prerelease": context["channel"] != "stable", "make_latest": "false",
        })
    elif not existing["draft"]:
        verify_remote_payload(api, directory, context, manifest)
    # Read once, verify existing bytes, then upload independent missing assets.
    # Seal with manifest last so a partial draft cannot masquerade as a snapshot.
    remote = api.release(context["release_tag"])
    missing = []
    for name in manifest["files"]:
        path = directory / name
        asset = named_asset(remote, name)
        if asset:
            api.verify_asset(asset, path)
        else:
            missing.append(path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(api.upload, context["release_tag"], path) for path in missing]
        for future in futures:
            try:
                future.result()
            except (ReleaseError, subprocess.CalledProcessError):
                pass  # Resolve uncertain uploads from the refreshed remote hashes below.
    remote = api.release(context["release_tag"])
    for name in manifest["files"]:
        asset = named_asset(remote, name)
        if asset is None:
            raise ReleaseError(f"Upload not visible: {name}")
        api.verify_asset(asset, directory / name)
    ensure_asset(api, context["release_tag"], directory / "manifest.json")
    verify_remote_payload(api, directory, context, manifest)
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(directory / "baseline-appcast.xml", backup / "appcast.xml")
    write_json(backup / "recovery.json", {**context, "baseline": manifest["baseline"], "prepared_at": timestamp()})
    return manifest


def commit(api, directory: Path, context: dict, *, read_public=public_read) -> dict:
    release = api.release(context["release_tag"])
    manifest = validate(directory, context, allow_expired=bool(release and not release["draft"]))
    if manifest["release_tag"] != context["release_tag"]:
        raise ReleaseError("Candidate was signed for a different tag URL")
    assert_tag(api, context)
    release = verify_remote_payload(api, directory, context, manifest)
    already_current = check_baseline(api, directory, context, manifest)
    if release["draft"]:
        api.api(f"releases/{release['id']}", method="PATCH", data={
            "draft": False, "prerelease": context["channel"] != "stable",
            "make_latest": "true" if context["channel"] == "stable" else "false",
        })
    for name in manifest["files"]:
        if name.endswith((".dmg", ".delta")):
            read_public(f"https://github.com/{api.repository}/releases/download/{context['release_tag']}/{name}")
    expected_hash = manifest["files"]["appcast.xml"]["sha256"]
    if not already_current:
        feed_release, _, _ = channel_feed(api, context)
        if feed_release is None:
            api.api("releases", method="POST", data={
                "tag_name": context["feed_tag"], "target_commitish": context["source_sha"],
                "name": f"Sparkle {context['channel']} update feed", "draft": False,
                "prerelease": True, "make_latest": "false", "body": "Signed Vocal More update metadata.",
            })
        # Upload before removing the live asset. Rename is still not an atomic
        # swap, but a failed large/network upload cannot remove the working feed.
        staged_path = directory.parent / f"appcast-{expected_hash}.xml"
        shutil.copy2(directory / "appcast.xml", staged_path)
        ensure_asset(api, context["feed_tag"], staged_path)
        assert_tag(api, context)
        check_baseline(api, directory, context, manifest)
        feed_release, old_asset, old_data = channel_feed(api, context)
        staged = named_asset(feed_release, staged_path.name)
        if staged is None:
            raise ReleaseError("Staged feed is missing")
        try:
            if old_asset:
                api.api(f"releases/assets/{old_asset['id']}", method="DELETE")
            api.api(f"releases/assets/{staged['id']}", method="PATCH", data={"name": "appcast.xml"})
        except Exception:
            _, live, live_data = channel_feed(api, context)
            if live_data and sha256(live_data) == expected_hash:
                pass  # An uncertain rename succeeded; finish by reading it back.
            elif live is None:
                restore = directory.parent / "feed-restore"
                restore.mkdir(exist_ok=True)
                payload = old_data or read_baseline(directory / "baseline-appcast.xml")
                if payload:
                    (restore / "appcast.xml").write_bytes(payload)
                    ensure_asset(api, context["feed_tag"], restore / "appcast.xml")
                raise
            else:
                raise ReleaseError("Feed changed externally; refused to overwrite it")
    _, _, final = channel_feed(api, context)
    if sha256(final) != expected_hash:
        raise ReleaseError("RELEASE_PUBLISHED_FEED_PENDING")
    read_public(Version.parse(context["version"]).feed_url, expected_hash)
    return {**context, "status": "PUBLISHED", "published_at": timestamp(),
            "producer": manifest["producer"], "files": manifest["files"],
            "notarization": json.loads((directory / "verification.json").read_text()).get("notarization")}
