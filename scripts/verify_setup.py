#!/usr/bin/env python3
"""
NEXAIVA "Basic Details" step: verify an Aiva Nex Agent install.

Prints PASS / WARN / FAIL for:
  1. file architecture   - NEXAIVA docs, extension manifest + every script it
                           and popup.html reference, server modules, .gitignore
  2. environment         - server/.venv, key packages importable in that venv
                           (fastapi, uvicorn, spaCy en_core_web_sm, rapidocr, cv2)
  3. secrets / config    - token present (value is NEVER printed),
                           AIVA_EXTENSION_ORIGIN, AIVA_ALLOW_CLOUD
  4. live server         - GET /health, authenticated POST /analyze == 200,
                           /perceive/health, local LLM reachable or rule-engine fallback

Ends with "Config verified" only when there is no FAIL (exit code 0), else exit 1.

Stdlib only (urllib, json, subprocess) - runs with any Python 3.8+ before or
after the venv exists:

    py -3.11 scripts/verify_setup.py                     # full check (server must be running)
    py -3.11 scripts/verify_setup.py --no-server         # offline: skip the live-server checks
    py -3.11 scripts/verify_setup.py --token-file PATH   # use another checkout's token file
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER = "http://127.0.0.1:8000"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

NEXAIVA_DOCS = ["NEXAIVA/Intro.md", "NEXAIVA/Details.md", "NEXAIVA/Vault.md", "NEXAIVA/SETUP_GUIDE.md"]
SERVER_MODULES = [
    "server/main.py",
    "server/security.py",
    "server/pii_checks.py",
    "server/requirements.txt",
    "server/requirements-dev.txt",
    "server/ner/__init__.py",
    "server/ner/api.py",
    "server/ner/detector.py",
    "server/ner/model.py",
    "server/ner/rules.py",
    "server/vision/__init__.py",
    "server/vision/routes.py",
    "server/vision/pipeline.py",
    "server/vision/ocr.py",
]
# Patterns .gitignore must contain so secrets never get committed.
GITIGNORE_REQUIRED = ["server/.aiva_token", ".env"]

# Imported inside the venv (subprocess) - module name -> human label.
PACKAGES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "spacy": "spaCy",
    "en_core_web_sm": "spaCy model en_core_web_sm",
    "rapidocr_onnxruntime": "RapidOCR (ONNX)",
    "cv2": "OpenCV",
}

Result = Tuple[str, str, str]  # (status, check name, detail)


# --------------------------------------------------------------------------
# 1. File architecture
# --------------------------------------------------------------------------

def _exists(root: Path, rel: str) -> bool:
    return (root / rel).is_file()


def check_file_architecture(root: Path) -> List[Result]:
    root = Path(root)
    out: List[Result] = []

    missing = [d for d in NEXAIVA_DOCS if not _exists(root, d)]
    out.append((FAIL, "NEXAIVA docs", "missing: " + ", ".join(missing)) if missing
               else (PASS, "NEXAIVA docs", f"{len(NEXAIVA_DOCS)} files present"))

    out.extend(check_extension(root))

    missing = [m for m in SERVER_MODULES if not _exists(root, m)]
    out.append((FAIL, "server modules", "missing: " + ", ".join(missing)) if missing
               else (PASS, "server modules", f"{len(SERVER_MODULES)} files present"))

    out.append(check_gitignore(root))
    return out


def manifest_files(manifest: dict) -> List[str]:
    """Every extension-relative file a Manifest V3 file points at."""
    files: List[str] = []
    bg = (manifest.get("background") or {}).get("service_worker")
    if bg:
        files.append(bg)
    for cs in manifest.get("content_scripts") or []:
        files.extend(cs.get("js") or [])
        files.extend(cs.get("css") or [])
    panel = (manifest.get("side_panel") or {}).get("default_path")
    if panel:
        files.append(panel)
    popup = (manifest.get("action") or {}).get("default_popup")
    if popup:
        files.append(popup)
    for icons in (manifest.get("icons") or {}, (manifest.get("action") or {}).get("default_icon") or {}):
        if isinstance(icons, dict):
            files.extend(icons.values())
    seen, ordered = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def html_scripts(html: str) -> List[str]:
    """Local <script src> / <link href> files referenced by an HTML page."""
    refs = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
    refs += re.findall(r"<link[^>]+href=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
    return [r for r in refs if not re.match(r"^[a-z]+:", r, re.IGNORECASE)]


def check_extension(root: Path) -> List[Result]:
    ext = Path(root) / "extension"
    mpath = ext / "manifest.json"
    if not mpath.is_file():
        return [(FAIL, "extension manifest", "extension/manifest.json not found")]
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [(FAIL, "extension manifest", f"not valid JSON ({type(exc).__name__})")]
    out: List[Result] = []
    mv = manifest.get("manifest_version")
    out.append((PASS, "extension manifest", f"Manifest V{mv}, '{manifest.get('name', '?')}'") if mv == 3
               else (FAIL, "extension manifest", f"manifest_version is {mv!r}, expected 3"))

    files = manifest_files(manifest)
    missing = [f for f in files if not (ext / f).is_file()]
    out.append((FAIL, "manifest files", "missing: " + ", ".join(missing)) if missing
               else (PASS, "manifest files", f"{len(files)} referenced files present"))

    panel = (manifest.get("side_panel") or {}).get("default_path")
    if panel and (ext / panel).is_file():
        refs = html_scripts((ext / panel).read_text(encoding="utf-8", errors="replace"))
        missing = [r for r in refs if not (ext / r).is_file()]
        out.append((FAIL, f"{panel} scripts", "missing: " + ", ".join(missing)) if missing
                   else (PASS, f"{panel} scripts", f"{len(refs)} referenced files present"))
    return out


def check_gitignore(root: Path) -> Result:
    gi = Path(root) / ".gitignore"
    if not gi.is_file():
        return (FAIL, ".gitignore secrets", ".gitignore missing")
    lines = {ln.strip() for ln in gi.read_text(encoding="utf-8").splitlines()}
    covered = {
        "server/.aiva_token": bool(lines & {"server/.aiva_token", ".aiva_token", "**/.aiva_token"}),
        ".env": bool(lines & {".env", ".env*", "**/.env", "**/.env*"}),
    }
    missing = [k for k in GITIGNORE_REQUIRED if not covered[k]]
    return ((FAIL, ".gitignore secrets", "not ignored: " + ", ".join(missing)) if missing
            else (PASS, ".gitignore secrets", "server/.aiva_token and .env* are ignored"))


# --------------------------------------------------------------------------
# 2. Environment
# --------------------------------------------------------------------------

def venv_python(root: Path) -> Optional[Path]:
    for rel in ("server/.venv/Scripts/python.exe", "server/.venv/bin/python"):
        p = Path(root) / rel
        if p.is_file():
            return p
    return None


def check_venv(root: Path) -> Result:
    py = venv_python(root)
    return (PASS, "server/.venv", str(py.relative_to(root))) if py else (
        FAIL, "server/.venv", "not found - run setup.ps1 (or setup.sh)")


_PROBE = (
    "import importlib.util, json, sys\n"
    "mods = json.loads(sys.argv[1])\n"
    "print(json.dumps({'version': '%d.%d.%d' % sys.version_info[:3],"
    " 'found': {m: importlib.util.find_spec(m) is not None for m in mods}}))\n"
)


def probe_packages(py: Path, runner: Callable = subprocess.run) -> Optional[Dict]:
    try:
        proc = runner([str(py), "-c", _PROBE, json.dumps(list(PACKAGES))],
                      capture_output=True, text=True, timeout=60)
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001 - any failure means "could not probe"
        return None


def check_packages(root: Path, runner: Callable = subprocess.run) -> List[Result]:
    py = venv_python(root)
    if not py:
        return [(SKIP, "packages", "no venv")]
    info = probe_packages(py, runner)
    if not info:
        return [(FAIL, "packages", "could not run the venv interpreter")]
    out: List[Result] = []
    ver = tuple(int(x) for x in info["version"].split(".")[:2])
    out.append((PASS if (3, 10) <= ver <= (3, 12) else WARN, "venv Python", info["version"] +
                ("" if (3, 10) <= ver <= (3, 12) else " (supported: 3.10-3.12)")))
    missing = [label for mod, label in PACKAGES.items() if not info["found"].get(mod)]
    out.append((FAIL, "packages", "not importable: " + ", ".join(missing)) if missing
               else (PASS, "packages", ", ".join(PACKAGES.values())))
    return out


# --------------------------------------------------------------------------
# 3. Secrets / config  (values are never printed)
# --------------------------------------------------------------------------

def resolve_token(token_file: Optional[Path], env: Dict[str, str]) -> Tuple[Optional[str], str]:
    """(token or None, where it came from). An explicit --token-file wins,
    then AIVA_SHARED_TOKEN (the server prefers it too), then the default file."""
    if token_file is not None:
        p = Path(token_file)
        if p.is_file():
            tok = p.read_text(encoding="utf-8").strip()
            if tok:
                return tok, f"file {p}"
        return None, f"file {p}"
    if env.get("AIVA_SHARED_TOKEN", "").strip():
        return env["AIVA_SHARED_TOKEN"].strip(), "env AIVA_SHARED_TOKEN"
    return None, "none"


def check_token(token: Optional[str], source: str) -> Result:
    if token:
        return (PASS, "shared token", f"present ({source}, {len(token)} chars; value not shown)")
    return (FAIL, "shared token", f"missing ({source}) - run setup.ps1 or start the server once")


def check_origin(env: Dict[str, str]) -> Result:
    raw = env.get("AIVA_EXTENSION_ORIGIN", "").strip()
    if not raw:
        return (WARN, "AIVA_EXTENSION_ORIGIN", "not set - CORS admits only localhost pages; "
                "set chrome-extension://<id> (see SETUP_GUIDE step 4)")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        return (FAIL, "AIVA_EXTENSION_ORIGIN", "'*' is refused by the server")
    bad = [o for o in origins if not re.match(r"^chrome-extension://[a-p]{32}/?$", o)]
    if bad:
        return (WARN, "AIVA_EXTENSION_ORIGIN", f"{len(bad)} origin(s) not shaped like chrome-extension://<32-char id>")
    return (PASS, "AIVA_EXTENSION_ORIGIN", f"{len(origins)} extension origin(s)")


def check_cloud(env: Dict[str, str]) -> Result:
    on = env.get("AIVA_ALLOW_CLOUD", "0").strip().lower() in ("1", "true", "yes")
    if on:
        return (WARN, "AIVA_ALLOW_CLOUD", "=1 in this shell: sanitized context MAY leave the machine")
    return (PASS, "AIVA_ALLOW_CLOUD", "off (fully local)")


# --------------------------------------------------------------------------
# 4. Live server (urllib only)
# --------------------------------------------------------------------------

def http(method: str, url: str, headers: Optional[Dict[str, str]] = None,
         body: Optional[dict] = None, timeout: float = 5.0) -> Tuple[int, Optional[dict]]:
    """(status, parsed JSON or None). status 0 = unreachable."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except ValueError:
        return status, None


# A tiny, already-sanitized screen graph (no PII) - exercises auth + the
# PII re-check + NER policy + decision path end to end.
PROBE_GRAPH = {
    "pageTitle": "verify_setup probe",
    "domain": "localhost",
    "inputs": [{"ref": "input-0", "type": "text", "label": "Search", "sanitizedValue": ""}],
    "buttons": [{"ref": "button-0", "text": "Continue"}],
    "sensitiveItemsCount": 0,
}


def check_server(base: str, token: Optional[str], request: Callable = http) -> List[Result]:
    base = base.rstrip("/")
    out: List[Result] = []
    status, body = request("GET", base + "/health")
    if status != 200 or not body or body.get("status") != "ok":
        detail = "unreachable" if status == 0 else f"HTTP {status}"
        out.append((FAIL, "server /health", f"{detail} at {base} - start: server\\.venv\\Scripts\\python server\\main.py"))
        out.append((SKIP, "authenticated /analyze", "server not reachable"))
        out.append((SKIP, "local LLM", "server not reachable"))
        return out
    out.append((PASS, "server /health", f"{base} ok"))

    if not token:
        out.append((FAIL, "authenticated /analyze", "no token to send"))
        return out
    auth = {"X-Aiva-Token": token}
    status, body = request("POST", base + "/analyze", auth, PROBE_GRAPH, 45.0)
    if status == 200 and body and body.get("action"):
        out.append((PASS, "authenticated /analyze", f"200, action={body.get('action')}, decidedBy={body.get('decidedBy')}"))
    elif status == 401:
        out.append((FAIL, "authenticated /analyze", "401 - token does not match the running server (use --token-file)"))
    else:
        out.append((FAIL, "authenticated /analyze", f"HTTP {status}"))

    status, body = request("GET", base + "/perceive/health", auth)
    if status == 200 and body and body.get("available"):
        out.append((PASS, "vision /perceive", "OCR engine available"))
    elif status == 404:
        out.append((WARN, "vision /perceive", "route missing - vision deps not installed, DOM-only mode"))
    else:
        out.append((WARN, "vision /perceive", f"not available (HTTP {status})"))

    status, body = request("GET", base + "/health/llm", auth)
    if status == 200 and body and body.get("reachable"):
        out.append((PASS, "local LLM", f"reachable, model {body.get('model')}"))
    else:
        out.append((WARN, "local LLM", "not reachable - /analyze uses the rule-engine fallback "
                    "(optional: scripts/setup-local-llm.ps1)"))
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

_COLOURS = {PASS: "32", WARN: "33", FAIL: "31", SKIP: "90"}


def _use_colour(stream) -> bool:
    if os.getenv("NO_COLOR") or not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        os.system("")  # enables ANSI escape processing on Windows 10+ consoles
    return True


def render(results: List[Result], colour: bool = False) -> str:
    lines = []
    for status, name, detail in results:
        tag = f"[{status}]"
        if colour:
            tag = f"\x1b[{_COLOURS[status]}m{tag}\x1b[0m"
        lines.append(f"{tag} {name}: {detail}")
    counts = {s: sum(1 for r in results if r[0] == s) for s in (PASS, WARN, FAIL, SKIP)}
    lines.append("")
    lines.append(f"Summary: {counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail, {counts[SKIP]} skipped")
    lines.append("Config verified" if counts[FAIL] == 0 else "Config NOT verified - fix the FAIL lines above")
    return "\n".join(lines)


def run_checks(root: Path, server: Optional[str], token_file: Optional[Path], env: Dict[str, str],
               check_packages_flag: bool = True, request: Callable = http,
               runner: Callable = subprocess.run) -> List[Result]:
    root = Path(root)
    results = check_file_architecture(root)
    results.append(check_venv(root))
    if check_packages_flag:
        results.extend(check_packages(root, runner))
    if token_file is None and not env.get("AIVA_SHARED_TOKEN", "").strip():
        token_file = root / "server" / ".aiva_token"
    token, source = resolve_token(token_file, env)
    results.append(check_token(token, source))
    results.append(check_origin(env))
    results.append(check_cloud(env))
    if server:
        results.extend(check_server(server, token, request))
    else:
        results.append((SKIP, "live server", "--no-server"))
    return results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Verify an Aiva Nex Agent install (NEXAIVA 'Basic Details').")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this checkout)")
    ap.add_argument("--server", default=DEFAULT_SERVER, help=f"server base URL (default {DEFAULT_SERVER})")
    ap.add_argument("--no-server", action="store_true", help="skip the live-server checks")
    ap.add_argument("--token-file", type=Path, default=None,
                    help="token file to authenticate with (default: AIVA_SHARED_TOKEN, else <root>/server/.aiva_token)")
    ap.add_argument("--skip-packages", action="store_true", help="do not probe packages inside the venv")
    args = ap.parse_args(argv)

    results = run_checks(args.root, None if args.no_server else args.server, args.token_file,
                         dict(os.environ), check_packages_flag=not args.skip_packages)
    print("Aiva Nex Agent - setup verification (" + str(Path(args.root).resolve()) + ")\n")
    print(render(results, colour=_use_colour(sys.stdout)))
    return 1 if any(r[0] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
