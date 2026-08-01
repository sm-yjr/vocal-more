#!/usr/bin/env python3
"""Interactively capture an ABBA Apple-AGC/manual microphone corpus.

This command records private ambient audio. It only runs after explicit terminal
confirmation, writes to the requested directory, and never sends audio over the
network. Running it from Terminal uses Terminal's TCC identity; release decisions
should prefer the same protocol inside Vocal More.app when app-identity parity is
required.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Callable, Sequence
import unicodedata
import wave

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vocal_more import __version__
from vocal_more.audio_quality_benchmark import (
    build_audio_quality_report,
    evaluate_microphone_mode_pairs,
    load_audio_quality_manifest,
    runtime_capture_facts,
    runtime_records_sha256,
)
from vocal_more.core.audio_recorder import AudioRecorder


@dataclass(frozen=True)
class CaptureTrial:
    pair_id: str
    gain_mode: str
    capture_sequence: int


def build_capture_plan(pairs: int) -> list[CaptureTrial]:
    """Return ABBA ordering while keeping one automatic/manual trial per pair."""
    if pairs <= 0 or pairs % 2:
        raise ValueError("pairs must be a positive even number for complete ABBA blocks")
    trials: list[CaptureTrial] = []
    sequence = 1
    for pair_index in range(1, pairs + 1):
        modes = (
            ("automatic", "manual")
            if pair_index % 2 == 1
            else ("manual", "automatic")
        )
        for mode in modes:
            trials.append(
                CaptureTrial(
                    pair_id=f"pair-{pair_index:02d}",
                    gain_mode=mode,
                    capture_sequence=sequence,
                )
            )
            sequence += 1
    return trials


def _load_stimuli(path: Path, *, pairs: int) -> tuple[tuple[str, ...], str]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Cannot read prompt file: {exc}") from exc
    stimuli = tuple(
        unicodedata.normalize("NFC", line.strip())
        for line in content.splitlines()
        if line.strip()
    )
    if len(stimuli) != pairs:
        raise SystemExit(
            f"Prompt file must contain exactly {pairs} non-empty lines; "
            f"received {len(stimuli)}"
        )
    canonical = "\n".join(stimuli).encode("utf-8")
    return stimuli, hashlib.sha256(canonical).hexdigest()


def _write_wav(path: Path, pcm: bytes, *, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as raw_output:
        with wave.open(raw_output, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
    _publish_private_temp(temporary, path, kind="capture output")


def _write_private_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    _publish_private_temp(temporary, path, kind="capture metadata")


def _publish_private_temp(temporary: Path, destination: Path, *, kind: str) -> None:
    """Publish a complete private file without any overwrite race."""

    try:
        os.link(temporary, destination)
    except FileExistsError:
        temporary.unlink()
        raise FileExistsError(
            f"Refusing to overwrite private {kind}: {destination.name}"
        ) from None
    else:
        temporary.unlink()


def _capture_output_paths(output_dir: Path, pairs: int) -> list[Path]:
    paths = [
        output_dir
        / "captures"
        / f"{trial.pair_id}-{trial.gain_mode}.wav"
        for trial in build_capture_plan(pairs)
    ]
    return paths + [
        output_dir / "audio-quality-manifest.yaml",
        output_dir / "audio-quality-report.json",
        output_dir / "capture-runtime-status.json",
    ]


def _require_new_output_paths(output_dir: Path, pairs: int) -> None:
    existing = [
        path for path in _capture_output_paths(output_dir, pairs) if path.exists()
    ]
    if existing:
        names = ", ".join(path.name for path in existing[:3])
        raise FileExistsError(
            "Refusing to overwrite private capture output "
            f"({names}); choose a new output directory"
        )


def _session_status(recorder) -> dict:
    status = recorder.input_status
    last_session = status.get("last_session")
    return dict(last_session) if isinstance(last_session, dict) else dict(status)


def _microphone_mode_value(session: dict, key: str) -> str:
    value = str(session.get(key) or "").strip()
    return value or "unknown"


def _actual_gain_control(session: dict, requested_mode: str) -> str:
    if session.get("phase") != "completed":
        raise RuntimeError("Capture runtime status is not a completed session")
    if session.get("requested_gain_mode") != requested_mode:
        raise RuntimeError(
            "Capture runtime status requested mode does not match the trial"
        )
    if session.get("processing_active") is not True:
        raise RuntimeError("Capture runtime audio processing was not active")
    if session.get("gain_control_verified") is not True:
        raise RuntimeError("Capture runtime gain control was not verified")
    if session.get("output_sample_rate_hz") != 16_000:
        raise RuntimeError("Capture runtime output was not 16 kHz")
    if session.get("output_channels") != 1:
        raise RuntimeError("Capture runtime output was not mono")
    if int(session.get("queue_dropped_blocks") or 0) != 0:
        raise RuntimeError("Capture runtime dropped audio blocks")
    if int(session.get("runtime_fault_count") or 0) != 0 or session.get(
        "runtime_fault_code"
    ):
        raise RuntimeError("Capture runtime reported an audio fault")

    observed = str(session.get("gain_control") or "")
    if requested_mode == "manual":
        if observed != "software":
            raise RuntimeError(
                "Manual trial did not verify the software gain path: "
                f"observed {observed or 'unknown'}"
            )
        if (
            session.get("voice_processing_enabled_observed") is True
            and session.get("agc_enabled_observed") is not False
        ):
            raise RuntimeError("Manual trial did not verify Apple AGC disabled")
        return observed
    if observed == "apple_agc":
        if session.get("voice_processing_enabled_observed") is not True:
            raise RuntimeError("Apple AGC trial did not verify voice processing")
        if session.get("agc_enabled_observed") is not True:
            raise RuntimeError("Apple AGC trial was not verified by runtime state")
        if session.get("fallback_code"):
            raise RuntimeError("Apple AGC trial also reported a fallback")
        return observed
    if observed == "software_fallback":
        if session.get("agc_enabled_observed") is True:
            raise RuntimeError("Software fallback contradicted observed Apple AGC state")
        return observed
    raise RuntimeError(
        "Automatic trial did not report a verified Apple AGC or software fallback path"
    )


def _validate_runtime_capture_facts(facts: dict) -> None:
    """Reject generated provenance when the recorder cannot identify its route."""

    device_name = str(facts.get("device_name") or "").strip()
    if device_name.lower() in {"", "unknown", "unavailable", "none"}:
        raise RuntimeError("Capture runtime did not identify the input device")
    if not isinstance(facts.get("system_default"), bool):
        raise RuntimeError(
            "Capture runtime did not verify whether the input was system default"
        )
    for field in ("native_backend", "processing_mode"):
        value = str(facts.get(field) or "").strip().lower()
        if value in {"", "unknown", "unavailable", "none"}:
            raise RuntimeError(f"Capture runtime did not identify {field}")
    source_rate = facts.get("source_sample_rate_hz")
    if (
        isinstance(source_rate, bool)
        or not isinstance(source_rate, (int, float))
        or not math.isfinite(source_rate)
        or source_rate <= 0
    ):
        raise RuntimeError("Capture runtime source sample rate is invalid")
    for field in ("source_channels", "output_sample_width_bytes"):
        value = facts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError(f"Capture runtime {field} is invalid")
    if facts.get("output_encoding") != "pcm_s16le":
        raise RuntimeError("Capture runtime output encoding was not PCM16 little-endian")
    if not isinstance(facts.get("highpass_effective"), bool):
        raise RuntimeError("Capture runtime high-pass state was not observed")
    for field in (
        "voice_processing_enabled_observed",
        "agc_enabled_observed",
    ):
        if facts.get(field) is not None and not isinstance(facts[field], bool):
            raise RuntimeError(f"Capture runtime {field} is invalid")
    for field in ("queue_dropped_blocks", "runtime_fault_count"):
        value = facts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"Capture runtime {field} is invalid")


def capture_protocol(
    *,
    output_dir: Path,
    pairs: int,
    duration_seconds: float,
    manual_gain: float,
    tags: tuple[str, ...],
    recorder_factory=AudioRecorder,
    prompt: Callable[[CaptureTrial], None],
    sleep: Callable[[float], None] = time.sleep,
    expected_device_name: str | None = None,
) -> list[dict]:
    """Capture a local corpus and retain the runtime-observed audio path."""
    if duration_seconds < 0:
        raise ValueError("duration_seconds cannot be negative")
    output_dir = Path(output_dir).resolve()
    _require_new_output_paths(output_dir, pairs)
    captures_dir = output_dir / "captures"
    recordings: list[dict] = []
    suite_device_identity: tuple[str, bool] | None = None
    for trial in build_capture_plan(pairs):
        prompt(trial)
        recorder = recorder_factory(sample_rate=16000, channels=1, blocksize=640)
        recorder.set_gain_mode(trial.gain_mode)
        recorder.set_gain(manual_gain)
        recorder.set_highpass_filter(True)
        recorder.set_highpass_freq(200)
        recorder.set_soft_limiter(True)
        recorder.start()
        try:
            sleep(duration_seconds)
        except BaseException:
            try:
                recorder.stop()
            except Exception:
                pass
            raise
        pcm = recorder.stop()
        if not pcm:
            raise RuntimeError(
                f"{trial.pair_id}/{trial.gain_mode} captured no audio"
            )
        filename = f"{trial.pair_id}-{trial.gain_mode}.wav"
        audio_path = captures_dir / filename
        session = _session_status(recorder)
        session["output_sample_width_bytes"] = 2
        session["output_encoding"] = "pcm_s16le"
        actual_gain_control = _actual_gain_control(session, trial.gain_mode)
        runtime_facts = runtime_capture_facts(session)
        _validate_runtime_capture_facts(runtime_facts)
        device_identity = (
            runtime_facts["device_name"],
            runtime_facts["system_default"],
        )
        if expected_device_name is not None and (
            runtime_facts["device_name"] != expected_device_name.strip()
        ):
            raise RuntimeError(
                "Capture runtime device does not match --microphone: "
                f"expected {expected_device_name.strip()!r}, observed "
                f"{runtime_facts['device_name']!r}"
            )
        if suite_device_identity is None:
            suite_device_identity = device_identity
        elif device_identity != suite_device_identity:
            raise RuntimeError(
                "Capture runtime device identity changed during the suite: "
                f"expected {suite_device_identity!r}, observed {device_identity!r}"
            )
        # Persist private audio only after the session has a trustworthy status.
        _write_wav(audio_path, pcm)
        recordings.append(
            {
                "id": f"{trial.pair_id}-{trial.gain_mode}",
                "pair_id": trial.pair_id,
                "audio": f"captures/{filename}",
                "capture_sequence": trial.capture_sequence,
                "requested_gain_mode": trial.gain_mode,
                "actual_gain_control": actual_gain_control,
                "preferred_microphone_mode": _microphone_mode_value(
                    session,
                    "preferred_microphone_mode",
                ),
                "active_microphone_mode": _microphone_mode_value(
                    session,
                    "active_microphone_mode",
                ),
                **runtime_facts,
                "tags": list(tags),
                "runtime_status": session,
            }
        )
    return recordings


def write_capture_outputs(
    *,
    output_dir: Path,
    recordings: list[dict],
    environment: dict,
    suite_id: str,
) -> tuple[Path, Path, Path]:
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = output_dir / "audio-quality-manifest.yaml"
    report_path = output_dir / "audio-quality-report.json"
    runtime_path = output_dir / "capture-runtime-status.json"
    existing = [
        path for path in (manifest_path, report_path, runtime_path) if path.exists()
    ]
    if existing:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(
            "Refusing to overwrite private capture metadata "
            f"({names}); choose a new output directory"
        )
    actual_devices = {
        (
            str(recording.get("device_name") or "").strip(),
            recording.get("system_default"),
        )
        for recording in recordings
    }
    if len(actual_devices) != 1:
        raise RuntimeError(
            "Capture runtime device identity changed or was not recorded"
        )
    actual_device_name, system_default = next(iter(actual_devices))
    if not actual_device_name or not isinstance(system_default, bool):
        raise RuntimeError("Capture runtime device identity is incomplete")

    manifest_recordings = [
        {key: value for key, value in recording.items() if key != "runtime_status"}
        for recording in recordings
    ]
    runtime_sessions = [
        {
            "id": recording["id"],
            "runtime_status": recording.get("runtime_status", {}),
        }
        for recording in recordings
    ]
    runtime_hash = runtime_records_sha256(runtime_sessions)
    actual_environment = dict(environment)
    actual_environment["microphone"] = actual_device_name
    manifest = {
        "schema_version": 1,
        "suite_id": suite_id,
        "description": "Physical microphone Apple AGC versus manual gain ABBA capture",
        "provenance": {
            "kind": "vocal_more_runtime_capture",
            "capture_tool": "scripts/capture_audio_quality_ab.py",
            "capture_schema_version": 2,
            "runtime_sidecar": runtime_path.name,
            "runtime_records_sha256": runtime_hash,
        },
        "environment": actual_environment,
        "analysis": {
            "frame_ms": 20,
            "silence_threshold_dbfs": -50,
            "clipping_threshold": 0.999,
        },
        "recordings": manifest_recordings,
    }
    _write_private_text(
        manifest_path,
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
    )
    pending = load_audio_quality_manifest(
        manifest_path,
        _verify_runtime_provenance=False,
    )
    mode_validation = evaluate_microphone_mode_pairs(manifest_recordings)
    validation_by_pair = {
        pair["pair_id"]: pair for pair in mode_validation["pairs"]
    }
    _write_private_text(
        runtime_path,
        json.dumps(
            {
                "schema_version": 2,
                "manifest_fingerprint": pending.fingerprint,
                "runtime_records_sha256": runtime_hash,
                "sessions": [
                    {
                        **session,
                        "pair_validation": validation_by_pair[
                            recording["pair_id"]
                        ],
                    }
                    for recording, session in zip(
                        recordings,
                        runtime_sessions,
                        strict=True,
                    )
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    loaded = load_audio_quality_manifest(manifest_path)
    report = build_audio_quality_report(loaded)
    _write_private_text(
        report_path,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest_path, report_path, runtime_path


def _hardware_model() -> str:
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.model"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        value = result.stdout.strip()
        if value:
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.machine() or "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=10)
    parser.add_argument(
        "--prompt-file",
        type=Path,
        required=True,
        help="Private UTF-8 text file with exactly one phrase per pair",
    )
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--manual-gain-db", type=float, default=18.0)
    parser.add_argument("--suite-id", default="built-in-microphone-whisper-ab")
    parser.add_argument(
        "--microphone",
        help=(
            "Expected exact runtime device name; capture aborts on mismatch. "
            "The manifest always records the observed name."
        ),
    )
    parser.add_argument("--room", default="unspecified")
    parser.add_argument("--ambient-condition", default="unspecified")
    parser.add_argument("--distance-cm", type=float)
    parser.add_argument("--tags", default="whisper")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the explicit terminal confirmation (for controlled automation)",
    )
    return parser


def _validate_cli_arguments(args: argparse.Namespace) -> None:
    try:
        build_capture_plan(args.pairs)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not math.isfinite(args.duration) or args.duration < 2:
        raise SystemExit(
            "Capture duration must be at least 2 seconds for leading and trailing quiet"
        )
    if not math.isfinite(args.manual_gain_db) or not -6 <= args.manual_gain_db <= 34:
        raise SystemExit("Manual gain must be between -6 dB and +34 dB")
    if args.distance_cm is not None and (
        not math.isfinite(args.distance_cm) or args.distance_cm <= 0
    ):
        raise SystemExit("Microphone distance must be positive")
    if not args.suite_id.strip():
        raise SystemExit("Suite id cannot be empty")
    if args.microphone is not None and not args.microphone.strip():
        raise SystemExit("Expected microphone name cannot be empty")
    if not any(tag.strip() for tag in args.tags.split(",")):
        raise SystemExit("At least one non-empty tag is required")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if platform.system() != "Darwin":
        raise SystemExit("Physical microphone A/B capture requires macOS")
    _validate_cli_arguments(args)
    stimuli, stimulus_set_sha256 = _load_stimuli(
        args.prompt_file,
        pairs=args.pairs,
    )

    output_dir = args.output_dir.resolve()
    try:
        output_dir.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit(
            "Refusing to store private microphone captures inside the repository; "
            "choose a directory such as /tmp/vocal-more-private-ab"
        )
    _require_new_output_paths(output_dir, args.pairs)

    if not args.yes:
        print(
            "This protocol records private ambient audio to the selected directory.\n"
            "It does not upload audio or copy prompt text into the report. "
            "Type RECORD to continue: ",
            end="",
            flush=True,
        )
        if input().strip() != "RECORD":
            print("Capture cancelled.")
            return 2

    tags = tuple(tag.strip() for tag in args.tags.split(",") if tag.strip())
    manual_gain = math.pow(10.0, args.manual_gain_db / 20.0)
    stimuli_by_pair = {
        f"pair-{index:02d}": stimulus
        for index, stimulus in enumerate(stimuli, start=1)
    }

    def prompt(trial: CaptureTrial) -> None:
        stimulus = stimuli_by_pair[trial.pair_id]
        input(
            f"\nTrial {trial.capture_sequence}: {trial.pair_id} / "
            f"{trial.gain_mode}. Keep 1 s quiet, say “{stimulus}”, then keep "
            "1 s quiet. Press Enter to start."
        )

    recordings = capture_protocol(
        output_dir=args.output_dir,
        pairs=args.pairs,
        duration_seconds=args.duration,
        manual_gain=manual_gain,
        tags=tags,
        prompt=prompt,
        expected_device_name=args.microphone,
    )
    environment = {
        "hardware_model": _hardware_model(),
        "microphone": "resolved from capture runtime",
        "macos_version": platform.mac_ver()[0] or "unknown",
        "app_version": __version__,
        "room": args.room,
        "ambient_condition": args.ambient_condition,
        "capture_protocol": {
            "order": "ABBA",
            "pair_count": args.pairs,
            "duration_seconds": args.duration,
            "manual_gain_db": args.manual_gain_db,
            "highpass_enabled": True,
            "highpass_hz": 200,
            "soft_limiter_enabled": True,
            "capture_host": "terminal_process",
            "stimulus_count": len(stimuli),
            "stimulus_set_sha256": stimulus_set_sha256,
            "stimulus_text_persisted": False,
        },
    }
    if args.distance_cm is not None:
        environment["microphone_distance_cm"] = args.distance_cm
    manifest, report, runtime = write_capture_outputs(
        output_dir=args.output_dir,
        recordings=recordings,
        environment=environment,
        suite_id=args.suite_id,
    )
    print(f"Manifest: {manifest}")
    print(f"Report: {report}")
    print(f"Runtime status: {runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
