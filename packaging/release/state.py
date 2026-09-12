"""Read channel state without mutating releases or feeds."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .github import named_asset
from .model import SPARKLE_NS, ReleaseError, Version, sha256


def feed_versions(data: bytes, channel: str) -> list[Version]:
    if not data:
        return []
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ReleaseError("Unexpected XML declaration in appcast")
    try:
        root = ET.fromstring(data)
        versions = []
        for item in root.findall("./channel/item"):
            value = item.findtext(f"{{{SPARKLE_NS}}}version")
            if not value:
                enclosure = item.find("enclosure")
                value = enclosure.get(f"{{{SPARKLE_NS}}}version") if enclosure is not None else None
            if not value:
                raise ReleaseError("Appcast item has no version")
            version = Version.parse(value)
            if version.channel != channel:
                raise ReleaseError("Cross-channel item in appcast")
            versions.append(version)
        if not versions:
            raise ReleaseError("Existing appcast contains no updates")
        return versions
    except ET.ParseError as exc:
        raise ReleaseError("Invalid appcast XML") from exc


def channel_feed(api, context: dict) -> tuple[dict | None, dict | None, bytes]:
    release = api.release(context["feed_tag"])
    if release and (release["draft"] or not release["prerelease"]):
        raise ReleaseError("Feed container must be a published prerelease")
    asset = named_asset(release, "appcast.xml")
    return release, asset, api.asset_bytes(asset) if asset else b""


def baseline(api, context: dict, *, recovery_feed: bytes | None = None) -> tuple[dict, bytes, dict[str, str]]:
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
        if version.text in tags and tags[version.text] != release["tag_name"]:
            raise ReleaseError("Two tags publish the same channel version")
        tags[version.text] = release["tag_name"]
        if version == target:
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
    versions = feed_versions(data, target.channel)
    previous = releases[-1] if releases else None
    latest_feed = max(versions, key=lambda v: v.key) if versions else None
    if latest_feed != (previous[0] if previous else None):
        raise ReleaseError("PREVIOUS_RELEASE_INCOMPLETE: channel release/feed disagree")
    previous_info = None
    if previous:
        version, release = previous
        asset = named_asset(release, f"Vocal-More-{version.text}.dmg")
        if not asset or asset.get("state") != "uploaded":
            raise ReleaseError("Previous channel DMG is missing")
        digest = asset.get("digest") or "sha256:" + sha256(api.asset_bytes(asset))
        previous_info = {"version": version.text, "tag": release["tag_name"], "asset": asset, "digest": digest}
    tags[target.text] = context["release_tag"]
    return {"feed_sha256": sha256(data), "previous": previous_info}, data, tags


def baseline_key(value: dict) -> tuple:
    previous = value.get("previous")
    return value["feed_sha256"], ((previous["version"], previous["tag"], previous["digest"]) if previous else None)
