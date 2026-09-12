"""Exercise actual publication state transitions against an in-memory GitHub."""

from pathlib import Path

import pytest
import yaml
from release import publish
from release.github import named_asset
from release.model import ReleaseError
from release.state import baseline

from tests.release_helpers import FakeGitHub, make_candidate, previous_release, xml_for


def no_network(*args):
    pass


def test_alpha_publication_isolated_and_retry_is_idempotent(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous_release(api, "0.4.17")
    stable_before = api.release("sparkle-feed")
    stable_bytes = api.asset_bytes(named_asset(stable_before, "appcast.xml"))
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    backup = tmp_path / "backup"
    publish.stage(api, directory, context, backup)
    assert api.release(context["release_tag"])["draft"]
    assert not api.release(context["feed_tag"])
    assert (backup / "recovery.json").exists()
    result = publish.commit(api, directory, context, read_public=no_network)
    assert result["status"] == "PUBLISHED"
    assert api.release(context["release_tag"])["prerelease"]
    assert api.release(context["release_tag"])["make_latest"] == "false"
    assert api.release("sparkle-feed") == stable_before
    assert api.asset_bytes(named_asset(api.release("sparkle-feed"), "appcast.xml")) == stable_bytes
    writes = len(api.writes)
    publish.stage(api, directory, context, backup)
    publish.commit(api, directory, context, read_public=no_network)
    assert len(api.writes) == writes


def test_failed_feed_rename_restores_baseline_and_can_resume(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, old_feed = previous_release(api, "0.4.18b1")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.4.18b2", previous=previous, old_feed=old_feed)
    publish.stage(api, directory, context, tmp_path / "backup")
    api.rename_failures = 1
    with pytest.raises(RuntimeError, match="Injected"):
        publish.commit(api, directory, context, read_public=no_network)
    assert not api.release(context["release_tag"])["draft"]
    assert api.asset_bytes(named_asset(api.release(context["feed_tag"]), "appcast.xml")) == old_feed
    publish.commit(api, directory, context, read_public=no_network)
    assert api.asset_bytes(named_asset(api.release(context["feed_tag"]), "appcast.xml")) == (directory / "appcast.xml").read_bytes()


def test_killed_publisher_with_missing_feed_recovers_from_snapshot(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, old_feed = previous_release(api, "0.4.18b1")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.4.18b2", previous=previous, old_feed=old_feed)
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
    api.content[asset["id"]] = xml_for("0.4.18a2")
    with pytest.raises(ReleaseError, match="SUPERSEDED"):
        publish.commit(api, directory, context, read_public=no_network)


def test_feed_baseline_change_requires_refresh(tmp_path, monkeypatch):
    api = FakeGitHub()
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.4.18b2")
    previous_release(api, "0.4.18b1")
    with pytest.raises(ReleaseError, match="STALE_BASELINE"):
        publish.stage(api, directory, context, tmp_path / "backup")
    assert api.release(context["release_tag"]) is None


def test_incomplete_previous_release_blocks_new_publication():
    api = FakeGitHub()
    api.add_release("v0.4.18-beta.1", prerelease=True)
    with pytest.raises(ReleaseError, match="PREVIOUS_RELEASE_INCOMPLETE"):
        baseline(api, {"version": "0.4.18b2", "channel": "beta", "feed_tag": "sparkle-feed-beta", "release_tag": "v0.4.18-beta.2"})


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
