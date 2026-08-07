"""Select the native desktop host for the current operating system."""

from __future__ import annotations

import sys

from . import __version__


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    if sys.platform == "darwin":
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
