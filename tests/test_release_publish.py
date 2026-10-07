"""Exercise actual publication state transitions against an in-memory GitHub."""

from pathlib import Path

import pytest
import yaml
from release import publish
from release.github import named_asset
from release.model import ReleaseError, Version
from release.state import baseline, feed_versions

from tests.release_helpers import FakeGitHub, make_candidate, previous_release, xml_for


def no_network(*args):
    pass


def feed_bytes(api, tag="sparkle-feed"):
    return api.asset_bytes(named_asset(api.release(tag), "appcast.xml"))


def test_beta_publication_keeps_stable_items_and_retry_is_idempotent(tmp_path, monkeypatch):
    api = FakeGitHub()
    _, stable_feed = previous_release(api, "0.6.0+1")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, old_feed=stable_feed)
    backup = tmp_path / "backup"
    publish.stage(api, directory, context, backup)
    assert api.release(context["release_tag"])["draft"]
    assert feed_bytes(api) == stable_feed
    assert (backup / "recovery.json").exists()
    result = publish.commit(api, directory, context, read_public=no_network)
    assert result["status"] == "PUBLISHED"
    assert api.release(context["release_tag"])["prerelease"]
    assert api.release(context["release_tag"])["make_latest"] == "false"
    # One feed: the stable item stays; the beta item is marked for beta installs only.
    assert feed_versions(feed_bytes(api)) == [Version.parse("0.6.0+1"), Version.parse(context["version"])]
    assert b"<sparkle:channel>beta</sparkle:channel>" in feed_bytes(api)
    writes = len(api.writes)
    publish.stage(api, directory, context, backup)
    publish.commit(api, directory, context, read_public=no_network)
    assert len(api.writes) == writes


def test_stable_after_beta_uses_previous_stable_and_mirrors_legacy_feeds(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, _ = previous_release(api, "0.6.0+1")
    _, shared = previous_release(api, "0.6.1-beta.1+2")
    api.add_release("sparkle-feed-alpha", prerelease=True)
    api.add_asset("sparkle-feed-alpha", "appcast.xml", b"<rss/>")
    state, _, _ = baseline(api, {"version": "0.6.1+3", "channel": "stable", "feed_tag": "sparkle-feed", "release_tag": "v0.6.1"})
    assert state["previous"]["version"] == "0.6.0+1"
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.6.1+3", previous=state["previous"], old_feed=shared)
    publish.stage(api, directory, context, tmp_path / "backup")
    publish.commit(api, directory, context, read_public=no_network)
    assert api.release(context["release_tag"])["make_latest"] == "true"
    assert feed_bytes(api) == (directory / "appcast.xml").read_bytes()
    # Pre-0.6 alpha installs poll the alpha feed and ignore beta items: they get stable.
    assert feed_bytes(api, "sparkle-feed-alpha") == feed_bytes(api)
    assert api.release("sparkle-feed-beta") is None


def test_first_build_numbered_release_upgrades_the_legacy_stable(tmp_path, monkeypatch):
    api = FakeGitHub()
    api.add_release("v0.5.0-alpha.9", prerelease=True)  # Retired channel: ignored.
    previous, old_feed = previous_release(api, "0.5.0")
    state, _, _ = baseline(api, {"version": "0.6.0+1", "channel": "stable", "feed_tag": "sparkle-feed", "release_tag": "v0.6.0"})
    assert state["previous"]["asset"]["name"] == "Vocal-More-0.5.0.dmg"
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.6.0+1", previous=state["previous"], old_feed=old_feed)
    publish.stage(api, directory, context, tmp_path / "backup")
    assert publish.commit(api, directory, context, read_public=no_network)["status"] == "PUBLISHED"
    assert b'sparkle:deltaFrom="0.5.0"' in feed_bytes(api)


def test_build_number_must_exceed_every_published_build():
    api = FakeGitHub()
    previous_release(api, "0.6.0+1")
    previous_release(api, "0.6.1-beta.1+5")
    with pytest.raises(ReleaseError, match="build number"):
        baseline(api, {"version": "0.6.1+4", "channel": "stable", "feed_tag": "sparkle-feed", "release_tag": "v0.6.1"})


def test_failed_feed_rename_restores_baseline_and_can_resume(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, old_feed = previous_release(api, "0.6.1-beta.1+2")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.6.1-beta.2+3", previous=previous, old_feed=old_feed)
    publish.stage(api, directory, context, tmp_path / "backup")
    api.rename_failures = 1
    with pytest.raises(RuntimeError, match="Injected"):
        publish.commit(api, directory, context, read_public=no_network)
    assert not api.release(context["release_tag"])["draft"]
    assert feed_bytes(api) == old_feed
    publish.commit(api, directory, context, read_public=no_network)
    assert feed_bytes(api) == (directory / "appcast.xml").read_bytes()


@pytest.mark.parametrize("accepted_before_timeout", [False, True])
def test_partial_draft_upload_resumes_without_recreating_release(tmp_path, monkeypatch, accepted_before_timeout):
    api = FakeGitHub()
    context, directory, manifest = make_candidate(tmp_path, monkeypatch)
    backup = tmp_path / "backup"
    upload = api.upload

    def interrupted_upload(tag, path):
        if path.name == context["dmg_name"]:
            if accepted_before_timeout:
                upload(tag, path)
            raise ReleaseError("Injected upload timeout")
        upload(tag, path)

    with monkeypatch.context() as patch:
        patch.setattr(api, "upload", interrupted_upload)
        if accepted_before_timeout:
            publish.stage(api, directory, context, backup)
        else:
            with pytest.raises(ReleaseError, match="Upload not visible"):
                publish.stage(api, directory, context, backup)
            assert named_asset(api.release(context["release_tag"]), "manifest.json") is None
            assert not (backup / "recovery.json").exists()

    draft = api.release(context["release_tag"])
    assert draft["draft"]
    existing_assets = {asset["name"]: asset["id"] for asset in draft["assets"]}
    publish.stage(api, directory, context, backup)
    resumed = api.release(context["release_tag"])
    assert resumed["id"] == draft["id"]
    assert {asset["name"] for asset in resumed["assets"]} == {*manifest["files"], "manifest.json"}
    for name, asset_id in existing_assets.items():
        assert named_asset(resumed, name)["id"] == asset_id
    assert sum(write[:2] == ("POST", "releases") for write in api.writes) == 1
    assert sum(write == ("upload", context["release_tag"], context["dmg_name"]) for write in api.writes) == 1
    assert publish.commit(api, directory, context, read_public=no_network)["status"] == "PUBLISHED"


def test_killed_publisher_with_missing_feed_recovers_from_snapshot(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, old_feed = previous_release(api, "0.6.1-beta.1+2")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.6.1-beta.2+3", previous=previous, old_feed=old_feed)
    publish.stage(api, directory, context, tmp_path / "backup")
    api.releases[context["release_tag"]]["draft"] = False
    asset = named_asset(api.release(context["feed_tag"]), "appcast.xml")
    api.api(f"releases/assets/{asset['id']}", method="DELETE")
    assert publish.commit(api, directory, context, read_public=no_network)["status"] == "PUBLISHED"


def test_moved_tag_or_conflicting_asset_causes_no_overwrite(tmp_path, monkeypatch):
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    api.source_sha = "b" * 40
    with pytest.raises(ReleaseError, match="tag moved"):
        publish.stage(api, directory, context, tmp_path / "backup")
    assert not api.writes
    api.source_sha = context["source_sha"]
    api.add_release(context["release_tag"], draft=True, prerelease=True)
    original = api.add_asset(context["release_tag"], context["dmg_name"], b"bad")
    with pytest.raises(ReleaseError, match="conflict"):
        publish.stage(api, directory, context, tmp_path / "backup")
    assert api.asset_bytes(original) == b"bad"


def test_old_retry_cannot_downgrade_newer_feed(tmp_path, monkeypatch):
    api = FakeGitHub()
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    publish.stage(api, directory, context, tmp_path / "backup")
    publish.commit(api, directory, context, read_public=no_network)
    asset = named_asset(api.release(context["feed_tag"]), "appcast.xml")
    api.content[asset["id"]] = xml_for("0.6.1-beta.2+3")
    with pytest.raises(ReleaseError, match="SUPERSEDED"):
        publish.commit(api, directory, context, read_public=no_network)


def test_feed_baseline_change_requires_refresh(tmp_path, monkeypatch):
    api = FakeGitHub()
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.6.1-beta.2+3")
    previous_release(api, "0.6.1-beta.1+2")
    with pytest.raises(ReleaseError, match="STALE_BASELINE"):
        publish.stage(api, directory, context, tmp_path / "backup")
    assert api.release(context["release_tag"]) is None


def test_incomplete_previous_release_blocks_new_publication():
    api = FakeGitHub()
    api.add_release("v0.6.1-beta.1", prerelease=True)
    with pytest.raises(ReleaseError, match="PREVIOUS_RELEASE_INCOMPLETE"):
        baseline(api, {"version": "0.6.1-beta.2+3", "channel": "beta", "feed_tag": "sparkle-feed", "release_tag": "v0.6.1-beta.2"})


def test_public_feed_read_failure_does_not_report_success(tmp_path, monkeypatch):
    api = FakeGitHub()
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    publish.stage(api, directory, context, tmp_path / "backup")
    def fail_feed(url, expected_hash=None):
        if expected_hash:
            raise ReleaseError("CDN not ready")
    with pytest.raises(ReleaseError, match="CDN"):
        publish.commit(api, directory, context, read_public=fail_feed)
    assert publish.commit(api, directory, context, read_public=no_network)["status"] == "PUBLISHED"


def test_workflows_keep_signing_out_of_linux_publisher():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    common = yaml.safe_load((root / ".github/workflows/_release-candidate.yml").read_text())
    assert workflow["concurrency"]["cancel-in-progress"] is False
    publisher = workflow["jobs"]["publish"]
    assert publisher["runs-on"] == "ubuntu-24.04"
    assert "prepare" in publisher["needs"]
    assert "needs.prepare.result == 'success'" in publisher["if"]
    assert "needs.prepare.result == 'skipped'" in publisher["if"]
    body = str(publisher)
    for secret in ("SPARKLE_PRIVATE_KEY", "APPLE_APP_SPECIFIC_PASSWORD", "MACOS_CERTIFICATE_PASSWORD"):
        assert secret not in body
    commands = "\n".join(step.get("run", "") for step in publisher["steps"])
    for tool in ("cargo build", "notarytool", "generate_appcast", "build_dmg.sh", "uv sync"):
        assert tool not in commands
    assert common["jobs"]["seal-candidate"]["needs"] == "build"
    names = [step["name"] for step in publisher["steps"]]
    assert names.index("Persist feed recovery checkpoint") < names.index("Publish release and signed channel feed")
