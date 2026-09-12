"""Offline release fixtures; no test writes to GitHub or invokes signing tools."""

from __future__ import annotations

import base64
import copy

from release import candidate
from release.github import GitHub
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


def xml_for(version: str, tag: str | None = None, *, size: int = 3, previous: str | None = None) -> bytes:
    value = Version.parse(version)
    prefix = f"https://github.com/{REPOSITORY}/releases/download/{tag or value.tag}/"
    delta = ""
    if previous:
        delta = f'<sparkle:deltas><enclosure url="{prefix}update.delta" length="5" sparkle:edSignature="{SIGNATURE}" sparkle:deltaFrom="{previous}"/></sparkle:deltas>'
    return (f'<rss xmlns:sparkle="{SPARKLE_NS}"><channel><item><sparkle:version>{version}</sparkle:version>'
            f'<enclosure url="{prefix}Vocal-More-{version}.dmg" length="{size}" sparkle:edSignature="{SIGNATURE}"/>'
            f'{delta}</item></channel></rss>').encode()


def make_candidate(tmp_path, monkeypatch, version="0.4.18a1", *, previous=None, old_feed=b""):
    value = Version.parse(version)
    context = {"repository": REPOSITORY, "source_sha": SOURCE, "version": version, "release_tag": value.tag,
               "channel": value.channel, "feed_tag": value.feed_tag, "dmg_name": f"Vocal-More-{version}.dmg",
               "notes_sha256": sha256(b"release notes")}
    directory = tmp_path / "candidate"
    directory.mkdir(parents=True)
    (directory / context["dmg_name"]).write_bytes(b"dmg")
    (directory / "release-notes.md").write_bytes(b"release notes")
    (directory / "baseline-appcast.xml").write_bytes(old_feed or EMPTY_BASELINE)
    write_json(directory / "verification.json", {"status": "passed", "version": version, "channel": value.channel,
               "dmg_sha256": sha256(b"dmg"), "notarization": {"id": "test-submission", "status": "Accepted"}})
    (directory / "appcast.xml").write_bytes(xml_for(version, previous=previous["version"] if previous else None))
    if previous:
        (directory / "update.delta").write_bytes(b"delta")
    for key, val in {"GITHUB_REPOSITORY": REPOSITORY, "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/release-prepare.yml@refs/heads/main",
                     "GITHUB_SHA": SOURCE, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "push"}.items():
        monkeypatch.setenv(key, val)
    manifest = candidate.seal(directory, context, {"feed_sha256": sha256(old_feed), "previous": previous})
    return context, directory, manifest


class FakeGitHub:
    repository = REPOSITORY

    def __init__(self):
        self.releases = {}
        self.content = {}
        self.next_id = 1
        self.writes = []
        self.source_sha = SOURCE
        self.rename_failures = 0

    def release(self, tag):
        return copy.deepcopy(self.releases.get(tag))

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
    value = Version.parse(version)
    api.add_release(value.tag, prerelease=bool(value.stage))
    asset = api.add_asset(value.tag, f"Vocal-More-{version}.dmg", b"old")
    api.add_release(value.feed_tag, prerelease=True)
    old_feed = xml_for(version)
    api.add_asset(value.feed_tag, "appcast.xml", old_feed)
    return {"version": version, "tag": value.tag, "asset": asset, "digest": asset["digest"]}, old_feed
