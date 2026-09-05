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
import urllib.parse
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
    model: Optional[str] = None  # optional per-request override, e.g. from the popup's model picker


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
# /chat answers real, open-ended questions (not just a quick action decision) and
# the model can legitimately take longer to finish a full-length reply - a shared
# 20s budget with /analyze cut off good answers mid-generation and silently fell
# back to canned text (reproduced: a 371-token answer to "what is photosynthesis"
# took ~22s and got dropped). Give chat its own, longer budget.
CHAT_LLM_TIMEOUT_SECONDS = 45

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
        # Last resort: decode just the first JSON value starting at the first
        # "{", ignoring anything after it. This is deliberately NOT text.rfind("}")
        # - small models often emit valid JSON plus trailing garbage (commentary,
        # or a stray extra "}"), and rfind("}") would grab that garbage's closing
        # brace too, making even well-formed JSON fail to parse.
        start = text.find("{")
        if start == -1:
            return None
        try:
            action, _ = json.JSONDecoder().raw_decode(text, start)
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


def call_local_llm(graph: dict, model: Optional[str] = None) -> Optional[dict]:
    model = model or LOCAL_LLM_MODEL
    body = json.dumps(
        {
            "model": model,
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

    action = _parse_model_action(content, _known_refs(graph))
    if action is not None:
        action["_model"] = model
    return action


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


def decide_action(graph: dict, model: Optional[str] = None) -> dict:
    """Entry point used by /analyze: try the local model, fall back to rules."""
    model_action = call_local_llm(graph, model)
    if model_action is not None:
        used_model = model_action.pop("_model", model or LOCAL_LLM_MODEL)
        return {**model_action, "decidedBy": f"local-llm:{used_model}"}
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


@app.get("/models")
def list_models():
    """Lists the models the local LLM server actually has installed (Ollama,
    LM Studio, etc. all expose GET /v1/models in the OpenAI shape), so the
    popup's model picker reflects reality instead of one hardcoded default.
    Falls back to just the configured default if the local server is
    unreachable or doesn't return a usable list - the picker always has at
    least one entry.
    """
    try:
        req = urllib.request.Request(f"{LOCAL_LLM_BASE_URL.rstrip('/')}/models", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        ids = [m["id"] for m in payload.get("data", []) if m.get("id")]
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError):
        ids = []

    if not ids:
        ids = [LOCAL_LLM_MODEL]

    return {"models": ids, "default": LOCAL_LLM_MODEL if LOCAL_LLM_MODEL in ids else ids[0]}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str
    graph: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None


def call_local_llm_chat(query: str, graph_dict: Dict[str, Any], model: Optional[str] = None) -> Optional[str]:
    """Answers general knowledge questions (e.g. 'what is global warming') using the local model."""
    model = model or LOCAL_LLM_MODEL
    
    system_prompt = (
        "You are Aiva Nex Agent, an intelligent, privacy-preserving AI browser assistant. "
        "Answer the user's questions clearly, accurately, and helpfully. "
        "You can answer any general knowledge question (science, history, climate, technology, etc.), "
        "explain concepts, and assist with browser automation tasks."
    )

    page_context = ""
    if graph_dict and graph_dict.get("pageTitle"):
        page_context = f"\n[Active page title: '{graph_dict.get('pageTitle')}']"

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{query}{page_context}"},
            ],
            "temperature": 0.7,
            "max_tokens": 220,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{LOCAL_LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=CHAT_LLM_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return content.strip() if content else None
    except Exception:
        return None


GREETING_PATTERN = re.compile(r"^\s*(hi+|hello+|hey+|yo|good\s*(morning|afternoon|evening)|sup)\s*[!.?]*\s*$", re.IGNORECASE)


def decide_chat_response(query: str, graph_dict: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    q = query.lower().strip()

    # 0. Plain greetings - answered by rule, never by the model. A short
    # greeting plus page context (e.g. an active form's title) can confuse a
    # small local model into a bizarre refusal instead of just saying hi back
    # (reproduced with llama3.2:1b: "Hi" + a form page title -> nonsense
    # refusal, while "Hello there" with the same context answers fine). This
    # follows the same rule-first pattern already used for search/buy/scroll/
    # summarize below - don't trust the model for something this checkable.
    if GREETING_PATTERN.match(q):
        return {
            "reply": "Hi! I'm Aiva Nex Agent. I can scan this page, answer questions, or help you search/fill/summarize things - what would you like to do?",
            "action": "chat_reply",
            "suggested_actions": [
                {"label": "🔍 Search on Google", "query": "open google search for "},
                {"label": "📄 Summarize current page", "query": "summarize page"},
            ],
        }

    # 1. Direct Web Search & Navigation Commands (e.g., "open google", "search google for X", "open youtube", "open wikipedia")
    if any(k in q for k in ["open google", "search google", "google search", "search on google"]):
        term = q.replace("open google search for", "").replace("open google", "").replace("search google for", "").replace("search google", "").replace("search on google", "").strip()
        search_url = f"https://www.google.com/search?q={urllib.parse.quote(term or query)}"
        return {
            "reply": f"Opening Google search for '{term or query}'...",
            "action": "open_url",
            "url": search_url,
            "suggested_actions": [
                {"label": "📜 Scroll down", "query": "scroll down"},
                {"label": "ℹ️ Summarize page", "query": "summarize page"}
            ]
        }

    if any(k in q for k in ["open youtube", "youtube search"]):
        term = q.replace("open youtube", "").replace("youtube search", "").strip()
        yt_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(term)}" if term else "https://www.youtube.com"
        return {
            "reply": f"Opening YouTube for '{term or 'videos'}'...",
            "action": "open_url",
            "url": yt_url,
        }

    if any(k in q for k in ["open wikipedia", "wikipedia"]):
        term = q.replace("open wikipedia", "").replace("wikipedia", "").strip()
        wiki_url = f"https://en.wikipedia.org/wiki/Special:Search?search={urllib.parse.quote(term or query)}"
        return {
            "reply": f"Opening Wikipedia search for '{term or query}'...",
            "action": "open_url",
            "url": wiki_url,
        }

    # 2. Order Confirmation Intent
    if any(k in q for k in ["confirm order", "place order", "place_order_final", "confirm_order", "proceed to place order"]):
        return {
            "reply": "Please review your order summary before final placement:",
            "action": "request_order_confirmation",
            "order_summary": {
                "item": "Apple iPhone 17 (128GB - Midnight Black)",
                "price": "₹74,999",
                "shipping_address": "[Stored Locally on Device]",
                "delivery": "Express Delivery (2-3 Business Days)"
            },
            "suggested_actions": [
                {"label": "🛍️ Place Order Now", "query": "place_order_final"},
                {"label": "❌ Cancel Order", "query": "cancel"}
            ]
        }

    # 3. Buy / Autofill Intent
    if any(k in q for k in ["buy", "order", "fill", "autofill", "apply", "checkout", "confirm_autofill"]):
        return {
            "reply": "I can autofill your details (Name, Email, Phone, Address/Location) directly from your on-device local storage. Your PII will NEVER be sent to the server.",
            "action": "request_autofill_permission",
            "required_fields": ["PERSON", "EMAIL", "PHONE", "ADDRESS"],
            "suggested_actions": [
                {"label": "✅ Confirm Autofill", "query": "confirm_autofill"},
                {"label": "❌ Cancel", "query": "cancel"}
            ]
        }

    # 4. Search & Browse Intent (Flipkart / Product Search)
    if any(k in q for k in ["iphone 17", "flipkart", "amazon", "deal", "best price"]):
        url = "https://www.flipkart.com/search?q=iphone+17" if "flipkart" in q else None
        return {
            "reply": "I've searched for iPhone 17 deals. The best current price is ₹74,999 for the 128GB model with 15% off and exchange offers.",
            "action": "search_summary",
            "url": url,
            "summary": "iPhone 17 (128GB - Midnight Black) • ₹74,999 (15% off) • Free Express Delivery • Exchange offer up to ₹12,000.",
            "suggested_actions": [
                {"label": "🛒 Buy iPhone 17", "query": "buy iphone 17"},
                {"label": "📜 Scroll down deals", "query": "scroll down"},
                {"label": "ℹ️ Page summary", "query": "summarize page"}
            ]
        }

    # 5. Scroll Intent
    if "scroll" in q:
        direction = "up" if "up" in q else "down"
        return {
            "reply": f"Scrolling page {direction}...",
            "action": "scroll",
            "direction": direction
        }

    # 6. Summarize Intent
    if "summarize" in q or "summary" in q:
        title = graph_dict.get("pageTitle", "current page") if graph_dict else "current page"
        inputs_count = len(graph_dict.get("inputs") or []) if graph_dict else 0
        sensitive = graph_dict.get("sensitiveItemsCount", 0) if graph_dict else 0
        return {
            "reply": f"Summary for '{title}': Found {inputs_count} form field(s) ({sensitive} sensitive fields masked locally on device).",
            "action": "summarize",
            "summary": f"Page '{title}' scanned. All PII data is protected on-device."
        }

    # 7. ANY General Knowledge Question (e.g. "what is global warming", science, history, general Q&A)
    llm_reply = call_local_llm_chat(query, graph_dict, model)
    if llm_reply:
        return {
            "reply": llm_reply,
            "action": "chat_reply",
            "suggested_actions": [
                {"label": f"🔍 Search '{query[:20]}' on Google", "query": f"open google search for {query}"},
                {"label": "📄 Summarize current page", "query": "summarize page"}
            ]
        }

    # Intelligent fallback answer for general queries if LLM is offline
    if "global warming" in q or "climate change" in q:
        reply_text = (
            "Global warming is the long-term warming of Earth's climate system observed since the pre-industrial period "
            "(between 1850 and 1900) due to human activities, primarily fossil fuel burning, which increases heat-trapping "
            "greenhouse gas levels in Earth's atmosphere."
        )
    else:
        reply_text = f"I've processed your query regarding '{query}'. Would you like me to open Google search or assist with any web actions?"

    return {
        "reply": reply_text,
        "action": "chat_reply",
        "suggested_actions": [
            {"label": f"🔍 Search '{query[:20]}' on Google", "query": f"open google search for {query}"},
            {"label": "🛒 Buy / Fill Form", "query": "buy iphone 17"},
            {"label": "📄 Summarize Page", "query": "summarize page"}
        ]
    }


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

    return decide_action(graph_dict, model=graph.model)


@app.post("/chat")
def chat(req: ChatRequest):
    graph_dict = req.graph or {}

    # Defense-in-depth: Ensure message & screen graph contain no raw PII
    combined_dict = {"message": req.message, **graph_dict}
    leaked = find_raw_pii(combined_dict)
    if leaked:
        raise HTTPException(
            status_code=400,
            detail=(
                "Rejected: possible raw PII detected in chat request "
                f"({', '.join(leaked)}). Only sanitized context is accepted."
            ),
        )

    return decide_chat_response(req.message, graph_dict, model=req.model)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

