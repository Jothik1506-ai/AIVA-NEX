# NEXAIVA / Vault — secrets, keys & auth (high security)

> **SECURITY RULE: this file never contains a secret value.** It lists each
> secret or sensitive setting by NAME only: what it is for, where the real
> value lives, how to create or rotate it, and who needs it. If you are about
> to paste a value in here, stop. Any commit that adds one must be reverted
> and the secret rotated.

## Rules for AI agents and contributors
1. **Never open, `cat`, `Get-Content`, print, log, echo or summarise**
   `server/.aiva_token`, any `.env` / `.env.*` file, or the value of
   `AIVA_SHARED_TOKEN` / `GEMINI_API_KEY`. To check a token exists, test the
   file size or run `scripts/verify_setup.py` (it prints presence and length
   only).
2. **Never commit them.** `.gitignore` covers `server/.aiva_token`,
   `**/.aiva_token`, `.env`, `.env*` (except `.env.example`), `*.local.ps1`,
   `*.local.json`. Before committing run `git status --short` and make sure
   none of these appear; never use `git add -f` on them.
3. **Never paste a secret into chat, a PR, an issue, a test, a doc, a
   screenshot or a log.** Tests use the fixed dummy `test-token-not-a-secret`
   (`server/tests/conftest.py`) — keep it obviously fake.
4. **To authenticate a script, pass a path, not a value**:
   `scripts/verify_setup.py --token-file <path>` reads the file itself.
5. If a secret was exposed: rotate it (below) *first*, then clean up.
6. `setup.ps1 -ShowToken` is the only sanctioned way to display the token,
   for a human pasting it into the extension. Agents must not use it.

## Inventory

| Name | Kind | Purpose | Real value lives in | Who needs it |
|---|---|---|---|---|
| Shared server token | secret | Authenticates the extension to the local server: header `X-Aiva-Token`, required on every route except `/health` and the docs pages (`server/security.py`) | `server/.aiva_token` (git-ignored, created with `secrets.token_urlsafe(32)`, chmod 600 where supported) **or** env `AIVA_SHARED_TOKEN` (wins if set) | The server process; the extension (⚙ Settings → `chrome.storage.local.aivaServerToken`); `verify_setup.py` via `--token-file` |
| `GEMINI_API_KEY` | secret (third-party) | Optional Google Gemini fallback for `/chat`; ignored unless `AIVA_ALLOW_CLOUD=1` | Process env only (set in the shell or a git-ignored `.env`); never in code | Only whoever explicitly opts in to cloud mode |
| `AIVA_ALLOW_CLOUD` | sensitive switch | `1` lets sanitized context leave the machine (Gemini, remote `LOCAL_LLM_BASE_URL`) | Process env | Demo operator; keep `0` for the privacy demo |
| `AIVA_EXTENSION_ORIGIN` | sensitive config (not secret) | CORS allow-list: `chrome-extension://<id>`; `*` is refused | Process env | Server operator |
| `LOCAL_LLM_BASE_URL` | sensitive config | Where page context is sent for decisions; must be loopback unless cloud is allowed | Process env | Server operator |
| `aivaServerToken` | secret copy | The token as stored by the extension | `chrome.storage.local` in the user's Chrome profile | Extension only |
| `aivaMemory` | personal data | On-device profile/memory facts for autofill (`extension/memory.js`) | `chrome.storage.local`; never sent to the server | Extension only |
| Feedback endpoint | public URL (not secret) | Opt-in feedback inbox (`background.js` `FEEDBACK_URL`) | Source code | Sent only after `aivaFeedbackOptIn` is ticked |

Not secrets (safe in code): server host/port `127.0.0.1:8000`, the Aadhaar
UIDAI sandbox test number used in docs/tests, the dummy test token.

## Create / rotate

**Shared token**
1. Stop the server.
2. Delete `server/.aiva_token` (or set a new `AIVA_SHARED_TOKEN`).
3. Run `.\setup.ps1` (creates a new file without printing it) or start the
   server (creates one and prints it once on the local console).
4. In the side panel: ⚙ Settings → paste the new token → **Save & Test
   Connection** → "Connected".
5. `py -3.11 scripts\verify_setup.py` → `authenticated /analyze` PASS.
Rotate whenever it may have been seen by anyone else, and before a public demo.

**GEMINI_API_KEY**: create/revoke in Google AI Studio; set only in the shell
that runs the server (`$env:GEMINI_API_KEY = ...` typed by the human) or a
git-ignored `.env`. Revoke there if exposed. Unset it when not demoing cloud.

## Where secrets flow (and nowhere else)
```
server/.aiva_token ─► server process (compare, constant time) 
         └─ human copies once ─► extension Settings ─► chrome.storage.local
                                   └─ background.js serverFetch() ─► X-Aiva-Token ─► 127.0.0.1:8000
GEMINI_API_KEY (env) ─► server ─► x-goog-api-key header ─► Google (only if AIVA_ALLOW_CLOUD=1)
```
The server never logs request bodies, headers or token values; `401`
responses do not reveal the expected token.
