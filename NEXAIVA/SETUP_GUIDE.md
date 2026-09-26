# NEXAIVA / Setup Guide — Agent initialization flow

```
[1] Install Agent  ──►  [2] Setup Guide (this file)  ──►  [3] Basic Details
    setup.ps1               walkthrough + file              verify_setup.py
    CLI + runtime           architecture check              "Config verified"
```
Target: one Windows laptop, CPU only, Chrome, Python 3.10–3.12 (3.11
recommended). After step 1 nothing needs the network.

## Step 1 — Install Agent
From the repo root in PowerShell:
```powershell
.\setup.ps1 -Dev        # -Dev adds pytest/httpx/pillow; omit for runtime only
# blocked by execution policy?  powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Dev
```
It (re)uses `server\.venv`, installs requirements, checks the spaCy model
`en_core_web_sm` and the RapidOCR ONNX models, creates `server\.aiva_token`
if missing (value not printed), and ends with an offline verification.
Re-running is safe. macOS/Linux/Git Bash: `./setup.sh --dev`.

## Step 2 — Walkthrough

1. **Start the server** (new terminal, repo root):
   ```powershell
   server\.venv\Scripts\python server\main.py
   ```
   It listens on `http://127.0.0.1:8000` and prints the token once on this
   local console. Check: open `http://127.0.0.1:8000/health` → `{"status":"ok"}`.
2. **Serve the demo page** (optional, another terminal):
   `cd demo; python -m http.server 5500` → open
   `http://localhost:5500/demo-form.html`.
3. **Load the extension unpacked**: `chrome://extensions` → **Developer
   mode** on → **Load unpacked** → select the `extension\` folder (or
   **Reload** if already loaded).
4. **Copy the extension ID** shown on its card (32 letters a–p).
5. **Set `AIVA_EXTENSION_ORIGIN`** and restart the server (Ctrl+C first):
   ```powershell
   $env:AIVA_EXTENSION_ORIGIN = "chrome-extension://<your-extension-id>"
   server\.venv\Scripts\python server\main.py
   ```
6. **Paste the token**: click the toolbar icon → side panel → **⚙ Settings**
   → paste the token (from the server console, or `.\setup.ps1 -ShowToken`)
   → **Save & Test Connection** → "Connected". See `Vault.md` for handling.
7. **Scan Page** — sensitive fields get a `🔒 TOKEN` overlay; the panel shows
   the count and the sanitized JSON.
8. **👁 Visual** — toggle it on (turns purple) for on-device visual perception.
9. **Send** (**Send Context** / **Send to Server**) — Visual mode shows the
   exact masked JPEG that left the browser, OCR/CV elements and timings; the
   chosen element is outlined in purple.
10. **Execute** — the extension performs the action on the page.

Optional local LLM (else the rule engine decides): `.\scripts\setup-local-llm.ps1`
(Ollama + `llama3.2:1b`), or set `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_MODEL`
for LM Studio. Optional strict NER: `$env:AIVA_NER_POLICY = "reject"`.

### File architecture check
`scripts\verify_setup.py` checks that the layout this guide depends on exists:
- `NEXAIVA\Intro.md`, `Details.md`, `Vault.md`, `SETUP_GUIDE.md`
- `extension\manifest.json` is valid Manifest V3, and every file it
  references (service worker, content scripts, side panel, icons) exists,
  plus every script/style `popup.html` loads
- server modules: `main.py`, `security.py`, `pii_checks.py`, `ner\*`,
  `vision\*`, `requirements*.txt`
- `.gitignore` covers `server/.aiva_token` and `.env*`

## Step 3 — Basic Details (config verified)
With the server running:
```powershell
py -3.11 scripts\verify_setup.py
```
| Check | PASS | WARN | FAIL |
|---|---|---|---|
| File architecture | all present | — | anything missing |
| `server\.venv`, packages | venv + fastapi, uvicorn, spaCy + model, RapidOCR, OpenCV | venv Python outside 3.10–3.12 | missing |
| Shared token | present (length only) | — | missing |
| `AIVA_EXTENSION_ORIGIN` | valid extension origin | unset / odd shape | `*` |
| `AIVA_ALLOW_CLOUD` | off | on | — |
| `/health` | 200 | — | unreachable |
| authenticated `/analyze` | 200 with an action | — | 401 / other |
| `/perceive/health` | OCR available | unavailable | — |
| local LLM | reachable | rule-engine fallback | — |

Last line is **`Config verified`** only when there is no FAIL (exit 0).
Options: `--no-server` (offline), `--token-file PATH` (token of another
checkout or a running server started elsewhere), `--server URL`,
`--skip-packages`. The verifier never prints the token.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Panel says token missing/wrong (401) | Re-paste token in ⚙ Settings; if you rotated it, restart the server |
| `authenticated /analyze` FAIL 401 | The running server uses another token (`AIVA_SHARED_TOKEN` in its shell, or another checkout) — pass `--token-file` |
| CORS error in the service-worker console | `AIVA_EXTENSION_ORIGIN` does not match the ID; fix and restart |
| Visual mode: "No response from page" | Reload the tab (content script missing) — `chrome://` pages can't be captured |
| `/perceive` 413 | Screenshot over 4 MB; raise `AIVA_MAX_PERCEIVE_BYTES` |
| `/ner/scan` shows `rules-only` | `server\.venv\Scripts\python -m spacy download en_core_web_sm` |
