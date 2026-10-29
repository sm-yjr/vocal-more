#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare an immutable, signed APT repository in a new local directory."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import subprocess
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse


def build(
    packages: list[Path],
    output: Path,
    signing_key: str,
    base_url: str,
    suite: str = "stable",
) -> Path:
    if not re.fullmatch(r"(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64})", signing_key):
        raise ValueError("use a full signing-key fingerprint")
    url = urlparse(base_url)
    if (
        url.scheme != "https"
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
        or any(c.isspace() for c in base_url)
    ):
        raise ValueError("base URL must be an HTTPS repository URL")
    if suite not in {"stable", "beta"}:
        raise ValueError("suite must be stable or beta")
    if output.exists():
        raise ValueError(
            "output must be a new directory; publish the verified candidate atomically"
        )
    if not packages:
        raise ValueError("at least one .deb is required")
    entries: dict[str, list[tuple[str, bytes, str]]] = {}
    identities: set[tuple[str, str, str]] = set()
    # Validate all package identities before creating any repository files.
    for package in packages:
        metadata = subprocess.check_output(
            ["dpkg-deb", "--field", str(package)], text=True
        )
        fields = dict(
            re.findall(r"^([A-Za-z][A-Za-z0-9-]*): (.*)$", metadata, re.MULTILINE)
        )
        name, version, arch = (
            fields.get(key, "") for key in ("Package", "Version", "Architecture")
        )
        if (
            name != "vocal-more"
            or arch not in {"amd64", "arm64"}
            or not re.fullmatch(r"[0-9][0-9A-Za-z.+~:-]*", version)
        ):
            raise ValueError(
                "repository accepts only versioned native Vocal More packages"
            )
        if suite == "stable" and "~" in version:
            raise ValueError("a beta package cannot enter the stable suite")
        identity = name, version, arch
        if identity in identities:
            raise ValueError(
                "duplicate package version/architecture; never replace a published version"
            )
        identities.add(identity)
        filename = f"pool/main/v/vocal-more/vocal-more_{version}_{arch}.deb"
        payload = package.read_bytes()
        stanza = metadata.rstrip() + f"\nFilename: {filename}\nSize: {len(payload)}\n"
        for label, algorithm in (
            ("MD5sum", "md5"),
            ("SHA1", "sha1"),
            ("SHA256", "sha256"),
            ("SHA512", "sha512"),
        ):
            stanza += f"{label}: {hashlib.new(algorithm, payload).hexdigest()}\n"
        entries.setdefault(arch, []).append((filename, payload, stanza))
    output.mkdir(parents=True)
    indexes: list[Path] = []
    for arch, values in sorted(entries.items()):
        for filename, payload, _ in values:
            target = output / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        index = output / f"dists/{suite}/main/binary-{arch}/Packages"
        index.parent.mkdir(parents=True)
        index.write_text("\n".join(stanza for _, _, stanza in sorted(values)))
        compressed = index.with_name("Packages.gz")
        compressed.write_bytes(gzip.compress(index.read_bytes(), mtime=0))
        indexes.extend((index, compressed))
    now = datetime.now(timezone.utc)
    release_directory = output / f"dists/{suite}"
    text = (
        f"Origin: Vocal More\nLabel: Vocal More\nSuite: {suite}\nCodename: {suite}\n"
        f"Date: {format_datetime(now, usegmt=True)}\nValid-Until: {format_datetime(now + timedelta(days=90), usegmt=True)}\n"
        f"Architectures: {' '.join(sorted(entries))}\nComponents: main\nDescription: Vocal More Linux APT repository\n"
    )
    for label, algorithm in (("SHA256", "sha256"), ("SHA512", "sha512")):
        text += label + ":\n"
        for index in indexes:
            payload = index.read_bytes()
            text += f" {hashlib.new(algorithm, payload).hexdigest()} {len(payload)} {index.relative_to(release_directory)}\n"
    release = release_directory / "Release"
    release.write_text(text)
    commands = [
        ["--armor", "--export-options", "export-minimal", "--export", signing_key],
        [
            "--local-user",
            signing_key,
            "--digest-algo",
            "SHA256",
            "--clearsign",
            "--output",
            str(release_directory / "InRelease"),
            str(release),
        ],
        [
            "--local-user",
            signing_key,
            "--digest-algo",
            "SHA256",
            "--detach-sign",
            "--output",
            str(release_directory / "Release.gpg"),
            str(release),
        ],
    ]
    key = subprocess.check_output(["gpg", "--batch", *commands[0]])
    if not key.strip():
        raise ValueError("signing public key was not exported")
    (output / "vocal-more-archive-keyring.asc").write_bytes(key)
    for command in commands[1:]:
        subprocess.run(["gpg", "--batch", "--yes", *command], check=True)
    subprocess.run(
        ["gpg", "--batch", "--verify", str(release_directory / "InRelease")], check=True
    )
    (output / "vocal-more.sources").write_text(
        f"Types: deb\nURIs: {base_url.rstrip('/')}\nSuites: {suite}\nComponents: main\n"
        f"Architectures: {' '.join(sorted(entries))}\nSigned-By: /etc/apt/keyrings/vocal-more-archive-keyring.asc\n"
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--signing-key",
        required=True,
        help="full fingerprint of an existing signing key",
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="intended HTTPS repository root; does not publish",
    )
    parser.add_argument("--suite", choices=("stable", "beta"), default="stable")
    args = parser.parse_args()
    print(
        build(
            args.packages,
            args.output.resolve(),
            args.signing_key,
            args.base_url,
            args.suite,
        )
    )


if __name__ == "__main__":
    main()
