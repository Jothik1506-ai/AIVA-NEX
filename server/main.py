"""
Aiva Nex Agent server (SIH prototype)

Receives ONLY a sanitized "screen graph" JSON from the browser extension -
never a screenshot, never raw PII. Before doing anything else, it re-checks
the payload text for PII-shaped substrings as a defense-in-depth backstop.
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScreenGraph(BaseModel):
    model_config = ConfigDict(extra="allow")

    pageTitle: Optional[str] = None
    domain: Optional[str] = None
    scannedAt: Optional[str] = None
    headings: Optional[List[str]] = None
    textSnippets: Optional[List[str]] = None
    forms: Optional[List[Dict[str, Any]]] = None
    inputs: Optional[List[Dict[str, Any]]] = None
    buttons: Optional[List[Dict[str, Any]]] = None
    links: Optional[List[Dict[str, Any]]] = None
    sensitiveItemsCount: Optional[int] = 0
    detectedTypes: Optional[Dict[str, int]] = None
    model: Optional[str] = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: str
    graph: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None


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


LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "llama3.2:1b")
LOCAL_LLM_TIMEOUT_SECONDS = 20
CHAT_LLM_TIMEOUT_SECONDS = 15

# Gemini API (free) as cloud LLM fallback - set GEMINI_API_KEY env var to enable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

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
    """Build a text description of the current page from the screen graph."""
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
    """Call Gemini API (free tier) as cloud LLM fallback."""
    if not GEMINI_API_KEY:
        return None

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

    url = GEMINI_API_URL.format(model=GEMINI_MODEL, key=GEMINI_API_KEY)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


def call_local_llm_chat(query: str, graph_dict: Dict[str, Any], model: Optional[str] = None) -> Optional[str]:
    """Answers user queries using local Ollama LLM, with Gemini API as fallback."""
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

    # Fallback to Gemini API if Ollama unavailable
    return call_gemini_chat(query, graph_dict)


GREETING_PATTERN = re.compile(r"^\s*(hi+|hello+|hey+|yo|good\s*(morning|afternoon|evening)|sup)\s*[!.?]*\s*$", re.IGNORECASE)


def _any_word_in(q: str, words: List[str]) -> bool:
    return any(re.search(r"(?<![a-zA-Z])" + re.escape(w) + r"(?![a-zA-Z])", q) for w in words)


def decide_action_rules(graph: dict) -> dict:
    inputs = graph.get("inputs") or []
    buttons = graph.get("buttons") or []

    for inp in inputs:
        stype = inp.get("sensitiveType")
        if stype in ("PASSWORD", "OTP"):
            return {
                "action": "focus",
                "targetRef": inp["ref"],
                "reason": f"An {stype} field was detected and likely needs input next.",
            }

    for btn in buttons:
        txt = (btn.get("text") or "").lower()
        if any(w in txt for w in ["submit", "continue", "next", "login", "pay", "proceed", "apply"]):
            return {
                "action": "click",
                "targetRef": btn["ref"],
                "reason": f"A primary action button ('{btn.get('text')}') is ready to be clicked.",
            }

    for inp in inputs:
        if inp.get("sensitive"):
            return {
                "action": "focus",
                "targetRef": inp["ref"],
                "reason": "Focusing the next sensitive input field.",
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
                {"label": "🔍 Search on Google", "query": "open google search for "},
                {"label": "📄 Summarize current page", "query": "summarize page"},
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

    # 2. Dynamic Product & E-Commerce Search (Amazon, Flipkart, eBay, Google, etc.)
    if any(k in q for k in ["amazon", "flipkart", "ebay", "myntra", "meesho", "price for", "prices for", "best price", "search for"]):
        platform = "amazon" if "amazon" in q else ("flipkart" if "flipkart" in q else ("ebay" if "ebay" in q else "google"))
        clean_query = q
        for rm in ["best prices for", "best price for", "best prices of", "best price of", "prices for", "price for", "in amazon", "on amazon", "in flipkart", "on flipkart", "in ebay", "on ebay", "search for", "find"]:
            clean_query = clean_query.replace(rm, "")
        clean_query = clean_query.strip() or query.strip()

        if platform == "amazon":
            url = f"https://www.amazon.in/s?k={urllib.parse.quote(clean_query)}"
        elif platform == "flipkart":
            url = f"https://www.flipkart.com/search?q={urllib.parse.quote(clean_query)}"
        elif platform == "ebay":
            url = f"https://www.ebay.com/sch/i.html?_nkw={urllib.parse.quote(clean_query)}"
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote(clean_query)}"

        return {
            "reply": f"Searching {platform.capitalize()} for '{clean_query}'...",
            "action": "open_url",
            "url": url,
            "summary": f"Navigating to {platform.capitalize()} to view listings for '{clean_query}'.",
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
    if "summarize" in q or "summary" in q:
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
                {"label": f"🔍 Search '{query[:20]}' on Google", "query": f"open google search for {query}"},
                {"label": "📄 Summarize current page", "query": "summarize page"}
            ]
        }

    # Fallback: LLM unavailable - auto open Google search for the query
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    return {
        "reply": f"Searching Google for '{query}'...",
        "action": "open_url",
        "url": search_url,
        "suggested_actions": [
            {"label": "📜 Scroll down", "query": "scroll down"},
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

    return decide_action(graph_dict, model=graph.model)


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

    return decide_chat_response(req.message, graph_dict, model=req.model)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
