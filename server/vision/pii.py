"""Server-side PII re-check for OCR output (defense in depth).

The extension already blacks out sensitive regions before the screenshot
leaves the browser. This module is the second line: every OCR string is
re-scanned with the SAME regexes main.py uses for /analyze (they are passed
in by main.py via ``build_router(pii_patterns=RAW_PII_PATTERNS)``, so there
is one source of truth and no circular import). Any match is replaced with a
``[TYPE]`` token before the text is returned or reaches any model.
"""

import re
from typing import Dict, List, Optional, Pattern, Tuple

# Fallback used only when the vision package is exercised standalone (tests,
# benchmarks). Mirrors main.RAW_PII_PATTERNS.
DEFAULT_PII_PATTERNS: Dict[str, Pattern] = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}

_active_patterns: Dict[str, Pattern] = dict(DEFAULT_PII_PATTERNS)


def set_pii_patterns(patterns: Optional[Dict[str, Pattern]]) -> None:
    """Use the server's canonical PII regexes (called once from main.py)."""
    global _active_patterns
    if patterns:
        _active_patterns = dict(patterns)


_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def _apply(text: str, found: List[str]) -> str:
    # card/aadhaar before phone so a 16-digit number is tagged as a card.
    order = sorted(_active_patterns.keys(), key=lambda k: {"card": 0, "aadhaar": 1}.get(k, 2))
    for name in order:
        pattern = _active_patterns[name]
        if pattern.search(text):
            if name not in found:
                found.append(name)
            text = pattern.sub(f"[{name.upper()}]", text)
    return text


def mask_pii_text(text: str) -> Tuple[str, List[str]]:
    """Return (masked_text, [pii types found]).

    OCR often drops spaces ("Phone9876543210forhelp"), which defeats the
    ``\\b``-anchored regexes. So after the normal pass we split letter/digit
    runs apart and scan again; the split text is only returned if that second
    pass actually found something (PAN-like tokens are caught by pass one).
    """
    if not text:
        return text, []
    found: List[str] = []
    masked = _apply(text, found)
    spaced = _LETTER_DIGIT_BOUNDARY.sub(" ", masked)
    if spaced != masked:
        before = len(found)
        respaced = _apply(spaced, found)
        if respaced != spaced or len(found) > before:
            masked = respaced
    return masked, found
