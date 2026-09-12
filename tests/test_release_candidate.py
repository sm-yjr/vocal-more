"""Candidate identity, channel and integrity failures must fail closed."""

import io
import json
import runpy
import stat
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from release import candidate
from release.github import GitHub
from release.model import ReleaseError, Version, read_context, sha256, write_json
from release.state import feed_versions

from tests.release_helpers import SOURCE, FakeGitHub, make_candidate, xml_for


@pytest.mark.parametrize("endpoint,octet_stream", [
    ("actions/artifacts/123/zip", False),
    ("releases/assets/123", True),
])
def test_binary_download_selects_the_endpoint_media_type(monkeypatch, endpoint, octet_stream):
    def run(command, **kwargs):
        assert command[:3] == ["gh", "api", f"repos/sm-yjr/vocal-more/{endpoint}"]
        assert ("Accept: application/octet-stream" in command) is octet_stream
        return SimpleNamespace(returncode=0, stdout=b"binary archive", stderr=b"")

    monkeypatch.setattr("release.github.subprocess.run", run)
    assert GitHub().api(endpoint, binary=True) == b"binary archive"


@pytest.mark.parametrize("text,tag,channel", [("0.4.18", "v0.4.18", "stable"), ("0.4.18a1", "v0.4.18-alpha.1", "alpha"), ("0.4.18b12", "v0.4.18-beta.12", "beta")])
def test_version_contract(text, tag, channel):
    version = Version.parse(text)
    assert version.tag == tag
    assert version.channel == channel
    assert Version.from_tag(tag) == Version.from_tag(text) == version


def test_versions_sort_numerically_and_stable_after_prereleases():
    values = ["0.4.18a10", "0.4.18b1", "0.4.18", "0.4.19a1", "0.4.18a2"]
    assert sorted(values, key=lambda s: Version.parse(s).key) == ["0.4.18a2", "0.4.18a10", "0.4.18b1", "0.4.18", "0.4.19a1"]


@pytest.mark.parametrize("text", ["01.2.3", "1.2", "1.2.3rc1", "1.2.3a0", "1.2.3b256", "1.2.3+local", "1.2.3;echo oops"])
def test_invalid_versions_are_rejected(text):
    with pytest.raises(ReleaseError):
        Version.parse(text)


@pytest.mark.parametrize("version", ["0.4.18", "0.4.18a1", "0.4.18b1"])
def test_real_setup_embeds_the_correct_update_channel(monkeypatch, version):
    import vocal_more
    captured = {}
    monkeypatch.setattr(vocal_more, "__version__", version)
    monkeypatch.setitem(sys.modules, "setuptools", SimpleNamespace(setup=lambda **kwargs: captured.update(kwargs)))
    monkeypatch.delenv("VOCAL_MORE_BUILD_NUMBER", raising=False)
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(str(root / "packaging/macos/setup.py"))
    plist = captured["app"][0]["plist"]
    assert plist["VocalMoreVersion"] == version
    assert plist["CFBundleShortVersionString"] == "0.4.18"
    assert plist["CFBundleVersion"] == version
    assert plist["SUFeedURL"] == Version.parse(version).feed_url
    assert plist["VocalMoreReleaseChannel"] == Version.parse(version).channel


def test_sealed_candidate_binds_source_notes_and_every_file(tmp_path, monkeypatch):
    context, directory, _ = make_candidate(tmp_path, monkeypatch)
    candidate.validate(directory, context)
    wrong = {**context, "source_sha": "b" * 40}
    with pytest.raises(ReleaseError, match="source_sha"):
        candidate.validate(directory, wrong)
    (directory / context["dmg_name"]).write_bytes(b"bad")
    with pytest.raises(ReleaseError, match="digest"):
        candidate.validate(directory, context)


def test_expiration_and_failed_notary_are_not_ready(tmp_path, monkeypatch):
    context, directory, manifest = make_candidate(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    manifest["prepared_at"] = (now - timedelta(days=16)).isoformat()
    manifest["expires_at"] = (now - timedelta(days=2)).isoformat()
    write_json(directory / "manifest.json", manifest)
    with pytest.raises(ReleaseError, match="CANDIDATE_EXPIRED"):
        candidate.validate(directory, context)
    candidate.validate(directory, context, allow_expired=True)
    report = json.loads((directory / "verification.json").read_text())
    report["notarization"]["status"] = "Invalid"
    write_json(directory / "verification.json", report)
    with pytest.raises(ReleaseError, match="successful final DMG"):
        candidate.seal(directory, context, manifest["baseline"])


@pytest.mark.parametrize("name,symlink", [("./manifest.json", False), ("../manifest.json", False), ("/absolute", False), ("folder/file", False), ("link", True), ("file\\evil", False)])
def test_artifact_extraction_rejects_unsafe_paths(tmp_path, name, symlink):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        entry = zipfile.ZipInfo(name)
        if symlink:
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "payload")
    raw = data.getvalue()
    with pytest.raises(ReleaseError, match="Unsafe"):
        candidate.unpack(raw, "sha256:" + sha256(raw), tmp_path / "candidate")


def test_artifact_digest_mismatch_stops_before_extraction(tmp_path):
    with pytest.raises(ReleaseError, match="archive digest"):
        candidate.unpack(b"not a zip", "sha256:wrong", tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_foreign_or_failed_workflow_is_rejected(tmp_path, monkeypatch):
    _, _, manifest = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    candidate.verify_producer(api, manifest)
    run = api.api("actions/runs/123")
    run["head_repository"]["full_name"] = "somebody/fork"
    monkeypatch.setattr(api, "api", lambda *args, **kwargs: run)
    with pytest.raises(ReleaseError, match="Untrusted"):
        candidate.verify_producer(api, manifest)


def test_current_run_needs_successful_candidate_gate(tmp_path, monkeypatch):
    _, _, manifest = make_candidate(tmp_path, monkeypatch)
    api = FakeGitHub()
    run = api.api("actions/runs/123")
    run.update(status="in_progress", conclusion=None)
    monkeypatch.setattr(api, "api", lambda *args, **kwargs: run)
    with pytest.raises(ReleaseError, match="not completed"):
        candidate.verify_producer(api, manifest)
    candidate.verify_producer(api, manifest, current_run=123)
    monkeypatch.setattr(api, "pages", lambda *args, **kwargs: [{"name": "prepare / seal-candidate", "conclusion": "failure"}])
    with pytest.raises(ReleaseError, match="gate"):
        candidate.verify_producer(api, manifest, current_run=123)


def test_channels_cannot_leak_into_each_others_feeds():
    with pytest.raises(ReleaseError, match="Cross-channel"):
        feed_versions(xml_for("0.4.18a1"), "stable")
    with pytest.raises(ReleaseError, match="Cross-channel"):
        feed_versions(xml_for("0.4.18b1"), "alpha")


def test_empty_notes_and_lock_version_mismatch_fail_preflight(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="0.4.18a1"\nlicense="GPL-3.0-only"\n')
    (tmp_path / "uv.lock").write_text('[[package]]\nname="vocal-more"\nversion="0.4.18a1"\n')
    notes = tmp_path / "docs/releases/0.4.18a1.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("")
    with pytest.raises(ReleaseError, match="notes"):
        read_context(tmp_path, SOURCE)
    notes.write_text("Alpha test")
    assert read_context(tmp_path, SOURCE)["release_tag"] == "v0.4.18-alpha.1"
    (tmp_path / "uv.lock").write_text('[[package]]\nname="vocal-more"\nversion="0.4.18"\n')
    with pytest.raises(ReleaseError, match="uv.lock"):
        read_context(tmp_path, SOURCE)
