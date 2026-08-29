"""
Aiva Nex Agent server (SIH prototype)

Receives ONLY a sanitized "screen graph" JSON from the browser extension -
never a screenshot, never raw PII. Before doing anything else, it re-checks
the payload text for PII-shaped substrings as a defense-in-depth backstop,
in case the client-side redaction ever missed something. If anything raw
slips through, the request is rejected outright.

The "decide what to do" step tries a real local LLM first (call_local_llm,
via any OpenAI-compatible local server - Ollama, LM Studio, Jan, ...) and
falls back to a small rule-based engine (decide_action_rules) if no local
model is reachable, its response can't be parsed, or it hallucinates a
target that doesn't exist on the page. This makes the demo unbreakable: with
a model running you get real model reasoning, without one you silently get
the same reliable rule-based behavior as before - either way /analyze always
returns a valid action.
"""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

app = FastAPI(title="Aiva Nex Agent Server", version="0.1.0")

# Wide open for local demo convenience. Chrome extensions with host_permissions
# already bypass CORS for their own requests, but this also lets you exercise
# the API directly from the /docs page or curl during the demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request model - deliberately loose (extra="allow", everything Optional).
# The extension's exact field set may evolve; the server should stay robust
# to that rather than 422-ing on minor shape drift during a live demo.
# ---------------------------------------------------------------------------
class ScreenGraph(BaseModel):
    model_config = ConfigDict(extra="allow")

    pageTitle: Optional[str] = None
    domain: Optional[str] = None
    scannedAt: Optional[str] = None
    forms: Optional[List[Dict[str, Any]]] = None
    inputs: Optional[List[Dict[str, Any]]] = None
    buttons: Optional[List[Dict[str, Any]]] = None
    links: Optional[List[Dict[str, Any]]] = None
    sensitiveItemsCount: Optional[int] = 0
    detectedTypes: Optional[Dict[str, int]] = None


# ---------------------------------------------------------------------------
# Defense-in-depth: re-scan the serialized payload for PII shapes. This is a
# backstop, not the primary defense (that's the extension's job) - it exists
# so a bug in client-side redaction fails loudly (HTTP 400) instead of
# silently leaking data server-side.
# ---------------------------------------------------------------------------
RAW_PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


def find_raw_pii(graph_dict: dict) -> List[str]:
    text = json.dumps(graph_dict)
    return [name for name, pattern in RAW_PII_PATTERNS.items() if pattern.search(text)]


# ---------------------------------------------------------------------------
# Local model call. Works against any server that speaks the OpenAI
# /v1/chat/completions wire format on localhost - Ollama, LM Studio, Jan, etc
# all do. No pip dependency needed for this: it's one small HTTP POST, done
# with the standard library.
#
# Defaults to Ollama (headless, no GUI needed) with a genuinely lightweight
# model. Override via env vars if you're pointing at something else:
#   LOCAL_LLM_BASE_URL   e.g. http://localhost:1234/v1   (LM Studio)
#   LOCAL_LLM_MODEL      e.g. qwen2.5:0.5b                (even smaller)
# ---------------------------------------------------------------------------
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "llama3.2:1b")
LOCAL_LLM_TIMEOUT_SECONDS = 20  # small CPU-only models can be slow on a cold first call

ALLOWED_ACTIONS = {"click", "focus", "scroll", "summarize"}

SYSTEM_PROMPT = (
    "You control a browser extension for filling in web forms. You receive a "
    "SANITIZED screen graph as JSON: all sensitive values are already replaced "
    "with tokens like EMAIL_1, PHONE_1, PASSWORD_FIELD, OTP_FIELD - these are "
    "placeholders, not real data, and must never be treated as real values. "
    "Decide exactly ONE next action for the browser to take and respond with "
    "ONLY a single JSON object, no other text, no markdown fences, matching "
    "one of these exact shapes:\n"
    '{"action":"focus","targetRef":"<ref from the graph>","reason":"<short reason>"}\n'
    '{"action":"click","targetRef":"<ref from the graph>","reason":"<short reason>"}\n'
    '{"action":"scroll","direction":"up"|"down"}\n'
    '{"action":"summarize","summary":"<one or two sentences>","reason":"<short reason>"}\n'
    "targetRef must be copied exactly from a \"ref\" field in the graph's "
    "inputs or buttons (e.g. \"input-3\", \"button-0\") - never invent one."
)


def _known_refs(graph: dict) -> set:
    refs = set()
    for f in graph.get("inputs") or []:
        if f.get("ref"):
            refs.add(f["ref"])
    for b in graph.get("buttons") or []:
        if b.get("ref"):
            refs.add(b["ref"])
    return refs


def _parse_model_action(raw_text: str, known_refs: set) -> Optional[dict]:
    text = raw_text.strip()
    # Small models often wrap JSON in ```json ... ``` even when told not to.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    text = text.strip()

    try:
        action = json.loads(text)
    except json.JSONDecodeError:
        # Last resort: grab the first {...} block in case the model added
        # commentary before/after the JSON despite instructions not to.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            action = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(action, dict) or action.get("action") not in ALLOWED_ACTIONS:
        return None

    # Never trust a target the model invented - a hallucinated ref would
    # make the extension silently do nothing (content.js just reports
    # "target not found"), which is confusing mid-demo; better to fall back
    # to the rule engine and get a valid action instead.
    if action["action"] in ("focus", "click"):
        if action.get("targetRef") not in known_refs:
            return None

    if action["action"] == "scroll" and action.get("direction") not in ("up", "down"):
        action["direction"] = "down"

    return action


def call_local_llm(graph: dict) -> Optional[dict]:
    body = json.dumps(
        {
            "model": LOCAL_LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(graph)},
            ],
            "temperature": 0,
            "max_tokens": 300,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{LOCAL_LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=LOCAL_LLM_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None  # no local model reachable, or it returned something unusable

    return _parse_model_action(content, _known_refs(graph))


# ---------------------------------------------------------------------------
# Rule-based fallback. Used whenever call_local_llm() returns None - no
# model reachable, bad/unparseable response, or a hallucinated target ref.
# Keep the signature the same: sanitized graph dict in, one action dict out.
# ---------------------------------------------------------------------------
def decide_action_rules(graph: dict) -> dict:
    inputs = graph.get("inputs") or []
    buttons = graph.get("buttons") or []

    # 1. An OTP field usually blocks everything else - handle it first.
    for f in inputs:
        if f.get("sanitizedValue") == "OTP_FIELD":
            return {
                "action": "focus",
                "targetRef": f.get("ref"),
                "reason": "An OTP field was detected and likely needs input next.",
            }

    # 2. Any required field still empty?
    for f in inputs:
        value = f.get("sanitizedValue")
        is_empty = value in (None, "", "null")
        if f.get("required") and is_empty:
            label = f.get("label") or f.get("ref")
            return {
                "action": "focus",
                "targetRef": f.get("ref"),
                "reason": f"Required field '{label}' looks empty.",
            }

    # 3. An upload/certificate action available? Common next step for
    #    scholarship/job application forms once the text fields are filled.
    for b in buttons:
        text = (b.get("text") or "").lower()
        if "upload" in text or "certificate" in text:
            return {
                "action": "click",
                "targetRef": b.get("ref"),
                "reason": f"Found an upload action: '{b.get('text')}'.",
            }

    # 4. Nothing urgent - summarize the page instead.
    total_inputs = len(inputs)
    sensitive = graph.get("sensitiveItemsCount", 0)
    summary = (
        f"This page ('{graph.get('pageTitle', 'untitled')}') has {total_inputs} form field(s), "
        f"{sensitive} of which were redacted locally before this request was sent. "
        f"{len(buttons)} button(s) and {len(graph.get('links') or [])} link(s) are available."
    )
    return {"action": "summarize", "summary": summary, "reason": "No urgent field or upload action found."}


def decide_action(graph: dict) -> dict:
    """Entry point used by /analyze: try the local model, fall back to rules."""
    model_action = call_local_llm(graph)
    if model_action is not None:
        return {**model_action, "decidedBy": f"local-llm:{LOCAL_LLM_MODEL}"}
    return {**decide_action_rules(graph), "decidedBy": "rule-engine"}


@app.get("/health")
def health():
    return {"status": "ok"}


def _local_llm_pingable() -> bool:
    """Lightweight reachability check (GET /models) - deliberately cheap, so
    the popup can poll this often without triggering a full model inference
    call each time."""
    try:
        req = urllib.request.Request(f"{LOCAL_LLM_BASE_URL.rstrip('/')}/models", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError):
        return False


@app.get("/health/llm")
def health_llm():
    """Lets the popup (or you, during setup) check whether a local model is
    actually reachable, without running the whole /analyze flow."""
    return {"reachable": _local_llm_pingable(), "baseUrl": LOCAL_LLM_BASE_URL, "model": LOCAL_LLM_MODEL}


@app.post("/analyze")
def analyze(graph: ScreenGraph):
    graph_dict = graph.model_dump()

    leaked = find_raw_pii(graph_dict)
    if leaked:
        raise HTTPException(
            status_code=400,
            detail=(
                "Rejected: possible raw PII detected in payload "
                f"({', '.join(leaked)}). Only sanitized data is accepted."
            ),
        )

    return decide_action(graph_dict)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
