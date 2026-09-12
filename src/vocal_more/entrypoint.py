"""Select the native desktop host for the current operating system."""

from __future__ import annotations

import sys

from . import __version__


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    if sys.platform == "darwin":
        from .rust_client import backend_paths
        explicit = sys.argv[sys.argv.index("--backend") + 1] if "--backend" in sys.argv and sys.argv.index("--backend") + 1 < len(sys.argv) else None
        if explicit not in (None, "rust", "python"):
            raise SystemExit("--backend must be rust or python")
        binary, _ = backend_paths()
        if explicit == "rust" or (explicit is None and binary.is_file()):
            from .rust_ui import main as rust_main
            rust_main()
            return
        if explicit == "python":
            index = sys.argv.index("--backend")
            del sys.argv[index:index + 2]
        from .app import main as platform_main
    elif sys.platform == "win32":
        from .windows_app import main as platform_main
    else:
        raise SystemExit(
            "Vocal More currently provides desktop hosts for macOS and Windows. "
            "Use `python -m vocal_more.serve` for the headless RPC service."
        )
    platform_main()


if __name__ == "__main__":
    main()
