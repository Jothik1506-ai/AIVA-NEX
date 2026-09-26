"""
Consistent NAME_n / ADDRESS_n tokenisation, and payload-wide sanitisation.

One `Tokenizer` lives for one request: the same person/address always maps to
the same token within it ("Mr. Ravi Kumar" and "Ravi Kumar" -> NAME_1), and
numbering starts after any NAME_n / ADDRESS_n tokens already present in the
payload so server tokens never collide with ones the extension produced.
"""

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .detector import detect_batch, detect_spans
from .types import Span

_EXISTING_TOKEN_RE = re.compile(r"\b(NAME|ADDRESS)_(\d+)\b")
_HONORIFIC_PREFIX_RE = re.compile(
    r"^(?:mr|mrs|ms|mx|shri|shrimati|sri|smt|kumari|km|dr|prof|thiru|thirumathi|selvi)\b\.?\s*"
)

# Keys whose values are structural metadata, never free text a person wrote.
SKIP_KEYS = {
    "ref", "type", "domain", "hrefDomain", "scannedAt", "model", "position",
    "fieldRefs", "detectedTypes", "sensitiveType", "role", "required", "isSensitive",
    "sensitiveItemsCount",
}


def _normalise(kind: str, surface: str) -> str:
    key = re.sub(r"[^\w\s/-]", " ", surface.casefold())
    key = re.sub(r"\s+", " ", key).strip()
    if kind == "NAME":
        key = _HONORIFIC_PREFIX_RE.sub("", key)
    return key


class Tokenizer:
    def __init__(self, reserved_text: str = ""):
        self._map: Dict[Tuple[str, str], str] = {}
        self._counts = {"NAME": 0, "ADDRESS": 0}
        for kind, n in _EXISTING_TOKEN_RE.findall(reserved_text or ""):
            self._counts[kind] = max(self._counts[kind], int(n))
        self.found = {"NAME": 0, "ADDRESS": 0}

    def token_for(self, kind: str, surface: str) -> str:
        key = (kind, _normalise(kind, surface))
        tok = self._map.get(key)
        if tok is None:
            self._counts[kind] += 1
            tok = f"{kind}_{self._counts[kind]}"
            self._map[key] = tok
        self.found[kind] += 1
        return tok

    def apply(self, text: str, spans: Iterable[Span]) -> str:
        out, cursor = [], 0
        for sp in sorted(spans, key=lambda s: s.start):
            if sp.start < cursor:
                continue
            out.append(text[cursor:sp.start])
            out.append(self.token_for(sp.type, text[sp.start:sp.end]))
            cursor = sp.end
        out.append(text[cursor:])
        return "".join(out)


def tokenize_text(text: str, tokenizer: Optional[Tokenizer] = None) -> Tuple[str, List[Span]]:
    """Replace NAME/ADDRESS spans in `text`; returns (tokenised_text, spans)."""
    tokenizer = tokenizer or Tokenizer(text)
    spans = detect_spans(text)
    return tokenizer.apply(text, spans), spans


def _collect(obj: Any, path: tuple, sink: List[Tuple[tuple, str]]):
    if isinstance(obj, str):
        sink.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP_KEYS:
                _collect(v, path + (k,), sink)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect(v, path + (i,), sink)


def _set(obj: Any, path: tuple, value: str) -> Any:
    if not path:
        return value
    head, rest = path[0], path[1:]
    obj[head] = _set(obj[head], rest, value)
    return obj


def sanitize_payload(payload: Any) -> Tuple[Any, Dict[str, int]]:
    """Tokenise NAME/ADDRESS spans in every free-text string of a JSON-like
    payload (dict/list/str). Returns (sanitised_copy, {"NAME": n, "ADDRESS": m})."""
    clean = json.loads(json.dumps(payload))  # deep copy, JSON-safe
    leaves: List[Tuple[tuple, str]] = []
    _collect(clean, (), leaves)
    tokenizer = Tokenizer(json.dumps(payload))
    if not leaves:
        return clean, dict(tokenizer.found)
    all_spans = detect_batch([text for _, text in leaves])
    for (path, text), spans in zip(leaves, all_spans):
        if spans:
            clean = _set(clean, path, tokenizer.apply(text, spans))
    return clean, dict(tokenizer.found)
