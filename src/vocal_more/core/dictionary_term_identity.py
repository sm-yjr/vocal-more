"""Conservative spelling/pronunciation evidence for an observed correction."""

from __future__ import annotations

import sys
import unicodedata


def same_term_identity(before: str, after: str) -> bool:
    """Compare case or exact romanization; never use fuzzy model similarity.

    Punctuation stays significant (C++ and C are different identifiers).
    Mandarin polyphones may miss this fast path and use repeated confirmation.
    Platforms without Foundation fall back to case-only matching.
    """
    def normalize(value: str) -> str:
        return unicodedata.normalize("NFC", value).casefold()

    if normalize(before) == normalize(after):
        return True
    if sys.platform != "darwin":
        return False
    if not any("\u3400" <= ch <= "\u9fff" for ch in before + after):
        return False
    try:
        from Foundation import NSString

        def romanize(value: str) -> str:
            transformed = NSString.stringWithString_(value).stringByApplyingTransform_reverse_(
                "Any-Latin; Latin-ASCII", False
            )
            return "".join(normalize(str(transformed)).split()) if transformed else ""

        left, right = romanize(before), romanize(after)
        return bool(left) and left == right
    except Exception:
        return False
