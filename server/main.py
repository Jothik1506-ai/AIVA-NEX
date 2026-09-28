"""
Aiva Nex Agent server (SIH prototype)

Receives ONLY a sanitized "screen graph" JSON from the browser extension -
never a screenshot, never raw PII. Before doing anything else, it re-checks
the payload text for PII-shaped substrings as a defense-in-depth backstop.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

# --- [privacy-hardening] begin -------------------------------------------
from pii_checks import CANDIDATE_PATTERNS, find_validated_pii_types
from security import install_security

log = logging.getLogger("aiva")

app = FastAPI(title="Aiva Nex Agent Server", version="0.1.0")
# CORS locked to the extension origin + localhost, X-Aiva-Token required on
# everything but /health, 256 KB body cap. See server/security.py.
SHARED_TOKEN = install_security(app)

# Field/list size limits. The body cap (security.py) bounds everything else.
ShortStr = Annotated[str, Field(max_length=500)]
SnippetStr = Annotated[str, Field(max_length=1000)]
MAX_ITEMS = 1000

# --- feature/ner: on-device NAME/ADDRESS detection (see server/ner/) --------
from ner import enforce_ner_policy, router as ner_router, sanitize_texts, warm_up_async  # noqa: E402

app.include_router(ner_router)  # POST /ner/scan (dev/debug)
warm_up_async()  # load the spaCy model once, in the background
# --- end feature/ner --------------------------------------------------------


class ScreenGraph(BaseModel):
    model_config = ConfigDict(extra="allow")

    pageTitle: Optional[str] = Field(default=None, max_length=500)
    domain: Optional[str] = Field(default=None, max_length=253)
    scannedAt: Optional[str] = Field(default=None, max_length=64)
    headings: Optional[List[ShortStr]] = Field(default=None, max_length=100)
    textSnippets: Optional[List[SnippetStr]] = Field(default=None, max_length=200)
    forms: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=MAX_ITEMS)
    inputs: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=MAX_ITEMS)
    buttons: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=MAX_ITEMS)
    links: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=MAX_ITEMS)
    sensitiveItemsCount: Optional[int] = Field(default=0, ge=0, le=100000)
    detectedTypes: Optional[Dict[str, int]] = None
    model: Optional[str] = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str = Field(min_length=1, max_length=4000)
    graph: Optional[Dict[str, Any]] = None
    model: Optional[str] = Field(default=None, max_length=200)
    history: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=50)


def find_raw_pii(graph_dict: dict) -> List[str]:
    """Types of VALIDATED raw PII left in the payload (-> HTTP 400).

    Unvalidated look-alikes (a 12-digit number failing Verhoeff, a 16-digit
    reference failing Luhn) are not rejected; see server/pii_checks.py.
    """
    return find_validated_pii_types(graph_dict)


# --- Visual perception (SIH26171): POST /perceive, see server/vision/ ---
try:
    from vision import attach_visual_target, build_router, visual_refs  # noqa: E402

    # Mask every PII-shaped candidate in OCR text (same patterns as the
    # extension, validated or not), then NAME/ADDRESS via the NER policy.
    app.include_router(build_router(
        pii_patterns={name.lower(): pattern for name, pattern in CANDIDATE_PATTERNS},
        text_sanitizer=sanitize_texts,
    ))
except ImportError as _vision_err:  # vision deps not installed: DOM-only mode still works
    print(f"[vision] disabled ({_vision_err}); pip install -r requirements.txt to enable /perceive")

    def attach_visual_target(action, graph):  # type: ignore[no-redef]
        return action

    def visual_refs(graph):  # type: ignore[no-redef]
        return set()
# --- end visual perception ---


LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "llama3.2:1b")
LOCAL_LLM_TIMEOUT_SECONDS = 20
CHAT_LLM_TIMEOUT_SECONDS = 15

# Off-device calls are OFF unless AIVA_ALLOW_CLOUD=1. That flag gates both the
# Gemini fallback and a LOCAL_LLM_BASE_URL pointing at a non-loopback host.
ALLOW_CLOUD = os.getenv("AIVA_ALLOW_CLOUD", "0").strip().lower() in ("1", "true", "yes")


def _is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _local_llm_allowed() -> bool:
    if _is_loopback_url(LOCAL_LLM_BASE_URL) or ALLOW_CLOUD:
        return True
    log.warning("LOCAL_LLM_BASE_URL is not a loopback address; refusing to send page context there without AIVA_ALLOW_CLOUD=1.")
    return False


# Optional Gemini cloud fallback - needs BOTH AIVA_ALLOW_CLOUD=1 and
# GEMINI_API_KEY. Sends sanitized page context to Google when used.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

if ALLOW_CLOUD:
    log.warning(
        "!!! AIVA_ALLOW_CLOUD=1: sanitized page context MAY be sent off-device "
        "(Gemini %s%s). The 'fully local' guarantee does NOT hold in this mode. !!!",
        GEMINI_MODEL,
        "" if GEMINI_API_KEY else ", but GEMINI_API_KEY is not set so it stays unused",
    )
elif GEMINI_API_KEY:
    log.warning("GEMINI_API_KEY is set but ignored: cloud fallback is off (set AIVA_ALLOW_CLOUD=1 to opt in).")
# --- [privacy-hardening] end ---------------------------------------------

ALLOWED_ACTIONS = {"click", "focus", "scroll", "summarize"}

SYSTEM_PROMPT = (
    "You control a browser extension for filling in web forms. You receive a "
    "SANITIZED screen graph as JSON. Decide exactly ONE next action for the browser to take and respond with "
    "ONLY a single JSON object matching one of these exact shapes:\n"
    '{"action":"focus","targetRef":"<ref>","reason":"<reason>"}\n'
    '{"action":"click","targetRef":"<ref>","reason":"<reason>"}\n'
    '{"action":"scroll","direction":"up"|"down"}\n'
    '{"action":"summarize","summary":"<summary>","reason":"<reason>"}\n'
)


def _known_refs(graph: dict) -> set:
    refs = set(visual_refs(graph))  # visual ids ("v3") from /perceive (SIH26171)
    for f in graph.get("inputs") or []:
        if f.get("ref"):
            refs.add(f["ref"])
    for b in graph.get("buttons") or []:
        if b.get("ref"):
            refs.add(b["ref"])
    return refs


def _parse_model_action(raw_text: str, known_refs: set) -> Optional[dict]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    text = text.strip()

    try:
        action = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            action = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(action, dict) or action.get("action") not in ALLOWED_ACTIONS:
        return None

    if action["action"] in ("focus", "click"):
        if action.get("targetRef") not in known_refs:
            return None

    if action["action"] == "scroll" and action.get("direction") not in ("up", "down"):
        action["direction"] = "down"

    return action


def call_local_llm(graph: dict, model: Optional[str] = None) -> Optional[dict]:
    if not _local_llm_allowed():  # [privacy-hardening]
        return None
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
    except Exception:
        return None

    action = _parse_model_action(content, _known_refs(graph))
    if action is not None:
        action["_model"] = model
    return action


def _build_page_context(graph_dict: Dict[str, Any]) -> str:
    """Build a text description of the current page from the screen graph.

    SECURITY INVARIANT (docs/MEMORY-ARCHITECTURE-PLAN.md §9.3): this function
    is an explicit allow-list of named ScreenGraph fields - NOT a blind
    json.dumps(graph_dict). Keep it that way. The extension's per-fact memory
    store (AivaMemory, extension/memory.js) lives only in the browser's
    chrome.storage.local; this server has no access to it and today's code
    has no path that threads a memory fact into `graph`, `message`, or
    `history` in the first place. If a future feature (e.g. V2 smart-compose
    using saved preferences) ever needs the LLM to see a memory value, it
    must be passed as an explicit, separately-named field the caller
    controls - never by writing memory content into the existing `graph`
    object, which both call_local_llm_chat() and call_gemini_chat() read via
    this same function. A field this function doesn't explicitly list here
    can never reach either LLM.
    """
    parts = []
    if graph_dict.get("pageTitle"):
        parts.append(f"Page Title: {graph_dict['pageTitle']}")
    if graph_dict.get("domain"):
        parts.append(f"Domain: {graph_dict['domain']}")
    if graph_dict.get("headings"):
        parts.append("Page Headings:\n" + "\n".join(f"- {h}" for h in graph_dict["headings"]))
    if graph_dict.get("textSnippets"):
        parts.append("Page Content Snippets:\n" + "\n".join(f"- {s}" for s in graph_dict["textSnippets"]))
    return "\n\n".join(parts)


def call_gemini_chat(query: str, graph_dict: Dict[str, Any]) -> Optional[str]:
    """Optional Gemini cloud fallback - OFF by default.

    Runs only when AIVA_ALLOW_CLOUD=1 AND GEMINI_API_KEY are both set
    ([privacy-hardening]). This is the one call in this file that leaves the
    machine - a real external network call to Google. Per plan
    §9.3, memory-derived context must never reach this function. It doesn't
    today only because nothing upstream threads memory into `query` or
    `graph_dict` - see the invariant documented on _build_page_context()
    above, which is what actually enforces this, not a check in this
    function itself. Do not "simplify" _build_page_context() into a raw
    dump of graph_dict without re-reading that comment.
    """
    if not (ALLOW_CLOUD and GEMINI_API_KEY):  # [privacy-hardening] opt-in only
        return None
    log.warning("Cloud fallback in use: sending sanitized page context to Google Gemini (%s).", GEMINI_MODEL)

    system_prompt = (
        "You are Aiva Nex Agent, an intelligent, privacy-preserving AI browser assistant. "
        "Answer the user's questions clearly and helpfully. "
        "When summarizing a page, use the provided page context. "
        "Keep responses concise (under 150 words)."
    )

    page_context = _build_page_context(graph_dict)
    full_prompt = f"{system_prompt}\n\nUser Request: {query}"
    if page_context:
        full_prompt += f"\n\nCurrent Page Context:\n{page_context}"

    body = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 300}
    }).encode("utf-8")

    url = GEMINI_API_URL.format(model=GEMINI_MODEL)
    # Key in a header, not the URL, so it never lands in proxy/access logs.
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


def call_local_llm_chat(query: str, graph_dict: Dict[str, Any], model: Optional[str] = None) -> Optional[str]:
    """Answers user queries using the local LLM; Gemini fallback only if opted in."""
    if not _local_llm_allowed():  # [privacy-hardening]
        return call_gemini_chat(query, graph_dict)
    model = model or LOCAL_LLM_MODEL

    system_prompt = (
        "You are Aiva Nex Agent, an intelligent, privacy-preserving AI browser assistant. "
        "Answer the user's questions clearly, accurately, and helpfully based on the webpage context provided. "
        "When asked to summarize a page, read the headings and text snippets from the page and write a clear 2-3 sentence summary. "
        "Keep responses concise (under 150 words)."
    )

    page_context = _build_page_context(graph_dict)
    full_prompt = f"User Request: {query}\n\nWebpage Context:\n{page_context}" if page_context else query

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_prompt},
            ],
            "temperature": 0.7,
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
        with urllib.request.urlopen(req, timeout=CHAT_LLM_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        if content and content.strip():
            return content.strip()
    except Exception:
        pass

    # Gemini fallback - returns None unless AIVA_ALLOW_CLOUD=1 (default off)
    return call_gemini_chat(query, graph_dict)


GREETING_PATTERN = re.compile(r"^\s*(hi+|hello+|hey+|yo|good\s*(morning|afternoon|evening)|sup)\s*[!.?]*\s*$", re.IGNORECASE)


def _any_word_in(q: str, words: List[str]) -> bool:
    return any(re.search(r"(?<![a-zA-Z])" + re.escape(w) + r"(?![a-zA-Z])", q) for w in words)


# --- rule-engine fallback for /analyze -------------------------------------
# Graph shape (extension/content.js collectFields/collectButtons): each input is
# {ref, type, label, required, isSensitive, sanitizedValue, position}; each
# button is {ref, text, position}. Sensitive values arrive as bare tokens
# (EMAIL_1, PASSWORD_FIELD, OTP_FIELD_2 ...), so a tokenised value means "value
# hidden", not necessarily "filled". Legacy callers sent `sensitive` /
# `sensitiveType`; `sensitiveType` is still honoured, and `categories` /
# `hasValue` are accepted if a caller includes them.

_BARE_TOKEN_RE = re.compile(r"^[A-Z]+(?:_[A-Z]+)*(?:_\d+)?$")
_CREDENTIAL_TOKEN_RE = re.compile(r"^(?:PASSWORD|OTP)_FIELD(?:_\d+)?$")
_CREDENTIAL_LABEL_RE = re.compile(
    r"\b(?:password|passcode|passphrase|otp|one[\s-]?time\s+(?:password|code)|m?pin|cvv|cvc|security\s+code)\b",
    re.IGNORECASE,
)
_NON_FOCUSABLE_TYPES = {"hidden", "submit", "button", "reset", "image", "checkbox", "radio", "file"}

# Buttons that finish or advance the task on the page vs. ones that leave,
# undo, or do something secondary. Secondary patterns are checked first.
_SECONDARY_BUTTON_RE = re.compile(
    r"\b(?:cancel|back|previous|prev|download|print|refresh|reload|edit|track|forgot|reset|clear|close|"
    r"dismiss|skip|delete|remove|filter|sort|share|log\s*out|sign\s*out|logout|draft|help)\b"
    r"|\bcontinue\s+(?:shopping|browsing)\b",
    re.IGNORECASE,
)
_PRIMARY_BUTTON_RE = re.compile(
    r"\b(?:submit|continue|next|proceed|apply|pay|confirm|verify|register|sign\s*up|signup|sign\s*in|signin|"
    r"log\s*in|login|place\s+order|checkout|check\s*out|buy|add|save|send|search|find|go|done|finish|use|book|"
    r"start|ok|okay|accept|agree|update|upload|request|create|join|subscribe)\b",
    re.IGNORECASE,
)
# Page text saying more content follows below the fold.
_MORE_CONTENT_RE = re.compile(
    r"\bscroll\b|\b(?:load|show|see|view|read)\s+more\b|\b(?:continue|keep)\s+reading\b"
    r"|\bshowing\s+[\d,]+\s*(?:-|–|to)\s*[\d,]+\s+of\s+[\d,]+",
    re.IGNORECASE,
)


def _input_categories(inp: dict) -> set:
    cats = inp.get("categories") or {}
    if isinstance(cats, (dict, list, tuple, set)):
        return {str(k).upper() for k in cats}
    return set()


def _is_credential_field(inp: dict) -> bool:
    """Password / OTP / PIN / CVV style field the user has to type into."""
    if str(inp.get("type") or "").lower() == "password":
        return True
    if str(inp.get("sensitiveType") or "").upper() in ("PASSWORD", "OTP"):  # legacy field name
        return True
    if _input_categories(inp) & {"PASSWORD_FIELD", "OTP_FIELD", "PASSWORD", "OTP"}:
        return True
    if _CREDENTIAL_TOKEN_RE.match(str(inp.get("sanitizedValue") or "")):
        return True
    return bool(_CREDENTIAL_LABEL_RE.search(str(inp.get("label") or "")))


def _value_state(inp: dict) -> str:
    """'empty', 'filled' or 'unknown' (a bare token hides whether a value exists)."""
    for key in ("hasValue", "filled"):
        if isinstance(inp.get(key), bool):
            return "filled" if inp[key] else "empty"
    if isinstance(inp.get("isEmpty"), bool):
        return "empty" if inp["isEmpty"] else "filled"
    val = inp.get("sanitizedValue")
    if val is None:
        val = inp.get("value")
    val = "" if val is None else str(val).strip()
    if not val or val.lower() == "unchecked":
        return "empty"
    if _BARE_TOKEN_RE.match(val):
        return "unknown"
    return "filled"


def _is_focusable_input(inp: dict) -> bool:
    if not inp.get("ref"):
        return False
    if str(inp.get("type") or "").lower() in _NON_FOCUSABLE_TYPES:
        return False
    return not str(inp.get("sanitizedValue") or "").startswith("HIDDEN_FIELD")


def _primary_button(buttons: list) -> Optional[dict]:
    for btn in buttons:
        txt = re.sub(r"\s+", " ", str(btn.get("text") or "")).strip()
        if not txt or not btn.get("ref") or _SECONDARY_BUTTON_RE.search(txt):
            continue
        if _PRIMARY_BUTTON_RE.search(txt):
            return btn
    return None


def _has_more_content(graph: dict) -> bool:
    texts = list(graph.get("headings") or []) + list(graph.get("textSnippets") or [])
    if any(_MORE_CONTENT_RE.search(str(t)) for t in texts):
        return True
    # Optional geometry, used only if a caller sends it: document taller than the viewport.
    vp = graph.get("viewport")
    if not isinstance(vp, dict):
        return False
    try:
        vh = float(vp.get("height") or 0)
        doc_h = float(vp.get("scrollHeight") or graph.get("scrollHeight") or 0)
        scroll_y = float(vp.get("scrollY") or 0)
    except (TypeError, ValueError):
        return False
    return vh > 0 and doc_h > scroll_y + vh * 1.1


def _field_name(inp: dict) -> str:
    return inp.get("label") or inp.get("ref")


def decide_action_rules(graph: dict) -> dict:
    inputs = [i for i in (graph.get("inputs") or []) if isinstance(i, dict)]
    buttons = [b for b in (graph.get("buttons") or []) if isinstance(b, dict)]
    fields = [i for i in inputs if _is_focusable_input(i)]

    # 1. Never submit while a required field is still empty.
    for inp in fields:
        if inp.get("required") and _value_state(inp) == "empty":
            return {
                "action": "focus",
                "targetRef": inp["ref"],
                "reason": f"Required field '{_field_name(inp)}' is still empty.",
            }

    # 2. Password / OTP / PIN / CVV fields that are empty, or whose value is
    #    hidden behind a token, most likely need the user's input next.
    for inp in fields:
        if _is_credential_field(inp) and _value_state(inp) in ("empty", "unknown"):
            return {
                "action": "focus",
                "targetRef": inp["ref"],
                "reason": f"The '{_field_name(inp)}' field (password/OTP/PIN) likely needs input next.",
            }

    # 3. Primary submit-like button.
    btn = _primary_button(buttons)
    if btn is not None:
        return {
            "action": "click",
            "targetRef": btn["ref"],
            "reason": f"A primary action button ('{btn.get('text')}') is ready to be clicked.",
        }

    # 4. No button to press but an empty field to fill (e.g. a lone search box).
    for inp in fields:
        if _value_state(inp) == "empty" and str(inp.get("type") or "").lower() != "select":
            return {
                "action": "focus",
                "targetRef": inp["ref"],
                "reason": f"Field '{_field_name(inp)}' is empty and there is no action button.",
            }

    # 5. Nothing actionable, and the page says more content follows below.
    if _has_more_content(graph):
        return {
            "action": "scroll",
            "direction": "down",
            "reason": "Nothing actionable in view and the page indicates more content below.",
        }

    total_inputs = len(inputs)
    sensitive = graph.get("sensitiveItemsCount", 0)
    summary = (
        f"This page ('{graph.get('pageTitle', 'untitled')}') has {total_inputs} form field(s), "
        f"{sensitive} of which were redacted locally."
    )
    return {"action": "summarize", "summary": summary, "reason": "No urgent field or action found."}


def decide_action(graph: dict, model: Optional[str] = None) -> dict:
    model_action = call_local_llm(graph, model)
    if model_action is not None:
        used_model = model_action.pop("_model", model or LOCAL_LLM_MODEL)
        return {**model_action, "decidedBy": f"local-llm:{used_model}"}
    return {**decide_action_rules(graph), "decidedBy": "rule-engine"}


def decide_chat_response(query: str, graph_dict: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    q = query.lower().strip()

    # 0. Greetings
    if GREETING_PATTERN.match(q):
        return {
            "reply": "Hi! I'm Aiva Nex Agent. I can scan this page, answer questions, or help you search/fill/summarize things - what would you like to do?",
            "action": "chat_reply",
            "suggested_actions": [
                {"label": "📄 Summarize current page", "query": "summarize page"},
                {"label": "📜 Scroll down", "query": "scroll down"},
            ],
        }

    # 1. Direct Web Search & Navigation Commands (Google, YouTube, Wikipedia)
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

    # 2. Generic web search ("search for X", "price for X"). No site is
    # hardcoded here: the side panel's intent parser (extension/intent.js)
    # handles "search X in <site>" on-device before /chat is ever called.
    if any(k in q for k in ["price for", "prices for", "best price", "search for"]):
        clean_query = q
        for rm in ["best prices for", "best price for", "best prices of", "best price of", "prices for", "price for", "search for"]:
            clean_query = clean_query.replace(rm, "")
        clean_query = clean_query.strip() or query.strip()
        return {
            "reply": f"Searching Google for '{clean_query}'...",
            "action": "open_url",
            "url": f"https://www.google.com/search?q={urllib.parse.quote(clean_query)}",
            "suggested_actions": [
                {"label": "📜 Scroll down results", "query": "scroll down"},
                {"label": "📄 Summarize page", "query": "summarize page"},
            ]
        }

    # 3. Order Confirmation Intent
    if _any_word_in(q, ["confirm order", "place order", "place_order_final", "confirm_order", "proceed to place order"]):
        return {
            "reply": "Please review your order summary before final placement:",
            "action": "request_order_confirmation",
            "order_summary": {
                "item": graph_dict.get("pageTitle") or "Selected Product",
                "price": "Check page price",
                "shipping_address": "[Stored Locally on Device]",
                "delivery": "Express Delivery (2-3 Business Days)"
            },
            "suggested_actions": [
                {"label": "🛍️ Place Order Now", "query": "place_order_final"},
                {"label": "❌ Cancel Order", "query": "cancel"}
            ]
        }

    # 4. Buy / Autofill Intent
    if _any_word_in(q, ["buy", "autofill", "apply", "checkout", "confirm_autofill"]):
        return {
            "reply": "I can autofill your details (Name, Email, Phone, Address/Location) directly from your on-device local storage. Your PII will NEVER be sent to the server.",
            "action": "request_autofill_permission",
            "required_fields": ["PERSON", "EMAIL", "PHONE", "ADDRESS"],
            "suggested_actions": [
                {"label": "✅ Confirm Autofill", "query": "confirm_autofill"},
                {"label": "❌ Cancel", "query": "cancel"}
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

    # 6. Page Summarize Intent (Uses Local LLM + Rich Page Text Snippets)
    if "summarize" in q or "summarise" in q or "summary" in q:
        llm_summary = call_local_llm_chat("Summarize the key information, titles, and search results on this page concisely.", graph_dict, model)
        if llm_summary:
            return {
                "reply": f"**Page Summary:**\n\n{llm_summary}",
                "action": "summarize",
                "summary": llm_summary
            }

        title = graph_dict.get("pageTitle", "current page") if graph_dict else "current page"
        headings = graph_dict.get("headings") or []
        snippets = graph_dict.get("textSnippets") or []

        if headings or snippets:
            extracted_text = " • ".join(headings[:3] + snippets[:3])
            summary_text = f"Page '{title}' highlights: {extracted_text}"
        else:
            inputs_count = len(graph_dict.get("inputs") or []) if graph_dict else 0
            summary_text = f"Page '{title}' scanned with {inputs_count} form fields."

        return {
            "reply": summary_text,
            "action": "summarize",
            "summary": summary_text
        }

    # 7. ANY General Knowledge Question or Chat Query (Local LLM First)
    llm_reply = call_local_llm_chat(query, graph_dict, model)
    if llm_reply:
        return {
            "reply": llm_reply,
            "action": "chat_reply",
            "suggested_actions": [
                {"label": f"🔍 Search '{query[:20]}' on Google", "query": f"search {query}"},
                {"label": "📄 Summarize current page", "query": "summarize page"}
            ]
        }

    # Fallback: LLM unavailable. Don't silently turn arbitrary chat text into
    # a web search (that is how "summarize the page" used to end up on
    # Google) - explain and offer explicit, deterministic commands instead.
    return {
        "reply": "No local model is available to answer that. Try a command: 'search <item>', 'search <item> on <site>', 'summarize this page', 'scroll down', 'click <label>' or 'fill form'.",
        "action": "chat_reply",
        "suggested_actions": [
            {"label": f"🔍 Search '{query[:20]}' on Google", "query": f"search {query}"},
            {"label": "📄 Summarize page", "query": "summarize page"}
        ]
    }


@app.get("/health")
def health():
    return {"status": "ok"}


def _local_llm_pingable() -> bool:
    try:
        req = urllib.request.Request(f"{LOCAL_LLM_BASE_URL.rstrip('/')}/models", method="GET")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError):
        return False


@app.get("/health/llm")
def health_llm():
    return {"reachable": _local_llm_pingable(), "baseUrl": LOCAL_LLM_BASE_URL, "model": LOCAL_LLM_MODEL}


@app.get("/models")
def list_models():
    try:
        req = urllib.request.Request(f"{LOCAL_LLM_BASE_URL.rstrip('/')}/models", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        ids = [m["id"] for m in payload.get("data", []) if m.get("id")]
    except Exception:
        ids = []

    if not ids:
        ids = [LOCAL_LLM_MODEL]

    return {"models": ids, "default": LOCAL_LLM_MODEL if LOCAL_LLM_MODEL in ids else ids[0]}


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

    graph_dict = enforce_ner_policy(graph_dict, "payload")  # feature/ner: tokenise or reject NAME/ADDRESS

    # attach_visual_target is a no-op unless the extension sent visualElements.
    return attach_visual_target(decide_action(graph_dict, model=graph.model), graph_dict)


@app.post("/chat")
def chat(req: ChatRequest):
    graph_dict = req.graph or {}

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

    clean = enforce_ner_policy({"message": req.message, "graph": graph_dict}, "chat request")  # feature/ner
    return decide_chat_response(clean["message"], clean["graph"], model=req.model)


if __name__ == "__main__":
    import uvicorn

    # [privacy-hardening] Show the token once so it can be pasted into the
    # extension's Settings panel (local console only, never logged).
    print(f"Aiva shared token (paste into extension Settings): {SHARED_TOKEN}")
    uvicorn.run(app, host="127.0.0.1", port=8000)
