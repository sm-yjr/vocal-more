"""Sparkle preparation on macOS; structural validation on either platform."""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .model import (
    EMPTY_BASELINE,
    REPOSITORY,
    SPARKLE_NS,
    ReleaseError,
    Version,
    file_hash,
    sha256,
)
from .state import baseline, feed_versions


def validate_appcast(directory: Path, manifest: dict) -> None:
    data = (directory / "appcast.xml").read_bytes()
    version = Version.parse(manifest["version"])
    versions = feed_versions(data, version.channel)
    if versions.count(version) != 1 or max(versions, key=lambda v: v.key) != version:
        raise ReleaseError("Candidate appcast does not advertise the expected newest version")
    root = ET.fromstring(data)
    item = next(i for i in root.findall("./channel/item") if i.findtext(f"{{{SPARKLE_NS}}}version") == version.text)
    enclosure = item.find("enclosure")
    if enclosure is None:
        raise ReleaseError("Missing full update enclosure")
    enclosures = [enclosure, *item.findall(f"{{{SPARKLE_NS}}}deltas/enclosure")]
    names = set()
    for entry in enclosures:
        prefix = f"https://github.com/{REPOSITORY}/releases/download/{manifest['release_tag']}/"
        url = entry.get("url", "")
        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        if not url.startswith(prefix) or url != prefix + name or name not in manifest["files"]:
            raise ReleaseError("Update URL is not an exact candidate asset URL")
        if name in names or int(entry.get("length", "-1")) != manifest["files"][name]["size"]:
            raise ReleaseError("Update enclosure length/name mismatch")
        names.add(name)
        signature = entry.get(f"{{{SPARKLE_NS}}}edSignature", "")
        try:
            if len(base64.b64decode(signature, validate=True)) != 64:
                raise ValueError("wrong signature length")
        except ValueError as exc:
            raise ReleaseError("Missing or malformed Sparkle archive signature") from exc
    if enclosure.get("url", "").rsplit("/", 1)[-1] != manifest["dmg_name"]:
        raise ReleaseError("Full update URL does not name the DMG")
    deltas = item.findall(f"{{{SPARKLE_NS}}}deltas/enclosure")
    previous = manifest["baseline"].get("previous")
    if len(deltas) != (1 if previous else 0):
        raise ReleaseError("Expected exactly one previous-version delta, or none for a new channel")
    if deltas and deltas[0].get(f"{{{SPARKLE_NS}}}deltaFrom") != previous["version"]:
        raise ReleaseError("Delta is based on the wrong channel version")
    expected_names = {manifest["dmg_name"]} | {n for n in manifest["files"] if n.endswith(".delta")}
    if names != expected_names:
        raise ReleaseError("Candidate delta files and appcast do not agree")


@contextmanager
def mounted_app(dmg: Path):
    with tempfile.TemporaryDirectory(prefix="vocal-release-mount-") as temp:
        mount = Path(temp) / "volume"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)], check=True, capture_output=True)
        try:
            yield mount / "Vocal More.app"
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, capture_output=True)


def tree_contents(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ("link", os.readlink(path))
        elif path.is_file():
            result[name] = ("file", path.stat().st_mode & 0o777, file_hash(path))
        elif path.is_dir():
            result[name] = ("dir", path.stat().st_mode & 0o777)
    return result


def verify_delta(sparkle: Path, previous: Path, current: Path, delta: Path) -> None:
    with (
        mounted_app(previous) as old,
        mounted_app(current) as new,
        tempfile.TemporaryDirectory(prefix="vocal-release-delta-") as temp,
    ):
        patched = Path(temp) / "Vocal More.app"
        subprocess.run([str(sparkle / "bin/BinaryDelta"), "apply", str(old), str(patched), str(delta)], check=True)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", str(patched)], check=True)
        if tree_contents(patched) != tree_contents(new):
            raise ReleaseError("Applied delta differs from final signed application")


def prepare(api, context: dict, candidate_dir: Path, root: Path) -> dict:
    state, old_feed, tag_map = baseline(api, context)
    (candidate_dir / "baseline-appcast.xml").write_bytes(old_feed or EMPTY_BASELINE)
    sparkle = Path(subprocess.check_output([str(root / "packaging/macos/install_sparkle.sh")], text=True).strip())
    secret = os.environ.get("SPARKLE_PRIVATE_KEY")
    if not secret:
        raise ReleaseError("Missing SPARKLE_PRIVATE_KEY")

    def signed_tool(tool: str, args: list[str]) -> None:
        # The private key is never a command-line argument or logged payload.
        subprocess.run([str(sparkle / "bin" / tool), "--ed-key-file", "-", *args], input=secret + "\n", text=True, check=True)

    with tempfile.TemporaryDirectory(prefix="vocal-release-feed-") as temp:
        updates = Path(temp)
        current = updates / context["dmg_name"]
        shutil.copy2(candidate_dir / current.name, current)
        shutil.copy2(candidate_dir / "release-notes.md", current.with_suffix(".md"))
        if old_feed:
            (updates / "appcast.xml").write_bytes(old_feed or EMPTY_BASELINE)
            signed_tool("sign_update", ["--verify", str(updates / "appcast.xml")])
        previous_path = None
        if state["previous"]:
            previous = state["previous"]
            previous_path = updates / previous["asset"]["name"]
            previous_data = api.asset_bytes(previous["asset"])
            if "sha256:" + sha256(previous_data) != previous["digest"]:
                raise ReleaseError("Previous DMG changed during candidate preparation")
            previous_path.write_bytes(previous_data)
        signed_tool("generate_appcast", [
            "--download-url-prefix", f"https://github.com/{REPOSITORY}/releases/download/{context['release_tag']}/",
            "--embed-release-notes", "--versions", context["version"], "--maximum-versions", "5",
            "--maximum-deltas", "1", "--delta-compression", "lzfse", "--link", f"https://github.com/{REPOSITORY}",
            "-o", str(updates / "appcast.xml"), str(updates),
        ])
        for delta in updates.glob("*.delta"):
            normalized = delta.name.replace(" ", ".")
            if normalized != delta.name:
                delta.rename(updates / normalized)
        spec = importlib.util.spec_from_file_location("release_url_normalizer", root / "packaging/macos/normalize_appcast_urls.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        appcast = updates / "appcast.xml"
        xml = module.normalize_appcast_urls(appcast.read_text(), tag_map=tag_map)
        # Keep the human-facing alpha/beta suffix while CFBundleShortVersionString
        # stays numeric. Preserve CDATA and sign only after this final edit.
        def display_version(match):
            item = match.group(0)
            if f"<sparkle:version>{context['version']}</sparkle:version>" in item:
                item = re.sub(r"(<sparkle:shortVersionString>).*?(</sparkle:shortVersionString>)",
                              lambda m: m[1] + context["version"] + m[2], item, flags=re.DOTALL)
            return item
        xml = re.sub(r"<item>.*?</item>", display_version, xml, flags=re.DOTALL)
        appcast.write_text(xml)
        # Separate feeds intentionally omit sparkle:channel; the app's SUFeedURL selects the channel.
        signed_tool("sign_update", [str(appcast)])
        signed_tool("sign_update", ["--verify", str(appcast)])
        tree = ET.fromstring(appcast.read_bytes())
        item = next((i for i in tree.findall("./channel/item") if i.findtext(f"{{{SPARKLE_NS}}}version") == context["version"]), None)
        if item is None:
            raise ReleaseError("Sparkle did not generate the requested version")
        for entry in [item.find("enclosure"), *item.findall(f"{{{SPARKLE_NS}}}deltas/enclosure")]:
            if entry is None:
                raise ReleaseError("Sparkle omitted full update")
            name = unquote(urlsplit(entry.attrib["url"]).path.rsplit("/", 1)[-1])
            path = updates / name
            if path.parent != updates or not path.is_file():
                raise ReleaseError("Sparkle generated an invalid archive reference")
            signed_tool("sign_update", ["--verify", str(path), entry.attrib[f"{{{SPARKLE_NS}}}edSignature"]])
            if path.suffix == ".delta":
                if previous_path is None:
                    raise ReleaseError("Unexpected delta without a baseline")
                verify_delta(sparkle, previous_path, current, path)
                shutil.copy2(path, candidate_dir / name)
        shutil.copy2(appcast, candidate_dir / "appcast.xml")
    return state
