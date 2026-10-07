"""Candidate identity, channel and integrity failures must fail closed."""

import io
import json
import runpy
import stat
import sys
import tomllib
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from release import candidate, feed
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


def test_release_lookup_finds_drafts_and_rejects_ambiguity(monkeypatch):
    api = GitHub()
    draft = {"id": 7, "tag_name": "v0.5.0-alpha.1", "draft": True}
    monkeypatch.setattr(api, "optional", lambda _: None)
    monkeypatch.setattr(api, "pages", lambda _: [draft])
    assert api.release("v0.5.0-alpha.1") == draft
    assert api.release("v0.5.0-alpha.2") is None
    monkeypatch.setattr(api, "pages", lambda _: [draft, {**draft, "id": 8}])
    with pytest.raises(ReleaseError, match="Multiple draft"):
        api.release("v0.5.0-alpha.1")


def test_published_release_lookup_does_not_scan_drafts(monkeypatch):
    api = GitHub()
    published = {"id": 7, "tag_name": "v0.5.0-alpha.1", "draft": False}
    monkeypatch.setattr(api, "optional", lambda _: published)
    monkeypatch.setattr(api, "pages", lambda _: pytest.fail("Published lookup must use tag endpoint"))
    assert api.release("v0.5.0-alpha.1") == published


@pytest.mark.parametrize("project,build,text,tag,channel,bundle,dmg", [
    ("0.6.0", 1, "0.6.0+1", "v0.6.0", "stable", "1", "Vocal-More-0.6.0.dmg"),
    ("0.6.1b12", 58, "0.6.1-beta.12+58", "v0.6.1-beta.12", "beta", "58", "Vocal-More-0.6.1-beta.12.dmg"),
])
def test_version_contract(project, build, text, tag, channel, bundle, dmg):
    version = Version.project(project, build)
    assert version == Version.parse(text)
    assert (version.text, version.tag, version.channel, version.bundle_version, version.dmg_name) == (text, tag, channel, bundle, dmg)
    assert version.pep440 == project
    assert Version.from_tag(tag).release == Version.from_tag(project).release == version.release


def test_feed_order_follows_build_numbers_and_legacy_releases_come_first():
    values = ["0.6.1-beta.1+5", "0.5.0", "0.6.0+4", "0.6.1+6", "0.5.0a9"]
    assert sorted(values, key=lambda s: Version.any(s).order) == ["0.5.0a9", "0.5.0", "0.6.0+4", "0.6.1-beta.1+5", "0.6.1+6"]


@pytest.mark.parametrize("text", ["01.2.3+1", "1.2+1", "1.2.3", "1.2.3+0", "1.2.3-alpha.1+1", "1.2.3-beta.0+1",
                                  "1.2.3-beta.256+1", "1.2.3b1+1", "1.2.3+local", "1.2.3+1;echo oops"])
def test_invalid_versions_are_rejected(text):
    with pytest.raises(ReleaseError):
        Version.parse(text)


@pytest.mark.parametrize("project,build", [("0.6.0a1", 1), ("0.6.0", 0), ("0.6.0", "1"), ("0.6.0", None), ("0.6.0rc1", 1)])
def test_alpha_and_missing_build_are_rejected_in_pyproject(project, build):
    with pytest.raises(ReleaseError):
        Version.project(project, build)


@pytest.mark.parametrize("version,display", [("0.4.18", "0.4.18+1"), ("0.4.18b1", "0.4.18-beta.1+1")])
def test_real_setup_embeds_the_correct_update_channel(monkeypatch, version, display):
    import vocal_more
    captured = {}
    monkeypatch.setattr(vocal_more, "__version__", version)
    monkeypatch.setitem(sys.modules, "setuptools", SimpleNamespace(setup=lambda **kwargs: captured.update(kwargs)))
    monkeypatch.delenv("VOCAL_MORE_BUILD_NUMBER", raising=False)
    root = Path(__file__).resolve().parents[1]
    build = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["vocal-more"]["build"]
    display = display.replace("+1", f"+{build}")
    runpy.run_path(str(root / "packaging/macos/setup.py"))
    plist = captured["app"][0]["plist"]
    assert plist["VocalMoreVersion"] == display
    assert plist["CFBundleShortVersionString"] == "0.4.18"
    assert plist["CFBundleVersion"] == str(build)
    assert plist["SUFeedURL"] == Version.parse(display).feed_url
    assert plist["VocalMoreReleaseChannel"] == Version.parse(display).channel


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


def test_feed_items_must_carry_the_channel_their_version_names():
    assert feed_versions(xml_for("0.6.1-beta.1+2")) == [Version.parse("0.6.1-beta.1+2")]
    unmarked = xml_for("0.6.1-beta.1+2").replace(b"<sparkle:channel>beta</sparkle:channel>", b"")
    with pytest.raises(ReleaseError, match="Cross-channel"):
        feed_versions(unmarked)
    with pytest.raises(ReleaseError, match="Cross-channel"):
        feed_versions(xml_for("0.5.0a9"))
    renumbered = xml_for("0.6.0+1").replace(b"<sparkle:version>1<", b"<sparkle:version>2<")
    with pytest.raises(ReleaseError, match="build number"):
        feed_versions(renumbered)


@pytest.mark.parametrize("previous,current", [
    ("0.6.0+1", "0.6.1+3"),
    ("0.6.1-beta.1+2", "0.6.1-beta.2+3"),
    ("0.6.1-beta.1+2", "0.6.1+3"),
])
def test_sparkle_rewritten_previous_display_and_channel_are_restored(previous, current):
    old = xml_for(previous, keep=xml_for("0.5.1"))
    generated = xml_for(current, keep=old).decode()
    # Sparkle 2.9.4 FeedXML.swift writes every available archive's numeric
    # CFBundleShortVersionString, including the previous archive used for delta.
    for value in (previous, current):
        generated = generated.replace(
            f"<sparkle:shortVersionString>{value}</sparkle:shortVersionString>",
            f"<sparkle:shortVersionString>{Version.parse(value).base}</sparkle:shortVersionString>")
    generated = generated.replace("<sparkle:channel>beta</sparkle:channel>", "")
    generated = generated.replace("</item>", "<description><![CDATA[<p>Keep notes & links</p>]]></description></item>")
    with pytest.raises(ReleaseError, match="Unsupported product version"):
        feed_versions(generated.encode())
    final = feed.finish_appcast(generated, Version.parse(current), old)
    assert feed_versions(final.encode()) == [Version.legacy("0.5.1"), Version.parse(previous), Version.parse(current)]
    assert final.count("<![CDATA[<p>Keep notes & links</p>]]>") == 3
    assert final.count("<sparkle:channel>beta</sparkle:channel>") == sum(
        Version.parse(value).channel == "beta" for value in (previous, current))


def test_empty_notes_and_lock_version_mismatch_fail_preflight(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="0.6.1b1"\nlicense="GPL-3.0-only"\n[tool.vocal-more]\nbuild=7\n')
    (tmp_path / "uv.lock").write_text('[[package]]\nname="vocal-more"\nversion="0.6.1b1"\n')
    notes = tmp_path / "docs/releases/0.6.1-beta.1.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("")
    with pytest.raises(ReleaseError, match="notes"):
        read_context(tmp_path, SOURCE)
    notes.write_text("Beta test")
    context = read_context(tmp_path, SOURCE)
    assert (context["version"], context["release_tag"], context["dmg_name"]) == (
        "0.6.1-beta.1+7", "v0.6.1-beta.1", "Vocal-More-0.6.1-beta.1.dmg")
    (tmp_path / "uv.lock").write_text('[[package]]\nname="vocal-more"\nversion="0.6.1"\n')
    with pytest.raises(ReleaseError, match="uv.lock"):
        read_context(tmp_path, SOURCE)
