"""Candidate routing and signed feed preparation without remote writes."""

import json
import shutil
from pathlib import Path

import pytest
from release import candidate, cli, feed
from release.model import ReleaseError

from tests.release_helpers import FakeGitHub, make_candidate, previous_release, xml_for


def available_candidate(api, monkeypatch, context, directory):
    original_pages = api.pages
    artifact = {"id": 7, "expired": False, "name": candidate.artifact_prefix(context) + "123-1",
                "created_at": "2026-09-12T00:00:00Z", "workflow_run": {"id": 123}}
    monkeypatch.setattr(api, "pages", lambda path, key=None: [artifact] if path == "actions/artifacts" else original_pages(path, key))
    def download(api, artifact_id, destination, expected):
        assert artifact_id == 7
        shutil.copytree(directory, destination)
        return candidate.validate(destination, expected)
    monkeypatch.setattr(candidate, "download", download)


def test_ready_missing_and_explicit_candidate_routes(tmp_path, monkeypatch):
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    assert cli.resolve(api, context, "auto") == ("full", 0, "CANDIDATE_NOT_READY")
    with pytest.raises(ReleaseError, match="CANDIDATE_NOT_READY"):
        cli.resolve(api, context, "require-ready")
    available_candidate(api, monkeypatch, context, directory)
    assert cli.resolve(api, context, "require-ready") == ("fast", 7, "READY")
    assert cli.resolve(api, context, "rebuild") == ("full", 0, "EXPLICIT_REBUILD")
    api.add_release(context["release_tag"], draft=True, prerelease=True)
    api.add_asset(context["release_tag"], "manifest.json", b"snapshot")
    assert cli.resolve(api, context, "auto") == ("resume", 0, "EXISTING_RELEASE_SNAPSHOT")
    with pytest.raises(ReleaseError, match="Cannot rebuild"):
        cli.resolve(api, context, "rebuild")


def test_tag_alias_and_new_baseline_need_resigned_metadata(tmp_path, monkeypatch):
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.4.18b2")
    api = FakeGitHub()
    available_candidate(api, monkeypatch, context, directory)
    alias = {**context, "release_tag": "0.4.18b2"}
    assert cli.resolve(api, alias, "auto") == ("refresh", 7, "TAG_URL_CHANGED")
    with pytest.raises(ReleaseError, match="TAG_URL_CHANGED"):
        cli.resolve(api, alias, "require-ready")
    previous_release(api, "0.4.18b1")
    assert cli.resolve(api, context, "auto") == ("refresh", 7, "STALE_BASELINE")
    with pytest.raises(ReleaseError, match="STALE_BASELINE"):
        cli.resolve(api, context, "require-ready")


def test_invalid_candidate_is_not_hidden_by_full_build(tmp_path, monkeypatch):
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    available_candidate(api, monkeypatch, context, directory)
    (directory / context["dmg_name"]).write_bytes(b"corrupt")
    with pytest.raises(ReleaseError, match="digest mismatch"):
        cli.resolve(api, context, "auto")
    assert not api.writes


def test_refresh_preserves_original_expiration(tmp_path, monkeypatch):
    context, directory, manifest = make_candidate(tmp_path, monkeypatch)
    refreshed = candidate.seal(directory, context, manifest["baseline"], {"expires_at": manifest["expires_at"]})
    assert refreshed["expires_at"] == manifest["expires_at"]


def test_preflight_skips_version_published_under_bare_alias(tmp_path, monkeypatch, capsys):
    import sys
    context, _, _ = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    api.add_release("0.4.18a1", prerelease=True)
    monkeypatch.setattr(cli, "GitHub", lambda: api)
    monkeypatch.setattr(cli, "context_for", lambda *args: context)
    monkeypatch.setattr(sys, "argv", ["release_cli.py", "preflight", "--prepare"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["skip"] is True
    assert not api.writes


def test_feed_generation_normalizes_before_signing_and_verifies_delta(tmp_path, monkeypatch):
    api = FakeGitHub()
    previous, old_feed = previous_release(api, "0.4.18b1")
    context, directory, _ = make_candidate(tmp_path, monkeypatch, "0.4.18b2", previous=previous, old_feed=old_feed)
    (directory / "update.delta").unlink()
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("SPARKLE_PRIVATE_KEY", "test-private-input")
    monkeypatch.setattr(feed.subprocess, "check_output", lambda *args, **kwargs: "/mock/sparkle\n")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["input"] == "test-private-input\n"
        assert "test-private-input" not in command
        if Path(command[0]).name == "generate_appcast":
            updates = Path(command[-1])
            xml = xml_for(context["version"], previous=previous["version"]).decode()
            xml = xml.replace("</sparkle:version>", "</sparkle:version><sparkle:shortVersionString>0.4.18</sparkle:shortVersionString>", 1)
            xml = xml.replace("update.delta", "Vocal%20More0.4.18b2-0.4.18b1.delta")
            (updates / "Vocal More0.4.18b2-0.4.18b1.delta").write_bytes(b"delta")
            (updates / "appcast.xml").write_text(xml)
        elif "--verify" not in command:
            text = Path(command[-1]).read_text()
            assert "<sparkle:shortVersionString>0.4.18b2</sparkle:shortVersionString>" in text
            assert "Vocal.More0.4.18b2-0.4.18b1.delta" in text
    monkeypatch.setattr(feed.subprocess, "run", run)
    applied = []
    monkeypatch.setattr(feed, "verify_delta", lambda *args: applied.append(args))
    state = feed.prepare(api, context, directory, root)
    candidate.seal(directory, context, state)
    assert len(applied) == 1
    assert applied[0][1].name == "Vocal-More-0.4.18b1.dmg"
    assert any("--verify" in call and call[-1].endswith("appcast.xml") for call in calls)
    assert not api.writes
