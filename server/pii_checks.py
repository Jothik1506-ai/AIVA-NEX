"""
PII detection shared logic (server side).

This file mirrors extension/pii-checks.js line for line - same patterns,
same validators, same two-pass overlap rule - so the browser and the server
agree on what counts as PII. If you change one, change the other and run
both test suites (see README "Running the tests").

Policy (documented choice):
  * Every *candidate* (right shape) is tokenised by the extension - over-
    redaction is cheap and safe.
  * The server only HARD-REJECTS (HTTP 400) a payload that still contains a
    *validated* item:
      - AADHAAR : 12 digits, first digit 2-9, Verhoeff checksum passes
      - CARD    : 13-19 digits (spaces/dashes allowed), Luhn checksum passes
      - PAN     : [A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]
      - PHONE   : optional +91/91, then 10 digits starting 6-9
      - EMAIL   : standard address shape
    A random 12-digit order number that fails Verhoeff, or a 16-digit
    reference that fails Luhn, is therefore NOT a reason to reject a request.
"""

import re
from typing import Any, Dict, Iterable, List

# --- Verhoeff (Aadhaar check digit) -----------------------------------------
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def verhoeff_valid(number: str) -> bool:
    digits = _digits(number)
    if not digits:
        return False
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    """Check digit to append to `number` so the result passes verhoeff_valid."""
    c = 0
    for i, ch in enumerate(reversed(_digits(number))):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return str(_VERHOEFF_INV[c])


def luhn_valid(number: str) -> bool:
    digits = _digits(number)
    if not digits:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


PAN_STRICT = re.compile(r"^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$")


def pan_valid(value: str) -> bool:
    return bool(PAN_STRICT.match((value or "").upper()))


def aadhaar_valid(value: str) -> bool:
    d = _digits(value)
    return len(d) == 12 and d[0] in "23456789" and verhoeff_valid(d)


def card_valid(value: str) -> bool:
    d = _digits(value)
    return 13 <= len(d) <= 19 and luhn_valid(d)


# --- Candidate patterns (must stay identical to extension/pii-checks.js) ----
# Digit runs are bounded with (?<!\d)(?<!\d[ -]) / (?![ -]?\d) instead of \b
# so a match can't start or end in the middle of a longer, separator-broken
# digit run (e.g. the last 12 digits of a 16-digit card).
CANDIDATE_PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CARD", re.compile(r"(?<!\d)(?<!\d[ -])\d(?:[ -]?\d){12,18}(?![ -]?\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)")),
    ("PAN", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")),
    ("PHONE", re.compile(r"(?<![\d+])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?![ -]?\d)")),
]

_VALIDATORS = {
    "EMAIL": lambda s: True,
    "CARD": card_valid,
    "AADHAAR": aadhaar_valid,
    "PAN": pan_valid,
    "PHONE": lambda s: True,
}


def detect_pii(text: str) -> List[Dict[str, Any]]:
    """All non-overlapping PII spans in `text`, sorted by position.

    Pass 1 accepts validated matches (priority = CANDIDATE_PATTERNS order),
    pass 2 then adds unvalidated candidates that don't overlap anything
    already claimed. Validated items therefore always win an overlap.
    Each item: {"type", "start", "end", "validated"}.
    """
    if not text:
        return []
    found = []
    for type_, pattern in CANDIDATE_PATTERNS:
        for m in pattern.finditer(text):
            found.append(
                {
                    "type": type_,
                    "start": m.start(),
                    "end": m.end(),
                    "validated": bool(_VALIDATORS[type_](m.group(0))),
                }
            )
    claimed: List[Dict[str, Any]] = []
    for want_validated in (True, False):
        for item in found:
            if item["validated"] != want_validated:
                continue
            if any(item["start"] < c["end"] and item["end"] > c["start"] for c in claimed):
                continue
            claimed.append(item)
    claimed.sort(key=lambda it: it["start"])
    return claimed


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v)


def find_validated_pii_types(payload: Any) -> List[str]:
    """Lower-case type names of VALIDATED PII still present anywhere in payload.

    Only these trigger the server's hard reject. Never returns the matched
    values themselves, so the result is safe to put in an error message.
    """
    types = set()
    for s in _iter_strings(payload):
        for item in detect_pii(s):
            if item["validated"]:
                types.add(item["type"].lower())
    order = ["email", "phone", "aadhaar", "pan", "card"]
    return [t for t in order if t in types]
