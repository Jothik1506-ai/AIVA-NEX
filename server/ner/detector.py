"""
Combines the spaCy model with the Indian rule layer into NAME / ADDRESS spans.

    model PERSON         -> NAME candidates (filtered: no digits, no locality
                            words like "Nagar", no UI words)  score 0.70
    rule names           -> honorific / relation / cue / surname  0.80-0.95
    model GPE/LOC/FAC    -> NOT spans on their own (a city is not a personal
                            address) - only extra evidence for the address scorer
    rule addresses       -> scored comma-separated runs (see rules.py)

Overlapping NAME candidates are merged (agreement bumps the score); a NAME that
sits inside an ADDRESS is absorbed by it ("Gandhi Nagar" is a place).
"""

import re
from typing import List, Optional, Sequence

from . import model as _model
from .rules import (
    HOUSE_RE,
    INDIAN_STATES,
    MAJOR_CITIES,
    SUFFIX_RE,
    TOKEN_LIKE_RE,
    _strip_common,
    find_address_spans,
    find_name_spans,
)
from .types import Span

PLACE_LABELS = {"GPE", "LOC", "FAC"}
# Place words the small English model sometimes tags as PERSON ("Sri Lanka" -> "Lanka").
NOT_NAMES = {"Lanka", *MAJOR_CITIES, *INDIAN_STATES}


def _model_names(text: str, ents) -> List[Span]:
    out = []
    for s, e, label in ents:
        if label != "PERSON":
            continue
        trimmed = _strip_common(text, s, e)
        if not trimmed:
            continue
        s, e = trimmed
        frag = text[s:e]
        if (len(frag) < 2 or not frag[0].isupper() or re.search(r"\d", frag) or frag in NOT_NAMES
                or TOKEN_LIKE_RE.search(frag) or HOUSE_RE.search(frag)
                or any(SUFFIX_RE.fullmatch(w.rstrip(".,")) for w in frag.split())):
            continue
        out.append(Span(s, e, "NAME", 0.70, "model"))
    return out


def _merge_same_type(spans: List[Span]) -> List[Span]:
    spans = sorted(spans, key=lambda sp: (sp.start, -sp.end))
    merged: List[Span] = []
    for sp in spans:
        if merged and sp.start < merged[-1].end and sp.type == merged[-1].type:
            prev = merged[-1]
            agree = prev.source != sp.source
            score = min(0.99, max(prev.score, sp.score) + (0.05 if agree else 0.0))
            source = prev.source if not agree else "+".join(sorted({*prev.source.split("+"), sp.source}))
            merged[-1] = Span(prev.start, max(prev.end, sp.end), sp.type, score, source)
        else:
            merged.append(sp)
    return merged


def _resolve(text: str, names: List[Span], addresses: List[Span]) -> List[Span]:
    keep_names = []
    addrs = list(addresses)
    for n in names:
        drop = False
        for k, a in enumerate(addrs):
            if not (n.start < a.end and n.end > a.start):
                continue
            if a.start <= n.start and n.end <= a.end:
                drop = True  # name inside an address -> address token covers it
            elif n.start <= a.start and n.end < a.end:
                # "Smt. X, Flat 4B ..." style partial overlap -> clip the address
                new_start = n.end
                while new_start < a.end and text[new_start] in " ,;:-":
                    new_start += 1
                addrs[k] = Span(new_start, a.end, a.type, a.score, a.source)
            else:
                addrs[k] = None  # address inside a name - unlikely, prefer the name
        addrs = [a for a in addrs if a is not None]
        if not drop:
            keep_names.append(n)
    return sorted(keep_names + addrs, key=lambda sp: sp.start)


def detect_spans(text: str, ents: Optional[list] = None) -> List[Span]:
    """NAME/ADDRESS spans for one text. `ents` = precomputed model entities."""
    if not text or not text.strip():
        return []
    if ents is None:
        ents = _model_entities_for([text])[0]
    names = _merge_same_type(find_name_spans(text) + _model_names(text, ents))
    hints = [(s, e) for s, e, label in ents if label in PLACE_LABELS]
    addresses = find_address_spans(text, hints, names)
    return _resolve(text, names, addresses)


def _model_entities_for(texts: Sequence[str]):
    # Names and addresses are capitalised; skip the model for texts with no
    # uppercase letter at all (most UI strings) - the rules still run.
    idx = [i for i, t in enumerate(texts) if t and any(c.isupper() for c in t)]
    results = [[] for _ in texts]
    if idx:
        for i, ents in zip(idx, _model.model_entities([texts[i] for i in idx])):
            results[i] = ents
    return results


def detect_batch(texts: Sequence[str]) -> List[List[Span]]:
    all_ents = _model_entities_for(texts)
    return [detect_spans(t, ents) for t, ents in zip(texts, all_ents)]


def detect_names_addresses(text: str) -> List[dict]:
    """Public API: [{start, end, type: NAME|ADDRESS, score}] for `text`."""
    return [{"start": s.start, "end": s.end, "type": s.type, "score": round(s.score, 3)}
            for s in detect_spans(text)]
