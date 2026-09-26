# Aiva Nex Agent — Privacy-Preserving Browser Agent

A Chrome extension + local server prototype (built for SIH) that demonstrates
a browser AI agent which understands a page, **redacts sensitive data
entirely on-device**, and only ever sends anonymized context to a server
before executing a returned action back in the browser.

## The privacy guarantee, in one sentence

**No raw screenshot and no raw PII value ever leaves the browser.** Detection,
classification, and tokenization all happen in the content script, before the
popup or the server ever sees the data. The server independently re-checks
every incoming payload and rejects anything that still contains validated
raw PII.

**By default nothing leaves the machine.** The server only talks to an LLM on
`localhost`; there is no cloud call and no telemetry unless you explicitly
opt in (see [Privacy and network behaviour](#privacy-and-network-behaviour)).

## Quick start (for reviewers)

```bash
# 1. Server - prints a shared token on start (also saved in server/.aiva_token)
cd server && pip install -r requirements.txt && python main.py

# 2. Demo page (separate terminal)
cd demo && python -m http.server 5500
```
Then in Chrome/Brave/Edge: `chrome://extensions` → enable **Developer mode**
→ **Load unpacked** → select the `extension/` folder → click the toolbar icon
→ **⚙ Settings** → paste the server token → **Save & Test Connection** → open
`http://localhost:5500/demo-form.html` → **Scan Page** → **Send to Server** →
**Execute Action**. Full walkthrough in [Setup](#setup) below.

Run all tests (from the repo root, no network needed):

```bash
node --test "extension/tests/*.test.js" && cd server && python -m pytest -q
```

## Problem statement coverage

| Requirement | Where it's implemented |
|---|---|
| Manifest V3 Chrome extension | `extension/manifest.json` |
| Content script scans the DOM | `extension/content.js` → `collectFields()`, `collectButtons()`, `collectLinks()` |
| Local detection: passwords, email, phone, Aadhaar (Verhoeff), PAN (strict format), cards (Luhn), names, addresses, OTP, hidden inputs | `extension/pii-checks.js` → `detectPII()`; `extension/content.js` → `LABEL_HINTS`, `analyzeField()` (see [What gets detected](#what-gets-detected-and-tokenized)) |
| Tokenization before send (`PERSON_1`, `EMAIL_1`, `PASSWORD_FIELD`, ...) | `extension/pii-checks.js` → `redactAllPII()`; `extension/content.js` → `literalToken()` |
| Anonymized "screen graph" JSON (title, domain-only, forms, labels, types, sanitized values, buttons, links, approx. positions) | `extension/content.js` → `buildScreenGraph()` (see [The screen graph sent to the server](#the-screen-graph-sent-to-the-server)) |
| Popup UI: scan / count / JSON preview / send / action / execute | `extension/popup.html`, `popup.js` |
| Visual redaction overlay on sensitive fields | `extension/content.js` → `applyRedactionOverlay()` |
| FastAPI `POST /analyze`, validates no raw PII, returns an action | `server/main.py` → `find_raw_pii()`, `/analyze`; checks in `server/pii_checks.py` |
| Local server hardening: CORS locked to the extension, shared-token auth, 256 KB body cap, field limits | `server/security.py`; token entry in `extension/settings.js` |
| Action commands: click / focus / scroll / summarize | `server/main.py` → `decide_action_rules()`, `call_local_llm()`; executed in `extension/content.js` → `executeAction()` |
| Demo page: scholarship/job form with all required fields | `demo/demo-form.html` |
| **Beyond the brief:** a real local LLM decides actions when one is running (Ollama/LM Studio/etc.), with an automatic rule-based fallback so the demo can't break | `server/main.py` → `decide_action()` (see [Using a local model](#using-a-local-model)) |
| **Beyond the brief:** in-popup feedback to a real inbox - **off by default**, sent only after opting in under Settings | `extension/popup.html/js` → feedback section; `extension/background.js` → `SEND_FEEDBACK` |

---

## How it works

```
┌─────────────────────────┐
│   Web page (any tab)    │
│  ┌────────────────────┐ │
│  │   content.js        │ │  1. Scans the DOM
│  │  - detect PII        │ │  2. Classifies + tokenizes sensitive fields
│  │  - tokenize          │ │     locally (PERSON_1, EMAIL_1, ...)
│  │  - redact overlay    │ │  3. Draws a visual mask over sensitive fields
│  └─────────┬────────────┘ │
└────────────┼─────────────┘
             │ sanitized screen graph (JSON)
             ▼
     ┌───────────────┐        ┌──────────────────────┐
     │   popup.js     │◄──────►│    background.js      │
     │ (scan/preview/ │  msg   │ (relays fetch() only,  │
     │  send/execute) │        │  never touches DOM)    │
     └───────────────┘        └───────────┬───────────┘
                                           │ POST /analyze
                                           ▼
                                ┌─────────────────────┐
                                │  FastAPI server      │
                                │ 1. re-checks for raw │
                                │    PII (reject if    │
                                │    found)            │
                                │ 2. decides an action │
                                │    (rule engine —    │
                                │    stand-in for an   │
                                │    LLM/VLM call)      │
                                └──────────┬───────────┘
                                           │ action JSON
                                           ▼
                              content.js executes it
                              (click / focus / scroll / summarize)
```

## Project structure

```
privacy-browser-agent/
  extension/
    manifest.json     Manifest V3 config
    pii-checks.js      Pure PII detection + tokenisation (Verhoeff, Luhn, PAN), unit-tested
    content.js         DOM scanning, field classification, redaction, action execution
    background.js      Service worker — relays the sanitized graph to the server (adds X-Aiva-Token)
    popup.html/.css/.js  Side-panel UI
    settings.js        Settings panel: server token, feedback opt-in
    tests/             node:test unit tests
  server/
    main.py            FastAPI app: /health, /analyze, /chat, /models
    pii_checks.py      Same detection logic as pii-checks.js (Python)
    security.py        CORS, shared token, body-size cap, safe 422 errors
    tests/             pytest suite
    requirements.txt, requirements-dev.txt
  demo/
    demo-form.html      Sample scholarship/job application form for the demo
  README.md
```

## Tech stack

- Chrome Extension, Manifest V3, plain JavaScript (no build step, no frameworks)
- FastAPI + Pydantic, Python 3.11+
- No heavy dependencies (no ML libraries in the server itself) — detection is
  regex + DOM-label heuristics, and the "decision" step talks to a **local**
  LLM over plain HTTP (stdlib `urllib`, no new pip package) and falls back to
  a small rule-based engine if none is reachable — see
  [Using a local model](#using-a-local-model).

---

## Setup

### 1. Start the FastAPI server

```bash
cd server
pip install -r requirements.txt
python main.py
```

The server runs on `http://127.0.0.1:8000`. Confirm it's up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

On first start the server creates a random shared token in
`server/.aiva_token` (git-ignored) and prints it. Every endpoint except
`/health` (and the `/docs` pages) requires it in the `X-Aiva-Token` header,
otherwise it answers `401`. To use a fixed token instead, set
`AIVA_SHARED_TOKEN` before starting. For the demo, also set the extension's
origin so CORS only admits it (the ID is shown on `chrome://extensions`):

```powershell
$env:AIVA_EXTENSION_ORIGIN = "chrome-extension://<your-extension-id>"
python main.py
```

`http://localhost:*` / `http://127.0.0.1:*` are always allowed for
development; nothing else is. Request bodies over 256 KB get `413`
(`AIVA_MAX_BODY_BYTES` to change).

Interactive API docs (Swagger UI) are available at
`http://127.0.0.1:8000/docs` if you want to inspect `/analyze` directly.

### 2. Load the extension in Chrome

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select the `extension/` folder.
4. `Aiva Nex Agent` should appear in your extensions list and toolbar.
5. Open the side panel → **⚙ Settings** → paste the server token →
   **Save & Test Connection** (it should say "Connected"). The token is kept
   in `chrome.storage.local` only.

### 3. Open the demo page

The content script runs on any `http(s)` page automatically. The simplest
way to open the demo form with zero extra Chrome settings:

```bash
cd demo
python -m http.server 5500
```

Then visit `http://localhost:5500/demo-form.html` in Chrome.

> **Alternative (file:// URLs):** you can instead double-click
> `demo-form.html` to open it directly, but Chrome disables extensions on
> `file://` pages by default. If you go this route, open
> `chrome://extensions`, click **Details** on `Aiva Nex Agent`, and turn on
> **Allow access to file URLs**, then reload the page.

### 4. Run the demo flow

1. Fill in a few fields on the demo form (or leave them — the flow works
   either way).
2. Click the `Aiva Nex Agent` toolbar icon to open the popup.
3. Click **Scan Page**.
   - The popup shows how many sensitive items were found and a live JSON
     preview of the sanitized screen graph.
   - On the page itself, every sensitive field (password, Aadhaar, PAN,
     OTP, email, phone, hidden CSRF token, etc.) is now visually covered
     with a redaction overlay showing its token (e.g. `🔒 EMAIL_1`).
4. Click **Send to Server (Sanitized Only)**.
   - Only the JSON shown in the preview is sent — never the real field
     values.
5. The popup shows the action the server decided on (e.g. *"focus the OTP
   field"* or *"click Upload Certificate"*).
6. Click **Execute Action** — the extension carries it out on the real page
   (scrolls to and focuses/clicks the target element, or shows a summary
   banner).

Try clearing the OTP field, then a name field, then filling everything in —
the server's decision changes each time (OTP → empty required field →
upload button → page summary), which is a good way to show the "agent"
reasoning during a live demo.

---

## What gets detected and tokenized

| Detected as              | Token(s)                         | How it's found |
|---------------------------|-----------------------------------|----------------|
| Password fields            | `PASSWORD_FIELD`                 | `input[type=password]` |
| OTP fields                 | `OTP_FIELD`                      | label/name/placeholder hint (`otp`, `one-time code`) |
| Hidden inputs               | `HIDDEN_FIELD`                   | `input[type=hidden]` or CSS-hidden |
| Email addresses            | `EMAIL_1`, `EMAIL_2`, …           | label hint, or regex fallback on the value |
| Phone numbers (Indian)      | `PHONE_1`, …                      | label hint, or `+91`/`91` optional + 10 digits starting 6-9 |
| Aadhaar numbers             | `ID_NUMBER_1`, …                  | label hint, or 12 digits, first digit 2-9 (+ Verhoeff checksum = validated) |
| PAN                         | `ID_NUMBER_1`, …                  | label hint, or `[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]` (validated) |
| Card numbers (13–19 digits, spaces/dashes ok) | `CARD_1`, …     | label hint, or digit run (+ Luhn checksum = validated) |
| Names                        | `PERSON_1`, …                    | field label containing "name" (best-effort — see limitations) |
| Addresses                    | `ADDRESS_1`, …                   | field label containing "address" (best-effort) |

Numbered tokens increment per type so multiple instances on one page stay
distinguishable (`EMAIL_1`, `EMAIL_2`, ...). `PASSWORD_FIELD` and
`OTP_FIELD` are literal — a second occurrence becomes `OTP_FIELD_2`, etc.

Detection runs in this priority order for any given field: **field type**
(password/hidden) → **label/name/placeholder hint** → **regex scan of the
actual value** as a fallback for unlabeled free text (e.g. a "comments" box
that happens to contain an email or phone number).

**Candidates vs. validated (the false-positive policy).** The extension
tokenises every value of the right *shape* — a 12-digit number starting 2-9
becomes `ID_NUMBER_n` even if its Verhoeff digit is wrong, because
over-redacting costs nothing. The server's reject rule is stricter: it only
returns `400` for *validated* PII (Verhoeff-valid Aadhaar, Luhn-valid card,
strict-format PAN, phone, email). So an order number like `234567890123` or
a 16-digit reference that fails Luhn no longer causes a false rejection.
The exact same logic lives in `extension/pii-checks.js` and
`server/pii_checks.py`, and both test suites use the same test vectors.

## The screen graph sent to the server

```json
{
  "pageTitle": "National Scholarship & Job Portal - Application Form",
  "domain": "localhost",
  "scannedAt": "2026-08-09T12:00:00.000Z",
  "forms": [{ "ref": "form-0", "fieldRefs": ["input-0", "input-1", "..."] }],
  "inputs": [
    { "ref": "input-1", "type": "text", "label": "Full Name", "required": true,
      "isSensitive": true, "sanitizedValue": "PERSON_1",
      "position": { "x": 100, "y": 150, "width": 300, "height": 30 } }
  ],
  "buttons": [{ "ref": "button-0", "text": "Upload Certificate", "position": { "...": "..." } }],
  "links": [{ "ref": "link-0", "text": "Home", "hrefDomain": "localhost", "position": { "...": "..." } }],
  "sensitiveItemsCount": 8,
  "detectedTypes": { "PERSON": 1, "EMAIL": 1, "OTP_FIELD": 1 }
}
```

Notes on what's deliberately **not** included:
- The full URL — only `domain` (hostname).
- Link `href`s — only their `hrefDomain`.
- Exact pixel positions — rounded to the nearest 5px ("approximate" per the
  spec, and it reduces fingerprinting).
- Anything that was classified as sensitive — its `sanitizedValue` is always
  the token, never the original text.

## Server API

- `GET /health` → `{"status": "ok"}`
- `GET /health/llm` → `{"reachable": true/false, "baseUrl": "...", "model": "..."}` —
  lets you check whether a local model is actually reachable without running
  the full `/analyze` flow.
- `POST /analyze` → receives a screen graph, returns one action:

  ```json
  { "action": "focus", "targetRef": "input-7", "reason": "...", "decidedBy": "local-llm:llama3.2:1b" }
  { "action": "click",  "targetRef": "button-0", "reason": "...", "decidedBy": "rule-engine" }
  { "action": "scroll", "direction": "down" }
  { "action": "summarize", "summary": "...", "reason": "..." }
  ```

  `decidedBy` tells you whether a real local model answered, or the
  rule-based fallback did — the popup shows this too.

  Before deciding anything, the server re-scans every string in the request
  for validated PII (email, phone, Verhoeff-valid Aadhaar, strict PAN,
  Luhn-valid card). If any is found, it responds `400` naming only the type,
  never the value — a defense-in-depth backstop in case client-side
  redaction ever has a bug, not the primary defense.

- `POST /chat` → same PII check on the message and graph, then answers.
- All endpoints except `/health` need the `X-Aiva-Token` header (`401`
  otherwise). Oversized bodies get `413`, over-long fields `422` (the error
  never echoes the submitted text back).

## Privacy and network behaviour

| Path | Default | How to enable |
|---|---|---|
| Local LLM (`LOCAL_LLM_BASE_URL`) | On, **loopback only** (`localhost`/`127.x`/`::1`) | A non-loopback URL is refused unless `AIVA_ALLOW_CLOUD=1` |
| Gemini cloud fallback | **Off** | `AIVA_ALLOW_CLOUD=1` **and** `GEMINI_API_KEY` (model via `GEMINI_MODEL`, default `gemini-2.5-flash`). Sends *sanitized* page context to Google; the server logs a loud warning on start and on every call |
| Feedback to `manager.aivafreelancia.in` | **Off** | Tick the opt-in in the side panel's ⚙ Settings |
| Chat "open Google/Amazon/YouTube search" | Only when you ask for it | Opens a normal browser tab with *your typed query*, not page content |

The server never logs request bodies, page text or PII; `uvicorn` access
logs contain only method, path and status.

## Running the tests

```bash
# Extension (Node 18+, no dependencies)
node --test "extension/tests/*.test.js"

# Server (one-time: python -m venv server/.venv && server/.venv/Scripts/pip install -r server/requirements-dev.txt)
cd server && python -m pytest -q
```

## Using a local model

`/analyze` tries a real local LLM first and only falls back to the
rule-based engine if no model is reachable, its response can't be parsed, or
it names a `targetRef` that doesn't actually exist on the page (so a
confused small model can never make the extension act on nothing). This
makes the demo unbreakable either way — with a model running you get real
model reasoning, without one you get the same reliable rule-based behavior.

It talks to any server that speaks the OpenAI `/v1/chat/completions` format
on localhost — no new pip dependency, just one HTTP call via the Python
standard library. A `LOCAL_LLM_BASE_URL` that points anywhere other than
this machine is refused unless you set `AIVA_ALLOW_CLOUD=1`.

**Recommended: [Ollama](https://ollama.com)** — runs headless as a
background service, no GUI app needed:

```bash
# after installing Ollama
ollama pull llama3.2:1b   # ~1.3GB, good balance of speed and instruction-following
python main.py            # server auto-detects it - no config needed
```

**Or let a script do the above for you:**

```powershell
# Windows
./scripts/setup-local-llm.ps1
```
```bash
# macOS/Linux
./scripts/setup-local-llm.sh
```

It checks whether Ollama is installed, asks before installing it if not
(via winget on Windows / the official installer on macOS-Linux), waits for
the service to come up, and pulls the model. It never runs without your
confirmation — nothing in the server or extension triggers it on its own.

Want something even lighter? `ollama pull qwen2.5:0.5b` (~350MB) works too —
set `LOCAL_LLM_MODEL=qwen2.5:0.5b` before starting the server. Very small
models are less reliable at producing clean JSON, but that's exactly what
the fallback exists for.

**Alternative: LM Studio, Jan, or anything else OpenAI-compatible** — start
its local server, then point the app at it:

```bash
# Windows PowerShell
$env:LOCAL_LLM_BASE_URL = "http://localhost:1234/v1"   # LM Studio's default
$env:LOCAL_LLM_MODEL = "<model name as loaded in LM Studio>"
python main.py
```

**Nothing installed at all?** That's fine — `/analyze` just always falls
back to the rule-based engine, exactly like before this feature existed.
Nothing else about the demo changes.

---

## Limitations

Being upfront about what this prototype does and doesn't do:

- **The local model is genuinely optional and unverified by default.** With
  nothing installed, every decision comes from the rule-based engine
  (`decide_action_rules()` in `server/main.py`) — reliable, but not "AI
  reasoning." A tiny model (0.5B–1B params) can also just be wrong or slow;
  the fallback logic only catches *malformed* responses, not *bad* ones.
- **Name and address detection are label-based heuristics**, not true NER.
  A field is classified as a name/address because its label/placeholder/name
  attribute says so — this is reliable on structured forms (the actual use
  case here) but won't catch a name mentioned in unlabeled free text.
- **Checksums confirm shape, not ownership.** Verhoeff (Aadhaar) and Luhn
  (cards) tell a real-looking number from a random one; they cannot tell
  whether it belongs to anyone. Phone numbers have no checksum, so any
  10-digit number starting 6-9 counts as a phone.
- **Redaction overlays reposition on scroll/resize but are a visual aid**,
  not a security boundary — the actual privacy guarantee is that raw values
  never enter the JSON that gets sent, independent of what's drawn on screen.
- The shared token stops other web pages and local apps from driving the
  server, but it is a single static secret, not per-user auth. The server
  binds to `127.0.0.1` only and is not meant to be exposed further.
