#!/usr/bin/env bash
# NEXAIVA "Install Agent" (macOS/Linux/Git Bash) - same steps as setup.ps1.
#   ./setup.sh [--dev] [--show-token] [--check-server] [--skip-verify]
# Idempotent; the token value is never printed unless --show-token.
set -u
cd "$(dirname "$0")"
ROOT="$(pwd)"
DEV=0; SHOW=0; CHECK=0; SKIPV=0
for a in "$@"; do
  case "$a" in
    --dev) DEV=1 ;; --show-token) SHOW=1 ;; --check-server) CHECK=1 ;; --skip-verify) SKIPV=1 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done
c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
step() { echo; c 36 "[$1/7] $2"; }
ok() { c 32 "  OK    $1"; }
warn() { c 33 "  WARN  $1"; }
fail() { c 31 "  FAIL  $1"; exit 1; }

step 1 "Python 3.10-3.12"
PY=""
for cand in python3.11 python3.12 python3.10 python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null; then PY="$cand"; break; fi
done
[ -n "$PY" ] || fail "no Python 3.10-3.12 found"
ok "$PY -> $("$PY" -V 2>&1)"

step 2 "Virtual environment (server/.venv)"
VPY="server/.venv/bin/python"; [ -x "$VPY" ] || VPY="server/.venv/Scripts/python.exe"
if [ -x "$VPY" ] && "$VPY" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' 2>/dev/null; then
  ok "reusing existing venv"
else
  "$PY" -m venv server/.venv || fail "venv creation failed"
  VPY="server/.venv/bin/python"; [ -x "$VPY" ] || VPY="server/.venv/Scripts/python.exe"
  ok "created"
fi

REQ=requirements.txt; [ "$DEV" = 1 ] && REQ=requirements-dev.txt
step 3 "pip install -r server/$REQ"
"$VPY" -m pip install --disable-pip-version-check -q -r "server/$REQ" || fail "pip install failed"
ok "requirements satisfied"

step 4 "spaCy model en_core_web_sm"
if "$VPY" -c "import spacy; spacy.load('en_core_web_sm')" 2>/dev/null; then ok "loads"
else "$VPY" -m spacy download en_core_web_sm && ok "downloaded" || warn "missing - NER runs rules-only"; fi

step 5 "RapidOCR ONNX models"
if "$VPY" -c "from rapidocr_onnxruntime import RapidOCR; RapidOCR()" 2>/dev/null; then ok "engine loads"
else warn "RapidOCR did not load - Visual mode disabled"; fi

step 6 "Shared token (server/.aiva_token)"
if [ -n "${AIVA_SHARED_TOKEN:-}" ]; then ok "AIVA_SHARED_TOKEN set in this shell"
elif [ -s server/.aiva_token ]; then ok "exists (value not shown)"
else
  "$VPY" -c "import secrets,pathlib; pathlib.Path('server/.aiva_token').write_text(secrets.token_urlsafe(32)+'\n', encoding='utf-8')" || fail "could not write token"
  chmod 600 server/.aiva_token 2>/dev/null
  ok "created (value not shown)"
fi
[ "$SHOW" = 1 ] && c 35 "  TOKEN ${AIVA_SHARED_TOKEN:-$(tr -d '\r\n' < server/.aiva_token)}"
[ -n "${AIVA_EXTENSION_ORIGIN:-}" ] || warn 'AIVA_EXTENSION_ORIGIN not set: export AIVA_EXTENSION_ORIGIN="chrome-extension://<id>"'

step 7 "Verify (scripts/verify_setup.py)"
if [ "$SKIPV" = 1 ]; then ok "skipped"
else
  ARGS=(scripts/verify_setup.py); [ "$CHECK" = 1 ] || ARGS+=(--no-server)
  "$VPY" "${ARGS[@]}" || fail "verification reported FAIL lines"
fi
echo; c 32 "Done. Next: server/.venv/bin/python server/main.py, then NEXAIVA/SETUP_GUIDE.md"
