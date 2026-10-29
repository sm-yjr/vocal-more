#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit live, billable DashScope verification with synthetic fixture data.

Credentials are read from the ignored input file. The disposable backend
profile is scrubbed and deleted; no credentials enter reports or build artifacts. Run local OS fixtures first.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "cloud.json").unlink(missing_ok=True)
    frames = args.frames.resolve()
    evidence = args.evidence.resolve()
    assert all((frames / f"frame-{n}.jpg").is_file() for n in range(3))
    assert evidence.is_file(), "Run smoke_linux_gnome_capsule.py first"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "flite=text='I am working on Luma Harbor. The verification code is V M eight six one seven.':voice=slt",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            str(output / "speech.pcm"),
        ],
        check=True,
        timeout=15,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "flite=text='Please read the verification code shown on the screen.':voice=slt",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            str(output / "visual-query.pcm"),
        ],
        check=True,
        timeout=15,
    )
    env = os.environ.copy()
    env.pop("DASHSCOPE_API_KEY", None)
    env.update(
        VOCAL_MORE_LIVE_CLOUD_TEST="1",
        VOCAL_MORE_CLOUD_OUTPUT=str(output),
        VOCAL_MORE_CLOUD_KEY_FILE=str(args.key_file.resolve()),
        VOCAL_MORE_CLOUD_FRAMES=str(frames),
        VOCAL_MORE_CLOUD_EVIDENCE=str(evidence),
    )
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    with (output / "test.log").open("w") as log:
        result = subprocess.run(
            [
                cargo,
                "test",
                "--locked",
                "--manifest-path",
                str(ROOT / "rust/Cargo.toml"),
                "-p",
                "vocal-more-desktop",
                "--lib",
                "linux_live_cloud_screen_and_learning",
                "--",
                "--ignored",
                "--nocapture",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=150,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(
            "Live cloud acceptance failed; inspect cloud.json and test.log"
        )
    print(
        "PASS: live screen/audio recognition, cloud correction classifier, dictionary apply and undo"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".build/linux-cloud-acceptance"
    )
    run(parser.parse_args())
