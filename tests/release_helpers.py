"""Offline release fixtures; no test writes to GitHub or invokes signing tools."""

from __future__ import annotations

import base64
import copy
from urllib.parse import unquote

from release import candidate
from release.github import APIError, GitHub
from release.model import (
    EMPTY_BASELINE,
    REPOSITORY,
    SPARKLE_NS,
    Version,
    sha256,
    write_json,
)

SOURCE = "a" * 40
SIGNATURE = base64.b64encode(b"s" * 64).decode()


def item_for(version: str, tag: str | None = None, *, size: int = 3, previous: str | None = None) -> str:
    value = Version.any(version)
    prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag or value.tag}/"
    delta = ""
    if previous:
        delta = (f'<sparkle:deltas><enclosure url="{prefix}update.delta" length="5" sparkle:edSignature="{SIGNATURE}" '
                 f'sparkle:deltaFrom="{Version.any(previous).bundle_version}"/></sparkle:deltas>')
    channel = "<sparkle:channel>beta</sparkle:channel>" if value.channel == "beta" else ""
    short = f"<sparkle:shortVersionString>{value.text}</sparkle:shortVersionString>" if value.build else ""
    return (f'<item><sparkle:version>{value.bundle_version}</sparkle:version>{channel}{short}'
            f'<enclosure url="{prefix}{value.dmg_name}" length="{size}" sparkle:edSignature="{SIGNATURE}"/>'
            f'{delta}</item>')


def xml_for(version: str, tag: str | None = None, *, size: int = 3, previous: str | None = None, keep: bytes = b"") -> bytes:
    """A feed whose newest item is `version`, after the items of feed `keep`."""
    kept = ""
    if keep:
        text = keep.decode()
        kept = text[text.index("<channel>") + len("<channel>"):text.index("</channel>")]
    return (f'<rss xmlns:sparkle="{SPARKLE_NS}"><channel>{kept}{item_for(version, tag, size=size, previous=previous)}'
            f'</channel></rss>').encode()


def make_candidate(tmp_path, monkeypatch, version="0.6.1-beta.1+2", *, previous=None, old_feed=b""):
    value = Version.parse(version)
    context = {"repository": REPOSITORY, "source_sha": SOURCE, "version": version, "release_tag": value.tag,
               "channel": value.channel, "feed_tag": value.feed_tag, "dmg_name": value.dmg_name,
               "notes_sha256": sha256(b"release notes")}
    directory = tmp_path / "candidate"
    directory.mkdir(parents=True)
    (directory / context["dmg_name"]).write_bytes(b"dmg")
    (directory / "release-notes.md").write_bytes(b"release notes")
    (directory / "baseline-appcast.xml").write_bytes(old_feed or EMPTY_BASELINE)
    write_json(directory / "verification.json", {"status": "passed", "version": version, "channel": value.channel,
               "dmg_sha256": sha256(b"dmg"), "notarization": {"id": "test-submission", "status": "Accepted"}})
    (directory / "appcast.xml").write_bytes(xml_for(version, previous=previous["version"] if previous else None, keep=old_feed))
    if previous:
        (directory / "update.delta").write_bytes(b"delta")
    for key, val in {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/release-prepare.yml@refs/heads/main",
                     "GITHUB_SHA": SOURCE, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "push"}.items():
        monkeypatch.setenv(key, val)
    manifest = candidate.seal(directory, context, {"feed_sha256": sha256(old_feed), "previous": previous})
    return context, directory, manifest


class FakeGitHub(GitHub):
    repository = REPOSITORY

    def __init__(self):
        self.releases = {}
        self.content = {}
        self.next_id = 1
        self.writes = []
        self.source_sha = SOURCE
        self.rename_failures = 0

    def add_release(self, tag, *, draft=False, prerelease=False):
        result = {"id": self.next_id, "tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": []}
        self.next_id += 1
        self.releases[tag] = result
        return result

    def add_asset(self, tag, name, data):
        asset = {"id": self.next_id, "name": name, "size": len(data), "digest": "sha256:" + sha256(data), "state": "uploaded"}
        self.next_id += 1
        self.releases[tag]["assets"].append(asset)
        self.content[asset["id"]] = data
        return asset

    def pages(self, path, key=None):
        if path == "releases":
            return copy.deepcopy(list(self.releases.values()))
        if path.endswith("/jobs"):
            return [{"name": "prepare / seal-candidate", "conclusion": "success"}]
        return []

    def tag_sha(self, tag):
        return self.source_sha

    def asset_bytes(self, asset):
        return self.content[asset["id"]]

    def verify_asset(self, asset, path):
        return GitHub.verify_asset(self, asset, path)

    def upload(self, tag, path):
        assert path.stat().st_size > 0, "Release snapshots must not upload empty assets"
        self.writes.append(("upload", tag, path.name))
        if any(a["name"] == path.name for a in self.releases[tag]["assets"]):
            raise RuntimeError("Duplicate upload")
        self.add_asset(tag, path.name, path.read_bytes())

    def api(self, path, *, method="GET", data=None, binary=False):
        # Keep production release()/optional() in the test path. The real tag
        # endpoint cannot see drafts; only the authenticated listing can.
        if path.startswith("releases/tags/") and method == "GET":
            release = self.releases.get(unquote(path.removeprefix("releases/tags/")))
            if release is None or release["draft"]:
                raise APIError("Release not found", 404)
            return copy.deepcopy(release)
        if path.startswith("actions/runs/"):
            return {"id": 123, "head_repository": {"full_name": REPOSITORY}, "path": ".github/workflows/release-prepare.yml",
                    "head_sha": SOURCE, "event": "push", "run_attempt": 1, "status": "completed", "conclusion": "success"}
        self.writes.append((method, path, data))
        if path == "releases" and method == "POST":
            return self.add_release(data["tag_name"], draft=data["draft"], prerelease=data["prerelease"])
        if path.startswith("releases/assets/"):
            asset_id = int(path.rsplit("/", 1)[-1])
            for release in self.releases.values():
                for asset in release["assets"]:
                    if asset["id"] == asset_id:
                        if method == "DELETE":
                            release["assets"].remove(asset)
                            return None
                        if method == "PATCH":
                            if self.rename_failures:
                                self.rename_failures -= 1
                                raise RuntimeError("Injected feed rename failure")
                            asset.update(data)
                            return copy.deepcopy(asset)
        elif path.startswith("releases/") and method == "PATCH":
            for release in self.releases.values():
                if release["id"] == int(path.rsplit("/", 1)[-1]):
                    release.update(data)
                    return copy.deepcopy(release)
        raise AssertionError((path, method, data))


def previous_release(api, version):
    """Publish `version` and append it to the shared feed; returns (previous, feed)."""
    value = Version.any(version)
    api.add_release(value.tag, prerelease=bool(value.stage))
    asset = api.add_asset(value.tag, value.dmg_name, b"old")
    feed = api.releases.get(value.feed_tag) or api.add_release(value.feed_tag, prerelease=True)
    live = next((a for a in feed["assets"] if a["name"] == "appcast.xml"), None)
    old_feed = xml_for(version, keep=api.content[live["id"]] if live else b"")
    if live:
        api.content[live["id"]] = old_feed
        live["size"], live["digest"] = len(old_feed), "sha256:" + sha256(old_feed)
    else:
        api.add_asset(value.feed_tag, "appcast.xml", old_feed)
    return {"version": value.text, "tag": value.tag, "asset": asset, "digest": asset["digest"]}, old_feed
