"""Real signed APT indexes and upgrade resolution, isolated from system APT."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / f"packaging/linux/{name}.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_debian_version_orders_builds_betas_and_stable():
    version = module("build_deb").debian_version

    def project(text, build):
        return {"project": {"version": text}, "tool": {"vocal-more": {"build": build}}}

    assert version(project("0.6.1", 2)) == "0.6.1-2"
    assert version(project("0.6.2b1", 3)) == "0.6.2~beta.1-3"
    if shutil.which("dpkg"):
        for left, right in (
            ("0.6.1-2", "0.6.1-10"),
            ("0.6.2~beta.1-3", "0.6.2~beta.2-4"),
            ("0.6.2~beta.2-4", "0.6.2-5"),
        ):
            subprocess.run(
                ["dpkg", "--compare-versions", left, "lt", right], check=True
            )


@pytest.fixture
def apt_tools():
    if sys.platform != "linux" or any(
        not shutil.which(tool) for tool in ("apt-get", "gpg", "dpkg-deb", "dpkg")
    ):
        pytest.skip("Linux APT, GnuPG and dpkg are required")


def deb(directory, version):
    stage = directory / ("stage-" + version)
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "DEBIAN/control").write_text(
        f"Package: vocal-more\nVersion: {version}\nArchitecture: amd64\nMaintainer: Test <test@example.invalid>\nDescription: isolated APT fixture\n"
    )
    package = directory / f"vocal-more_{version}_amd64.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "--build", str(stage), str(package)],
        check=True,
        capture_output=True,
    )
    return package


@pytest.fixture
def signing_key(tmp_path, monkeypatch, apt_tools):
    directory = tmp_path / "gnupg"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(directory))
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "Vocal More isolated test <test@example.invalid>",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
        capture_output=True,
    )
    keys = subprocess.check_output(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"], text=True
    )
    return next(
        line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:")
    )


def test_signed_repository_resolves_upgrade_and_rejects_tampering(
    tmp_path, signing_key
):
    import pwd

    builder = module("build_apt_repository")
    package = deb(tmp_path, "1.0-2")
    repository = builder.build(
        [package],
        tmp_path / "repository",
        signing_key,
        "https://apt.example.invalid/vocal-more",
    )
    configuration = tmp_path / "apt"
    for path in ("etc", "state/lists/partial", "cache/archives/partial"):
        (configuration / path).mkdir(parents=True, exist_ok=True)
    (configuration / "state/status").write_text(
        "Package: vocal-more\nStatus: install ok installed\nPriority: optional\nSection: sound\nInstalled-Size: 1\nMaintainer: Test <test@example.invalid>\nArchitecture: amd64\nVersion: 1.0-1\nDescription: prior installed fixture\n\n"
    )
    key = repository / "vocal-more-archive-keyring.asc"
    # Only this test's key and file repository are visible to this APT process.
    (configuration / "etc/sources.list").write_text(
        f"deb [signed-by={key}] file:{repository} stable main\n"
    )
    apt_config = configuration / "apt.conf"
    apt_config.write_text(f'''Dir "{configuration}";
Dir::Etc "etc";
Dir::Etc::parts "-";
Dir::Etc::main "-";
Dir::Etc::sourceparts "-";
Dir::Etc::trusted "-";
Dir::Etc::trustedparts "-";
Dir::State "state";
Dir::State::status "{configuration}/state/status";
Dir::Cache "cache";
APT::Architecture "amd64";
APT::Architectures {{"amd64";}};
APT::Sandbox::User "{pwd.getpwuid(os.getuid()).pw_name}";
Debug::NoLocking "true";
''')
    environment = {**os.environ, "APT_CONFIG": str(apt_config), "LC_ALL": "C"}
    update = subprocess.run(
        ["apt-get", "update"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert update.returncode == 0, update.stdout + update.stderr
    upgrade = subprocess.run(
        ["apt-get", "--simulate", "upgrade"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    assert "Inst vocal-more [1.0-1] (1.0-2" in upgrade.stdout
    # A changed signed index must fail authentication, never use a trusted=yes bypass.
    (repository / "dists/stable/InRelease").write_text(
        (repository / "dists/stable/InRelease")
        .read_text()
        .replace("Origin: Vocal More", "Origin: Tampered")
    )
    shutil.rmtree(configuration / "state/lists")
    (configuration / "state/lists/partial").mkdir(parents=True)
    changed = subprocess.run(
        ["apt-get", "update"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert changed.returncode != 0
    assert "signature" in (changed.stdout + changed.stderr).lower()


def test_stable_suite_rejects_beta_and_replacing_candidates(tmp_path, apt_tools):
    builder = module("build_apt_repository")
    beta = deb(tmp_path, "0.7.0~beta.1-3")
    with pytest.raises(ValueError, match="beta"):
        builder.build(
            [beta], tmp_path / "stable", "A" * 40, "https://apt.example.invalid"
        )
    assert not (tmp_path / "stable").exists()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(ValueError, match="new directory"):
        builder.build([beta], occupied, "A" * 40, "https://apt.example.invalid", "beta")
