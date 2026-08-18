"""Launcher used by the py2app bundle."""

from __future__ import annotations

import os


def _main() -> None:
    if os.environ.get("VOCAL_MORE_PACKAGING_SMOKE_TEST") == "1":
        import numpy  # noqa: F401

        from vocal_more.core.audio_recorder import AudioRecorder  # noqa: F401

        print("Vocal More packaging smoke test passed")
        return

    from vocal_more.app import main

    main()


if __name__ == "__main__":
    _main()
