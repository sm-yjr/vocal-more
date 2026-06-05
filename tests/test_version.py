"""Version metadata should have one source of truth."""

from pathlib import Path
import sys
import types

import vocal_more
from vocal_more import __version__
from tests.project_metadata import read_project_version


def test_runtime_version_matches_project_metadata():
    assert __version__ == read_project_version(Path(__file__).resolve().parents[1])


def test_bundle_version_takes_precedence_for_packaged_app(monkeypatch):
    """py2app builds should display the Info.plist version even without dist metadata."""

    class FakeInfo:
        def objectForKey_(self, key):
            return {
                "CFBundleIdentifier": "com.sm-yjr.vocal-more",
                "CFBundleShortVersionString": "9.8.7",
            }.get(key)

    class FakeBundle:
        @staticmethod
        def infoDictionary():
            return FakeInfo()

    foundation = types.ModuleType("Foundation")
    foundation.NSBundle = types.SimpleNamespace(mainBundle=lambda: FakeBundle())
    monkeypatch.setitem(sys.modules, "Foundation", foundation)

    assert vocal_more._version_from_bundle() == "9.8.7"
