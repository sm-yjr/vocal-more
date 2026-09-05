"""Contract tests for the generated React settings application."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_DIR = ROOT / "resources" / "settings"
SETTINGS_HTML = SETTINGS_DIR / "settings.html"
FRONTEND = ROOT / "frontend" / "settings"


def test_vite_build_is_file_url_safe_and_fully_offline():
    html = SETTINGS_HTML.read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="([^"]+)"', html)

    assert references
    assert all(reference.startswith("./assets/") for reference in references)
    assert all(
        (SETTINGS_DIR / reference.removeprefix("./")).is_file()
        for reference in references
    )
    assert "http://" not in html
    assert "https://" not in html
    assert 'src="/' not in html
    assert 'href="/' not in html
    # WKWebView does not execute external ES modules loaded from file://.
    # The production bundle is a single self-contained chunk, so it must be
    # emitted as a deferred classic script for the native settings host.
    assert 'type="module"' not in html
    assert re.search(r'<script defer src="\./assets/[^"]+\.js"></script>', html)


def test_distribution_contains_frontend_third_party_notices():
    notices = SETTINGS_DIR / "THIRD-PARTY-LICENSES.md"

    assert notices.is_file()
    text = notices.read_text(encoding="utf-8")
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    for dependency in package["dependencies"]:
        assert f"## {dependency} -" in text

    shadcn_notice = SETTINGS_DIR / "SHADCN-UI-LICENSE.txt"
    assert shadcn_notice.is_file()
    shadcn_text = shadcn_notice.read_text(encoding="utf-8")
    assert "MIT License" in shadcn_text
    assert "Copyright (c) 2023 shadcn" in shadcn_text
