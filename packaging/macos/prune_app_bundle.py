#!/usr/bin/env python3
"""Remove development-only Python payloads from a completed py2app bundle."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable


_REMOVABLE_DIRECTORY_NAMES = {"test", "tests"}
_REMOVABLE_TEST_PACKAGE_NAMES = {"certifi", "numpy"}
_REMOVABLE_CACHE_DIRECTORY_NAMES = {"__pycache__"}
_REMOVABLE_FILE_NAMES = {"py.typed"}
_REMOVABLE_FILE_SUFFIXES = {".pyi"}
_REMOVABLE_PACKAGE_RESOURCES = {
    # DashScope loads this tokenizer table only through its optional tokenizer
    # API. Vocal More never tokenizes locally; ASR and LLM calls are remote.
    ("dashscope", "resources", "qwen.tiktoken"),
}
_SUPPORTED_ARCHITECTURES = {"arm64", "x86_64"}
_MACHO_MAGICS = {
    bytes.fromhex(value)
    for value in (
        "cafebabe",
        "cafebabf",
        "bebafeca",
        "bfbafeca",
        "feedface",
        "feedfacf",
        "cefaedfe",
        "cffaedfe",
    )
}


def _contained_files(path: Path) -> list[Path]:
    if path.is_symlink() or path.is_file():
        return [path]
    return [candidate for candidate in path.rglob("*") if candidate.is_file()]


def _remove_path(path: Path) -> tuple[int, int]:
    files = _contained_files(path)
    byte_count = sum(
        file.stat().st_size
        for file in files
        if not file.is_symlink()
    )
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return len(files), byte_count


def _is_removable_test_directory(path: Path, python_root: Path) -> bool:
    if path.name in _REMOVABLE_CACHE_DIRECTORY_NAMES:
        return True
    relative_parts = path.relative_to(python_root).parts
    if len(relative_parts) == 1:
        return path.name in _REMOVABLE_DIRECTORY_NAMES
    return (
        path.name in _REMOVABLE_DIRECTORY_NAMES
        and relative_parts[0] in _REMOVABLE_TEST_PACKAGE_NAMES
    )


def prune_app_bundle(app_path: Path) -> tuple[int, int]:
    """Prune safe development artifacts below bundled Python libraries."""
    app_path = app_path.resolve()
    if app_path.suffix != ".app" or not app_path.is_dir():
        raise ValueError(f"expected a .app bundle, got: {app_path}")

    library_root = app_path / "Contents" / "Resources" / "lib"
    python_roots = sorted(
        path
        for path in library_root.glob("python*")
        if path.is_dir()
    )
    if not python_roots:
        raise ValueError(f"bundle has no Python runtime library: {library_root}")

    removed_files = 0
    removed_bytes = 0
    for python_root in python_roots:
        removable_directories = sorted(
            (
                path
                for path in python_root.rglob("*")
                if path.is_dir()
                and _is_removable_test_directory(path, python_root)
            ),
            key=lambda path: len(path.parts),
        )
        for directory in removable_directories:
            if not directory.exists():
                continue
            file_count, byte_count = _remove_path(directory)
            removed_files += file_count
            removed_bytes += byte_count

        removable_files = [
            path
            for path in python_root.rglob("*")
            if path.is_file()
            and (
                path.name in _REMOVABLE_FILE_NAMES
                or path.suffix in _REMOVABLE_FILE_SUFFIXES
            )
        ]
        for file_path in removable_files:
            file_count, byte_count = _remove_path(file_path)
            removed_files += file_count
            removed_bytes += byte_count

        for relative_parts in _REMOVABLE_PACKAGE_RESOURCES:
            resource = python_root.joinpath(*relative_parts)
            if not resource.exists():
                continue
            file_count, byte_count = _remove_path(resource)
            removed_files += file_count
            removed_bytes += byte_count

    return removed_files, removed_bytes


def _has_macho_magic(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in _MACHO_MAGICS
    except OSError:
        return False


def _macho_candidates(app_path: Path):
    contents = app_path / "Contents"
    for path in contents.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if "Sparkle.framework" in path.parts:
            continue
        if (
            path.suffix in {".dylib", ".so"}
            or path.name == "Python"
            or os.access(path, os.X_OK)
            or _has_macho_magic(path)
        ):
            yield path


def _read_architectures(
    binary: Path,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess],
) -> set[str] | None:
    result = command_runner(
        ["/usr/bin/lipo", "-archs", str(binary)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if _has_macho_magic(binary):
            details = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"failed to inspect Mach-O file {binary}: {details}")
        return None
    return set(result.stdout.split())


def thin_macho_binaries(
    app_path: Path,
    *,
    target_arch: str,
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[int, int]:
    """Remove non-target slices from bundled Mach-O files except Sparkle."""
    if target_arch not in _SUPPORTED_ARCHITECTURES:
        raise ValueError(f"unsupported target architecture: {target_arch}")
    app_path = app_path.resolve()
    if app_path.suffix != ".app" or not app_path.is_dir():
        raise ValueError(f"expected a .app bundle, got: {app_path}")

    thinned_count = 0
    bytes_saved = 0
    for binary in _macho_candidates(app_path):
        architectures = _read_architectures(
            binary,
            command_runner=command_runner,
        )
        if architectures is None:
            continue
        if target_arch not in architectures:
            raise RuntimeError(
                f"Mach-O file {binary} does not contain target architecture "
                f"{target_arch}: {sorted(architectures)}"
            )
        if len(architectures) < 2:
            continue

        before_size = binary.stat().st_size
        mode = stat.S_IMODE(binary.stat().st_mode)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{binary.name}.thin-",
            dir=str(binary.parent),
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        temporary_path.unlink()
        try:
            thin_result = command_runner(
                [
                    "/usr/bin/lipo",
                    str(binary),
                    "-thin",
                    target_arch,
                    "-output",
                    str(temporary_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if thin_result.returncode != 0 or not temporary_path.exists():
                details = (thin_result.stderr or thin_result.stdout).strip()
                raise RuntimeError(f"failed to thin {binary}: {details}")
            temporary_path.chmod(mode)
            after_size = temporary_path.stat().st_size
            os.replace(temporary_path, binary)
        finally:
            temporary_path.unlink(missing_ok=True)
        thinned_count += 1
        bytes_saved += max(0, before_size - after_size)

    for binary in _macho_candidates(app_path):
        architectures = _read_architectures(
            binary,
            command_runner=command_runner,
        )
        if architectures is None:
            continue
        if architectures != {target_arch}:
            raise RuntimeError(
                f"Mach-O architecture validation failed for {binary}: "
                f"expected only {target_arch}, got {sorted(architectures)}"
            )

    return thinned_count, bytes_saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="Path to the py2app .app bundle")
    parser.add_argument(
        "--target-arch",
        choices=sorted(_SUPPORTED_ARCHITECTURES),
        help="Thin non-Sparkle Mach-O files to this architecture",
    )
    args = parser.parse_args(argv)
    try:
        removed_files, removed_bytes = prune_app_bundle(args.app)
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"Removed {removed_files} files "
        f"({removed_bytes / (1024 * 1024):.1f} MiB) from {args.app}"
    )
    if args.target_arch:
        try:
            thinned_count, thinned_bytes = thin_macho_binaries(
                args.app,
                target_arch=args.target_arch,
            )
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(
            f"Thinned {thinned_count} Mach-O files to {args.target_arch} "
            f"({thinned_bytes / (1024 * 1024):.1f} MiB saved)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
