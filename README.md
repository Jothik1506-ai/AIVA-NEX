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
In optional Visual mode only a screenshot whose sensitive regions were
blacked out on-device is sent, and only to the local server (see
[Visual perception](#visual-perception-sih26171)).

## Demo setup

Everything runs on one Windows laptop, CPU only, no network after install.

**1. Server (one-time install, then start).**

```powershell
cd server
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # runtime (FastAPI, spaCy + en_core_web_sm, RapidOCR, OpenCV) + test deps
$env:AIVA_EXTENSION_ORIGIN = "chrome-extension://<your-extension-id>"   # from chrome://extensions
.venv\Scripts\python main.py
```

On first start the server creates a random shared token, prints it and saves
it to `server/.aiva_token` (git-ignored). Every route except `/health`
(`/analyze`, `/chat`, `/models`, `/ner/scan`, `/perceive`, ...) needs it in the
`X-Aiva-Token` header, else `401`. `AIVA_EXTENSION_ORIGIN` locks CORS to the
extension (`localhost` pages are always allowed for development). Bodies over
256 KB get `413`, except `/perceive` (one masked screenshot), capped at 4 MB
(`AIVA_MAX_BODY_BYTES` / `AIVA_MAX_PERCEIVE_BYTES`).

**2. Demo page.** `cd demo; python -m http.server 5500`, then open
`http://localhost:5500/demo-form.html`.

**3. Extension.** `chrome://extensions` → **Developer mode** → **Load unpacked**
→ `extension/` (or **Reload** if already loaded — the manifest changed). Copy
the extension ID into `AIVA_EXTENSION_ORIGIN` above and restart the server.
Open the side panel → **⚙ Settings** → paste the token from
`server/.aiva_token` → **Save & Test Connection** ("Connected").

**4. DOM flow.** **Scan Page** → **Send to Server** → **Execute Action**.

**5. Visual mode (SIH26171).** **Scan Page** → click **👁 Visual** (turns
purple) → **Send Context**. The panel shows the exact masked JPEG that left
the browser (sensitive fields blacked out), the OCR/CV elements and timings;
the chosen element is outlined in purple, **Execute** acts on it.

**6. NER policy** (`AIVA_NER_POLICY`, set before starting the server):
`tokenize` (default) turns free-text names/addresses into `NAME_n` /
`ADDRESS_n` in `/analyze`, `/chat` and OCR text from `/perceive`; `reject`
answers `400` for names/addresses in `/analyze`/`/chat` and masks them as
`[NAME]` / `[ADDRESS]` in OCR text; `off` disables it. Demo Aadhaar test
number (UIDAI sandbox, checksum-valid): `9999 4105 7058`.

**7. Tests** (from the repo root, no network needed):

```powershell
node --test "extension/tests/*.test.js"
cd server; .venv\Scripts\python -m pytest -q          # add -m "not slow" to skip OCR model tests
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
| **SIH26171:** on-device visual perception — masked screenshot → local OCR + CV → visual elements the agent can act on | `extension/vision.js`, `extension/vision-content.js`, `server/vision/`, `POST /perceive` (see [Visual perception](#visual-perception-sih26171)) |
| On-device name/address detection (NER) in free text and OCR text | `extension/ner-rules.js`, `server/ner/` (see [Name & address detection](#name--address-detection-ner)) |
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
    ner-rules.js       Client-side NAME/ADDRESS rules (hooked into pii-checks.js redactAllPII)
    content.js         DOM scanning, field classification, redaction, action execution
    background.js      Service worker — relays to the server via serverFetch() (adds X-Aiva-Token)
    popup.html/.css/.js  Side-panel UI
    settings.js        Settings panel: server token, feedback opt-in
    vision.js          Visual mode: capture, on-device masking, /perceive, highlight
    vision-content.js  Reports sensitive-field rects (no values), draws highlight
    tests/             node:test unit tests
  server/
    main.py            FastAPI app: /health, /analyze, /chat, /models, /ner/scan, /perceive
    pii_checks.py      Same detection logic as pii-checks.js (Python)
    security.py        CORS, shared token, body-size caps, safe 422 errors
    ner/               spaCy en_core_web_sm + Indian rule layer (names/addresses)
    vision/            OCR (RapidOCR/ONNX) + OpenCV detection + fusion + PII/NER re-check
    tests/             pytest suite
    requirements.txt, requirements-dev.txt
  demo/
    demo-form.html      Sample scholarship/job application form for the demo
  README.md
```

## Tech stack

- Chrome Extension, Manifest V3, plain JavaScript (no build step, no frameworks)
- FastAPI + Pydantic, Python 3.11+
- CPU-only ML on the server: spaCy `en_core_web_sm` (names/addresses) and
  RapidOCR (ONNX) + OpenCV (Visual mode). The "decision" step talks to a
  **local** LLM over plain HTTP (stdlib `urllib`) and falls back to a small
  rule-based engine if none is reachable — see
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
- `POST /perceive` → masked screenshot in, visual elements out (see
  [Visual perception](#visual-perception-sih26171)); `GET /perceive/health`.
- `POST /ner/scan` → dev/debug NAME/ADDRESS spans + tokenised text.
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

See step 7 of [Demo setup](#demo-setup). The server suite covers privacy
hardening (`test_api.py`, `test_pii_checks.py`), NER (`test_ner.py`), visual
perception (`test_vision.py`) and the cross-feature checks (`test_integration.py`:
token + body cap on `/perceive` and `/ner/scan`, NER over OCR text).

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

## Name & address detection (NER)

Form fields labelled "Name"/"Address" were always tokenised, but a name or
address typed into free text ("Deliver to Smt. Lakshmi Devi, Flat 4B, Sai
Residency, Ameerpet Road, Hyderabad 500016") used to go through untouched.
Now personal **names** become `NAME_n` and **addresses** become `ADDRESS_n`,
fully on-device, CPU only.

### Setup (one-time)

```bash
cd server
python -m venv .venv && .venv\Scripts\activate     # (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt                    # includes spaCy + en_core_web_sm (~12 MB wheel)
# if the model wheel was skipped/failed:
python -m spacy download en_core_web_sm
```

If the model is missing the server still starts and uses the rule layer only
(`/ner/scan` reports `"engine": "rules-only"`).

### Two-layer design

| Layer | Where | What | Cost |
|---|---|---|---|
| 1. Rules | `extension/ner-rules.js`, inside `redactAllPII()` in the content script | Indian honorifics (Mr/Mrs/Shri/Smt/Kumari/Dr), relation markers (S/o, D/o, W/o, C/o), "my name is …", salutations, common Indian surnames; addresses scored from 6-digit PIN code, H.No/D.No/Flat/Plot numbers (`12-3-45`), locality words (Nagar, Colony, Road, Layout, Mandal, District, Village …), state and city names, cue phrases ("I live at", "deliver to") | ~0.4 ms per 2 KB |
| 2. Model + rules | `server/ner/` on the local FastAPI server | spaCy `en_core_web_sm` PERSON entities + the same Indian rules; GPE/LOC/FAC entities only add evidence to the address score | ~100–130 ms per 2 KB |

Layer 1 runs **before** anything leaves the browser, so the common Indian
formats are tokenised at the source. Layer 2 is the defence-in-depth
second check on everything that reaches `/analyze` and `/chat`, catching
free-text names the rules miss ("Order summary for Vikram Singh").

Rules for keeping false positives down: a lone city is never an address
("Hyderabad weather" stays as is); an address needs a score of 3 or more
(PIN = 2, house number = 2, locality word = 1, state = 1, city/landmark = 0.5,
cue phrase = 1); prices are never PIN codes (`Rs 500080`); tokens that are
already there (`EMAIL_1`, `PERSON_1`) are skipped; and model PERSON hits that
are really localities ("Gandhi Nagar") or UI words are dropped.

Within one request the same person or address always gets the same token
(`Mr. Ravi Kumar` and `Ravi Kumar` both become `NAME_1`). Numbering continues
after any `NAME_n`/`ADDRESS_n` the extension already produced, so tokens
never collide.

**Policy** (`AIVA_NER_POLICY`, default `tokenize`): the regex check still
**rejects** exact-shape PII (email, Aadhaar …) with HTTP 400. For NER hits the
server **tokenises** them before any LLM (local or cloud fallback) sees the
payload, because NER is probabilistic and real pages contain names (news
bylines, authors), so rejecting them would break ordinary pages. Set
`AIVA_NER_POLICY=reject` for the strict behaviour (400, same message format)
or `off` to disable it.

### Why spaCy `en_core_web_sm`

| Option | Size | CPU latency (2 KB) | Verdict |
|---|---|---|---|
| spaCy `en_core_web_sm` | 12 MB wheel, ~1.4 s cold load | ~70 ms model alone | **chosen**: tiny, pip-installable, no GPU/ONNX toolchain, recognises Indian names well enough ("Anjali Sharma", "Vikram Singh") |
| Small ONNX transformer NER (e.g. multilingual BERT/XLM-R, int8) | 100–300 MB plus tokenizer | several hundred ms on CPU | rejected for the prototype: 10–25× the download on a slow link, more dependencies (onnxruntime, tokenizers), still misses Indian address structure |

Neither model understands Indian *addresses* (the small model tags
"Gandhi Nagar" as a PERSON and "Hyderabad 500080" as an EVENT), so the Indian
rule layer does that job in both cases. Transformers would add weight
without solving the main problem.

### API

```python
from ner import detect_names_addresses, tokenize_text
detect_names_addresses("Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080")
# [{'start': 0, 'end': 10, 'type': 'NAME', 'score': 0.9},
#  {'start': 12, 'end': 56, 'type': 'ADDRESS', 'score': 0.99}]
tokenize_text("Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080")[0]
# 'NAME_1, ADDRESS_1'
```

`POST /ner/scan` with `{"text": "..."}` (dev/debug) returns
`{spans, tokenized, engine, elapsedMs}`. It never echoes the raw text back
and nothing in the NER package logs text (only counts).

### Latency (Ryzen 7 7730U, 15 GB RAM, CPU only)

```
cd server && python -m ner.bench
engine            : spacy:en_core_web_sm+rules
model load (cold) : ~1.4 s   (once per process, in a background thread at startup)
2 KB text         : median ~130 ms
screen graph (28 snippets): median ~100 ms
```

### Tests

```bash
cd server && pip install -r requirements-dev.txt && python -m pytest tests   # server (FastAPI + NER)
node --test extension/tests/*.test.js                                          # client rule layer
```

All test names and addresses are invented.

---

## Visual perception (SIH26171)

Problem statement SIH26171 asks for *on-device visual perception for
light-weight browser agents*. Besides reading the DOM, the agent can now
**look at the rendered screen**: toggle **👁 Visual** in the side panel and
the next **Send Context** runs a fully local vision step. This matters for
canvas-drawn UIs, image buttons, shadow-DOM widgets and anything whose DOM
does not say what the user actually sees.

### How it works

```
side panel (vision.js)                      local server (server/vision/)
 1. ask page for sensitive-field rects  ──┐
    (vision-content.js, rects only)       │
 2. chrome.tabs.captureVisibleTab         │
 3. OffscreenCanvas: downscale ≤1280px,   │
    paint SOLID BLACK over every          │
    sensitive rect  ── masked JPEG ──────►│ POST /perceive (127.0.0.1 only)
                                          │  a. RapidOCR (ONNX, CPU) → text lines
                                          │  b. OpenCV edges/contours → control boxes
                                          │  c. PII regex re-check on every OCR string
                                          │  d. fuse OCR + CV + sanitized DOM boxes →
                                          │     [{id, text, bbox, type_guess, confidence}]
 4. elements shown in panel  ◄────────────┘     + short screen summary
 5. POST /analyze {graph + visualElements} → action may target a visual id;
    response carries bbox → purple outline drawn on the chosen element
```

- **Model choice.** OCR is [RapidOCR](https://github.com/RapidAI/RapidOCR)
  (PaddleOCR PP-OCR det+rec exported to ONNX, ~15 MB, runs on onnxruntime's
  CPU provider — no GPU, no PyTorch). UI controls are found with classical
  OpenCV (Canny edges + rectangle contours whose whole border is an edge),
  which costs tens of milliseconds and needs no weights. A learned UI
  detector (YOLO / OmniParser-style) would add hundreds of MB and seconds of
  CPU time per frame for little gain on forms, so it was deliberately not
  used. The DOM boxes the extension already has are fused in when available
  (IoU match), giving each visual element a `domRef` the agent can act on.
- **Element types:** `button`, `input`, `checkbox`, `link` (blue ink),
  `heading`, `text`, and `masked_sensitive` (the black boxes). Inputs get a
  `label` from the nearest text above/left of them.
- **Acting on vision.** `/analyze` accepts an optional `visualElements`
  list. The LLM may target a visual id (`"v7"`), which is mapped to its DOM
  ref; if the DOM rule engine finds nothing it falls back to a visual rule
  (primary button → click, else first input → focus). Actions come back with
  `targetVisualId` + `bbox` (CSS px); a visual-only target with no DOM node
  is executed at the bbox centre (`document.elementFromPoint`).

### Privacy masking (before anything leaves the browser)

1. `vision-content.js` returns only **rectangles** (never text/values) of:
   fields `content.js` flagged as sensitive, its redaction overlays, and any
   `password` / `cc-*` / `one-time-code` input even if the scan missed it.
2. The side panel draws **opaque black boxes (4 px padding)** over those
   rects on an `OffscreenCanvas` and only then encodes the JPEG. The card in
   the panel shows the exact masked image that was sent.
3. If the page can't report rects (e.g. `chrome://` pages, content script
   missing) **no screenshot is taken or sent**.
4. The image goes only to `127.0.0.1:8000/perceive`, is processed in memory,
   and is never written to disk or returned. Every OCR string is re-checked
   with the same candidate patterns as `server/pii_checks.py` and matches are
   replaced by `[EMAIL]`, `[PHONE]`, … (`piiMaskedServerSide`), then names and
   addresses go through the NER detector and become `NAME_n` / `ADDRESS_n`
   (or `[NAME]` / `[ADDRESS]` with `AIVA_NER_POLICY=reject`) before any model
   sees them (`nerMaskedServerSide`). `/perceive` needs the shared token like
   every other route.

### Latency (CPU only)

Measured on the target laptop — AMD Ryzen 7 7730U, 15 GB RAM, no GPU,
Python 3.11, onnxruntime CPU — 1280×800 screenshot:

| Screen (1280×800 JPEG q90) | OCR lines | Elements | `/perceive` total, warm | first request after startup |
|---|---|---|---|---|
| Synthetic form (tests) | 7 | 8 | **~0.42 s** | — |
| `demo-form.html` scholarship form | ~20 | 23 | **~0.75–0.85 s** | ~0.85–0.95 s |
| `demo-form.html` product page | ~15 | 17 | **~0.77–0.86 s** | ~0.95 s |
| Wikipedia article (text-dense) | 80 | 93 | **~2.0–2.2 s** | ~2.2–2.3 s |

OCR is >95% of the time (OpenCV detection 10–45 ms, fusion <35 ms). Typical
forms/app screens are well under the 2 s target; very text-dense pages sit
right at it, because recognition cost grows with the number of text lines.
The server loads and warms the OCR models in a background thread at startup
(~4–5 s, once), so the user's first request is not a cold start. Two tuning
choices, both measured: recogniser batch size 1 (batching pads every line to
the widest one: 2.6 s → 1.45 s recognition on the dense page) and rounding
recogniser input widths up to 160 px buckets (onnxruntime pays a one-off cost
per new input shape: first sight of the dense page 5.4 s → 2.2 s).

Reproduce: `cd server; .venv\Scripts\python -m vision.bench [screenshot.png]`.

### One-time setup

```powershell
cd server
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

The RapidOCR ONNX models ship inside the `rapidocr-onnxruntime` wheel, so
this pip install is the only download; after it everything runs offline.
Check with `GET http://127.0.0.1:8000/perceive/health` →
`{"available": true, ...}`.

### Demo steps

1. `cd server; .venv\Scripts\python main.py` and serve the demo page
   (`cd demo; python -m http.server 5500`).
2. Reload the unpacked extension (manifest now also loads `vision-content.js`),
   open `http://localhost:5500/demo-form.html`, type a fake email/phone into
   the form, open the side panel.
3. **Scan Page** → click **👁 Visual** (turns purple) → **Send Context**.
4. The panel shows the masked screenshot that was sent (sensitive fields are
   black boxes), the screen summary, element list and OCR/CV timings.
5. The returned action names its visual target; the element is outlined in
   purple on the page. **Execute** performs it.

### Tests

```powershell
cd server
.venv\Scripts\python -m pytest            # all, incl. OCR tests
.venv\Scripts\python -m pytest -m "not slow"   # skip model-loading tests
```

`tests/test_vision.py` draws a synthetic form with PIL and checks that OCR
finds the expected labels, buttons/inputs are classified with correct boxes,
extension-style black masks hide content and are recognised, OCR text with
PII is masked server-side, `/perceive` fuses DOM refs at devicePixelRatio 2,
and `/analyze` picks an action by visual element id.

---

## Limitations

Being upfront about what this prototype does and doesn't do:

- **The local model is genuinely optional and unverified by default.** With
  nothing installed, every decision comes from the rule-based engine
  (`decide_action_rules()` in `server/main.py`) — reliable, but not "AI
  reasoning." A tiny model (0.5B–1B params) can also just be wrong or slow;
  the fallback logic only catches *malformed* responses, not *bad* ones.
- **Free-text name/address detection is best-effort.** Labelled fields are
  reliable. In free text, the rule layer and the small spaCy model (see
  "Name & address detection (NER)") catch common Indian formats, but they can
  miss unusual names written in lowercase or in a non-Latin script, and can
  sometimes tokenise a product or brand name that looks like a person's name.
- **Checksums confirm shape, not ownership.** Verhoeff (Aadhaar) and Luhn
  (cards) tell a real-looking number from a random one; they cannot tell
  whether it belongs to anyone. Phone numbers have no checksum, so any
  10-digit number starting 6-9 counts as a phone.
- **Redaction overlays reposition on scroll/resize but are a visual aid**,
  not a security boundary — the actual privacy guarantee is that raw values
  never enter the JSON that gets sent, independent of what's drawn on screen.
- **Visual perception is heuristic.** OCR + edge-based boxes work well on
  ordinary forms and pages; borderless/ghost buttons, icons without text and
  very dense UIs are guessed less reliably (lower `confidence`). A scroll in
  the few ms between reading mask rects and capturing could misalign masks;
  the capture is taken immediately after the rects are read.
- The shared token stops other web pages and local apps from driving the
  server, but it is a single static secret, not per-user auth. The server
  binds to `127.0.0.1` only and is not meant to be exposed further.
