# NEXAIVA / Intro — Aiva Nex Agent (read this first)

> Low-token overview. Open `Details.md` only when a routing hint below says so.
> Secrets: `Vault.md` (names and locations only, never values).

## What it is
Chrome MV3 extension + local FastAPI server. A privacy-preserving browser
agent for **SIH 2026, problem SIH26171 (ISRO): on-device visual perception
for light-weight browser agents**. It reads a page (DOM and, optionally,
pixels), removes sensitive data **on the device**, then picks one action
(click / focus / scroll / summarize) and runs it in the page.

## Problem → answer
| Need | Answer |
|---|---|
| Agent must "see" the screen, CPU only | Masked screenshot → RapidOCR (ONNX) + OpenCV on the local server, ~0.4–0.9 s per form (≤~2.2 s dense page) |
| No PII may leave the browser | 3 privacy layers: regex+checksums, NER, black-box masking of pixels |
| Light-weight | No GPU, no PyTorch, no build step; ~12 MB spaCy model + ~15 MB OCR models |
| Works offline | LLM only on localhost (Ollama / LM Studio); rule engine if none |

## 30-second architecture
```
page ──content.js──► detect + tokenise PII (pii-checks.js, ner-rules.js)
                     → sanitized screen graph (JSON)
side panel (popup.js / vision.js)
   ├─ DOM mode:    graph ───────────────────────────► POST /analyze
   └─ Visual mode: screenshot → black boxes over sensitive rects
                   (OffscreenCanvas) → masked JPEG ─► POST /perceive
background.js serverFetch(): adds X-Aiva-Token, talks ONLY to 127.0.0.1:8000
server/main.py: token + CORS + body caps → PII re-check (400 on validated PII)
                → NER tokenise|reject → local LLM or rule engine → action JSON
content.js / vision-content.js execute the action (purple outline in Visual mode)
```
Defaults: nothing leaves the machine. Cloud (Gemini) only with
`AIVA_ALLOW_CLOUD=1` + a key. Feedback to an external inbox only after opt-in.

## Privacy layers (one line each)
1. **Regex + checksums** — email, phone, Aadhaar (Verhoeff), card (Luhn),
   PAN (strict). Extension tokenises every candidate; server rejects
   *validated* leftovers with 400. Same logic in JS and Python.
2. **NER** — names/addresses. Browser: Indian rule layer. Server: spaCy
   `en_core_web_sm` + rules. `AIVA_NER_POLICY=tokenize|reject|off`.
3. **Vision masking** — sensitive rects blacked out before the JPEG is
   encoded; server re-masks PII/NER in OCR text; image never stored.

## Run it (details: `SETUP_GUIDE.md`)
```powershell
.\setup.ps1 -Dev                              # Install Agent: venv, deps, models, token, verify
$env:AIVA_EXTENSION_ORIGIN = "chrome-extension://<id>"
server\.venv\Scripts\python server\main.py    # http://127.0.0.1:8000
py -3.11 scripts\verify_setup.py              # Basic Details: PASS/WARN/FAIL + "Config verified"
```
Load `extension/` unpacked → ⚙ Settings → paste token → Scan Page →
👁 Visual → Send → Execute.

## Tests
```powershell
node --test "extension/tests/*.test.js"                  # 19 node:test
cd server; .venv\Scripts\python -m pytest -q             # pytest (110 core + verifier tests)
```

## Folder map
```
NEXAIVA/        Intro.md (this) · Details.md · Vault.md · SETUP_GUIDE.md
setup.ps1/.sh   Install Agent (idempotent)
scripts/        verify_setup.py (config verifier) · setup-local-llm.ps1/.sh (Ollama)
extension/      manifest.json · background.js (serverFetch) · content.js (scan/tokenise/execute)
                pii-checks.js · ner-rules.js · memory.js · vision-content.js (rects only)
                popup.html/js/css · settings.js (token, opt-in) · vision.js (capture+mask)
                tests/ (node:test)
server/         main.py (routes, decision) · security.py (token, CORS, caps)
                pii_checks.py · ner/ (spaCy + rules) · vision/ (OCR, CV, fusion)
                tests/ (pytest) · requirements*.txt · .aiva_token (git-ignored secret)
demo/           demo-form.html (serve: python -m http.server 5500)
docs/           MEMORY-ARCHITECTURE-PLAN.md (on-device memory store design)
```

## Read `Details.md` only if you need…
| You need | Section in Details.md |
|---|---|
| Script load order, message types, how the token is attached | §1 Extension internals |
| Where each privacy layer runs, JS↔Python parity rule | §2 Privacy layers |
| Route list, auth, size caps, request/response shapes | §3 Server routes |
| OCR/CV pipeline, element types, latency numbers | §4 Vision pipeline |
| Name/address rules, scores, policy, model choice | §5 NER design |
| Every env var and its default | §6 Config reference |
| What the test suites cover, how to run subsets | §7 Testing |
| Honest limits for judges/reviewers | §8 Known limitations |
| Which secrets exist and where they live | `Vault.md` (not Details) |

## Rules for agents working here
- Never open, print or commit `server/.aiva_token` or any `.env*` (see `Vault.md`).
- Change PII logic in **both** `extension/pii-checks.js` and `server/pii_checks.py`; run both suites.
- Keep `_build_page_context()` in `server/main.py` an explicit allow-list.
- Work on a branch/worktree; the main checkout may be serving a live demo.
