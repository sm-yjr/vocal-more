"""Read channel state without mutating releases or feeds."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .github import named_asset
from .model import SPARKLE_NS, ReleaseError, Version, sha256


def item_version(item) -> Version:
    """Read one appcast item; its <sparkle:channel> must agree with its version."""
    value = item.findtext(f"{{{SPARKLE_NS}}}version")
    if not value:
        enclosure = item.find("enclosure")
        value = enclosure.get(f"{{{SPARKLE_NS}}}version") if enclosure is not None else None
    if not value:
        raise ReleaseError("Appcast item has no version")
    channel = (item.findtext(f"{{{SPARKLE_NS}}}channel") or "").strip() or "stable"
    if value.isdigit():
        version = Version.parse((item.findtext(f"{{{SPARKLE_NS}}}shortVersionString") or "").strip())
        if version.bundle_version != value:
            raise ReleaseError("Appcast build number disagrees with its display version")
    else:
        version = Version.legacy(value)  # Pre-0.6 stable items, before build numbers.
    if version.channel != channel:
        raise ReleaseError("Cross-channel item in appcast")
    return version


def feed_versions(data: bytes) -> list[Version]:
    if not data:
        return []
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ReleaseError("Unexpected XML declaration in appcast")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ReleaseError("Invalid appcast XML") from exc
    versions = [item_version(item) for item in root.findall("./channel/item")]
    if not versions:
        raise ReleaseError("Existing appcast contains no updates")
    return versions


def newest(versions: list[Version]) -> Version | None:
    return max(versions, key=lambda v: v.order) if versions else None


def channel_feed(api, context: dict) -> tuple[dict | None, dict | None, bytes]:
    release = api.release(context["feed_tag"])
    if release and (release["draft"] or not release["prerelease"]):
        raise ReleaseError("Feed container must be a published prerelease")
    asset = named_asset(release, "appcast.xml")
    return release, asset, api.asset_bytes(asset) if asset else b""


def release_dmg(release: dict, version: Version) -> dict | None:
    # Tags carry no build, so accept the 0.6+ (SemVer) and pre-0.6 (PEP 440) names.
    names = {f"Vocal-More-{version.semver}.dmg", f"Vocal-More-{version.pep440}.dmg"}
    return next((named_asset(release, name) for name in sorted(names) if named_asset(release, name)), None)


def baseline(api, context: dict, *, recovery_feed: bytes | None = None) -> tuple[dict, bytes, dict[str, str]]:
    """Same-channel predecessor (delta source) plus the shared feed it must match."""
    target = Version.parse(context["version"])
    releases = []
    tags = {}
    for release in api.pages("releases"):
        if release["draft"]:
            continue
        try:
            version = Version.from_tag(release["tag_name"])
        except ReleaseError:
            continue
        if version.channel != target.channel:
            continue
        if bool(release["prerelease"]) != bool(version.stage):
            raise ReleaseError("Release prerelease flag disagrees with version")
        if version.release in tags and tags[version.release] != release["tag_name"]:
            raise ReleaseError("Two tags publish the same channel version")
        tags[version.release] = release["tag_name"]
        if version.release == target.release:
            if release["tag_name"] != context["release_tag"]:
                raise ReleaseError("Version already published under another tag")
            continue  # The target may be a partially published release being resumed.
        if version.key > target.key:
            raise ReleaseError("SUPERSEDED: a newer version is already published")
        releases.append((version, release))
    releases.sort(key=lambda pair: pair[0].key)
    _, _, data = channel_feed(api, context)
    if not data and recovery_feed is not None:
        data = recovery_feed
    versions = feed_versions(data)
    others = [v for v in versions if v.release != target.release]
    if others and newest(others).build >= target.build:
        raise ReleaseError("SUPERSEDED: build number must exceed every published build")
    previous = releases[-1] if releases else None
    latest_feed = newest([v for v in others if v.channel == target.channel])
    if (latest_feed.release if latest_feed else None) != (previous[0].release if previous else None):
        raise ReleaseError("PREVIOUS_RELEASE_INCOMPLETE: channel release/feed disagree")
    previous_info = None
    if previous:
        _, release = previous
        asset = release_dmg(release, latest_feed)
        if not asset or asset.get("state") != "uploaded":
            raise ReleaseError("Previous channel DMG is missing")
        digest = asset.get("digest") or "sha256:" + sha256(api.asset_bytes(asset))
        previous_info = {"version": latest_feed.text, "tag": release["tag_name"], "asset": asset, "digest": digest}
    # Asset names carry the SemVer/PEP 440 version, or the build in delta names.
    tag_map = {}
    for release_id, tag in tags.items():
        version = Version(*release_id)
        tag_map[version.semver] = tag_map[version.pep440] = tag
    tag_map[target.semver] = tag_map[target.pep440] = tag_map[target.bundle_version] = context["release_tag"]
    return {"feed_sha256": sha256(data), "previous": previous_info}, data, tag_map


def baseline_key(value: dict) -> tuple:
    previous = value.get("previous")
    return value["feed_sha256"], ((previous["version"], previous["tag"], previous["digest"]) if previous else None)
