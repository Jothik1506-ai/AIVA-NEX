"""
FastAPI glue: the policy hook used by /analyze and /chat, and POST /ner/scan.

Policy (env AIVA_NER_POLICY):
  tokenize (default) - free-text NAME/ADDRESS spans that slipped past the
                       extension are replaced with NAME_n / ADDRESS_n before
                       the payload reaches the LLM (local or cloud fallback).
  reject             - same detection, but the request is refused with HTTP
                       400, exactly like the structured-PII regex re-check.
  off                - NER disabled.

Why tokenize is the default: the regex re-check targets exact-shape PII
(email, Aadhaar, ...) where a hit is almost certainly a leak, so rejecting is
safe. NER is probabilistic and page text legitimately contains names (a news
article, an author byline); rejecting every such page would make the agent
unusable, while tokenising keeps the privacy guarantee AND the task working.

Raw text is never logged - only counts and the engine name.
"""

import logging
import os
import time
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .detector import detect_batch, detect_spans
from .model import engine_name
from .tokenizer import Tokenizer, sanitize_payload

log = logging.getLogger("aiva.ner")

router = APIRouter(prefix="/ner", tags=["ner"])


def ner_policy() -> str:
    policy = os.getenv("AIVA_NER_POLICY", "tokenize").strip().lower()
    return policy if policy in ("tokenize", "reject", "off") else "tokenize"


def enforce_ner_policy(payload: Any, what: str = "payload") -> Any:
    """Apply the NER policy to a JSON-like payload; returns the (possibly
    tokenised) payload or raises HTTPException(400) in reject mode."""
    policy = ner_policy()
    if policy == "off":
        return payload
    clean, found = sanitize_payload(payload)
    kinds = [k.lower() for k, n in found.items() if n]
    if kinds:
        log.info("NER %s: %s in %s", policy, found, what)  # counts only, never text
        if policy == "reject":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Rejected: possible raw PII detected in {what} ({', '.join(kinds)}). "
                    "Only sanitized data is accepted."
                ),
            )
    return clean


def sanitize_texts(texts: List[str], what: str = "OCR text") -> Tuple[List[str], Dict[str, int]]:
    """NAME/ADDRESS protection for text the SERVER produced (e.g. OCR output
    from /perceive), where rejecting the request buys nothing - the pixels
    already arrived. Returns (clean_texts, {"NAME": n, "ADDRESS": m}).

      tokenize (default) - consistent NAME_n / ADDRESS_n tokens
      reject             - irreversible [NAME] / [ADDRESS] masks (strict mode)
      off                - unchanged
    """
    policy = ner_policy()
    texts = list(texts or [])
    if policy == "off" or not texts:
        return texts, {}
    tokenizer = Tokenizer(" ".join(texts))
    counts: Dict[str, int] = {}
    out: List[str] = []
    for text, spans in zip(texts, detect_batch(texts)):
        for sp in spans:
            counts[sp.type] = counts.get(sp.type, 0) + 1
        if not spans:
            out.append(text)
        elif policy == "reject":
            buf, cursor = [], 0
            for sp in sorted(spans, key=lambda s: s.start):
                if sp.start < cursor:
                    continue
                buf.append(text[cursor:sp.start] + f"[{sp.type}]")
                cursor = sp.end
            out.append("".join(buf) + text[cursor:])
        else:
            out.append(tokenizer.apply(text, spans))
    if counts:
        log.info("NER %s: %s in %s", policy, counts, what)  # counts only, never text
    return out, counts


class ScanRequest(BaseModel):
    text: str = Field(..., max_length=20000)


class ScanSpan(BaseModel):
    start: int
    end: int
    type: str
    score: float
    source: str


class ScanResponse(BaseModel):
    spans: List[ScanSpan]
    tokenized: str
    engine: str
    elapsedMs: float


@router.post("/scan", response_model=ScanResponse)
def ner_scan(req: ScanRequest) -> ScanResponse:
    """Dev/debug: return NAME/ADDRESS spans (offsets only) and the tokenised text.
    The raw input is never echoed back or logged."""
    t0 = time.perf_counter()
    spans = detect_spans(req.text)
    tokenized = Tokenizer(req.text).apply(req.text, spans)
    elapsed = (time.perf_counter() - t0) * 1000
    return ScanResponse(
        spans=[ScanSpan(**sp.to_dict()) for sp in spans],
        tokenized=tokenized,
        engine=engine_name(),
        elapsedMs=round(elapsed, 2),
    )
