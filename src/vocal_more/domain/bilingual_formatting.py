"""Deterministic bilingual (Chinese/English) text formatting.

Pure-local, zero-cost post-processing applied to dictation output so that
basic CJK–Latin spacing correctness does not depend on LLM polishing.

The rules are deliberately conservative — lossless, high-confidence
transformations only:

* Insert one ASCII space at CJK ↔ ASCII letter/digit boundaries.
* Convert full-width ASCII letters/digits (ＡＢＣ１２３) to half-width.
* Collapse runs of repeated Chinese sentence punctuation (。！？，、).

Explicitly out of scope (too risky to do deterministically): full/half-width
punctuation conversion, quote handling, English intra-sentence punctuation.

Protected segments are never modified: URLs (``scheme://`` or ``www.``),
path-like tokens (any whitespace-delimited run containing ``/`` or ``\\``),
backtick-wrapped inline code, and the interior of contiguous ASCII runs.
"""

from __future__ import annotations

import re

# CJK Unified Ideographs (U+4E00-U+9FFF) plus Extension A (U+3400-U+4DBF).
_CJK = "\u3400-\u4dbf\u4e00-\u9fff"
_ASCII_ALNUM = "0-9A-Za-z"

_CJK_THEN_ALNUM_RE = re.compile(rf"([{_CJK}])([{_ASCII_ALNUM}])")
_ALNUM_THEN_CJK_RE = re.compile(rf"([{_ASCII_ALNUM}])([{_CJK}])")

# Runs of the *same* Chinese sentence punctuation collapse to one.
_REPEATED_PUNCT_RE = re.compile(r"([。！？，、])\1+")

# Protected segments, matched before any transformation:
#   1. backtick-wrapped inline code;
#   2. any whitespace-delimited run containing "/" or "\" — this covers
#      path-like tokens as well as scheme:// URLs;
#   3. scheme-less "www." URLs.
_PROTECTED_RE = re.compile(
    r"`[^`]*`"
    r"|\S*[/\\]\S*"
    r"|www\.\S*"
)

# Full-width digits / uppercase / lowercase ASCII letters -> half-width.
# Punctuation (e.g. ！ ， ？) is intentionally NOT converted.
_FULLWIDTH_TO_HALFWIDTH = str.maketrans(
    {
        chr(code): chr(code - 0xFEE0)
        for code in (
            list(range(0xFF10, 0xFF1A))  # ０-９
            + list(range(0xFF21, 0xFF3B))  # Ａ-Ｚ
            + list(range(0xFF41, 0xFF5B))  # ａ-ｚ
        )
    }
)


def _format_segment(segment: str) -> str:
    """Apply the formatting rules to a single unprotected segment."""
    segment = segment.translate(_FULLWIDTH_TO_HALFWIDTH)
    segment = _CJK_THEN_ALNUM_RE.sub(r"\1 \2", segment)
    segment = _ALNUM_THEN_CJK_RE.sub(r"\1 \2", segment)
    segment = _REPEATED_PUNCT_RE.sub(r"\1", segment)
    return segment


def format_bilingual_text(text: str) -> str:
    """Format bilingual dictation output deterministically.

    Pure function: no state, no I/O. Segments matched by ``_PROTECTED_RE``
    (URLs, path-like tokens, inline code) pass through untouched; the rules
    are applied only to the text between them.
    """
    if not text:
        return text

    parts: list[str] = []
    last_end = 0
    for match in _PROTECTED_RE.finditer(text):
        start, end = match.span()
        if start > last_end:
            parts.append(_format_segment(text[last_end:start]))
        parts.append(match.group())
        last_end = end
    if last_end < len(text):
        parts.append(_format_segment(text[last_end:]))
    return "".join(parts)
