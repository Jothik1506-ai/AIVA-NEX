"""Tests for scripts/verify_setup.py (NEXAIVA 'Basic Details' verifier).

No live server and no real venv needed: the file tree is built in tmp_path,
network calls and the venv probe are replaced by fakes.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import verify_setup as vs  # noqa: E402

FAKE_TOKEN = "fake-token-for-tests-0123456789"
EXT_ID = "abcdefghijklmnopabcdefghijklmnop"

MANIFEST = {
    "manifest_version": 3,
    "name": "Aiva Nex Agent",
    "icons": {"16": "icons/icon16.png"},
    "action": {"default_icon": {"16": "icons/icon16.png"}},
    "side_panel": {"default_path": "popup.html"},
    "background": {"service_worker": "background.js"},
    "content_scripts": [{"matches": ["<all_urls>"], "js": ["pii-checks.js", "content.js"]}],
}
POPUP = '<link rel="stylesheet" href="popup.css"><script src="popup.js"></script><script src="https://cdn.example/x.js"></script>'


def _touch(root: Path, rel: str, text: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def tree(tmp_path):
    for rel in vs.NEXAIVA_DOCS + vs.SERVER_MODULES:
        _touch(tmp_path, rel)
    _touch(tmp_path, "extension/manifest.json", json.dumps(MANIFEST))
    for rel in ("background.js", "pii-checks.js", "content.js", "popup.js", "popup.css", "icons/icon16.png"):
        _touch(tmp_path, "extension/" + rel)
    _touch(tmp_path, "extension/popup.html", POPUP)
    _touch(tmp_path, ".gitignore", ".venv/\nserver/.aiva_token\n.env*\n")
    return tmp_path


def _by_name(results):
    return {name: (status, detail) for status, name, detail in results}


# --- file architecture -----------------------------------------------------

def test_complete_tree_passes(tree):
    results = vs.check_file_architecture(tree)
    assert all(s == vs.PASS for s, _, _ in results), results
    names = _by_name(results)
    assert names["manifest files"][1].startswith("5 referenced")  # sw, 2 content js, panel, icon (deduped)
    assert names["popup.html scripts"][1].startswith("2 referenced")  # remote URL ignored


def test_missing_nexaiva_doc_fails(tree):
    (tree / "NEXAIVA" / "Vault.md").unlink()
    status, detail = _by_name(vs.check_file_architecture(tree))["NEXAIVA docs"]
    assert status == vs.FAIL and "NEXAIVA/Vault.md" in detail


def test_missing_manifest_script_fails(tree):
    (tree / "extension" / "content.js").unlink()
    status, detail = _by_name(vs.check_file_architecture(tree))["manifest files"]
    assert status == vs.FAIL and "content.js" in detail


def test_missing_popup_script_fails(tree):
    (tree / "extension" / "popup.js").unlink()
    status, detail = _by_name(vs.check_file_architecture(tree))["popup.html scripts"]
    assert status == vs.FAIL and "popup.js" in detail


def test_invalid_manifest_and_wrong_version(tree):
    (tree / "extension" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert _by_name(vs.check_extension(tree))["extension manifest"][0] == vs.FAIL
    (tree / "extension" / "manifest.json").write_text(json.dumps({**MANIFEST, "manifest_version": 2}), encoding="utf-8")
    assert _by_name(vs.check_extension(tree))["extension manifest"][0] == vs.FAIL


def test_missing_server_module_fails(tree):
    (tree / "server" / "vision" / "ocr.py").unlink()
    status, detail = _by_name(vs.check_file_architecture(tree))["server modules"]
    assert status == vs.FAIL and "server/vision/ocr.py" in detail


def test_gitignore_must_cover_token_and_env(tree):
    (tree / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    status, _, detail = vs.check_gitignore(tree)
    assert status == vs.FAIL and "server/.aiva_token" in detail and ".env" in detail


def test_real_repo_file_architecture_passes():
    """The committed repo itself must satisfy the file-architecture check."""
    results = vs.check_file_architecture(REPO_ROOT)
    assert [r for r in results if r[0] == vs.FAIL] == []


def test_manifest_files_matches_real_manifest():
    manifest = json.loads((REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    files = vs.manifest_files(manifest)
    assert "background.js" in files and "vision-content.js" in files and "popup.html" in files


# --- config / secrets ------------------------------------------------------

def test_token_from_file_never_printed(tree):
    _touch(tree, "server/.aiva_token", FAKE_TOKEN + "\n")
    token, source = vs.resolve_token(tree / "server" / ".aiva_token", {})
    assert token == FAKE_TOKEN
    result = vs.check_token(token, source)
    assert result[0] == vs.PASS and FAKE_TOKEN not in result[2]


def test_token_file_beats_env_and_missing_token_fails(tree):
    _touch(tree, "server/.aiva_token", FAKE_TOKEN)
    token, _ = vs.resolve_token(tree / "server" / ".aiva_token", {"AIVA_SHARED_TOKEN": "env-token"})
    assert token == FAKE_TOKEN
    token, source = vs.resolve_token(tree / "nope", {})
    assert token is None and vs.check_token(token, source)[0] == vs.FAIL


def test_origin_checks():
    assert vs.check_origin({})[0] == vs.WARN
    assert vs.check_origin({"AIVA_EXTENSION_ORIGIN": "*"})[0] == vs.FAIL
    assert vs.check_origin({"AIVA_EXTENSION_ORIGIN": "http://evil.example"})[0] == vs.WARN
    assert vs.check_origin({"AIVA_EXTENSION_ORIGIN": f"chrome-extension://{EXT_ID}"})[0] == vs.PASS


def test_cloud_flag():
    assert vs.check_cloud({})[0] == vs.PASS
    assert vs.check_cloud({"AIVA_ALLOW_CLOUD": "1"})[0] == vs.WARN


# --- venv / packages (probe faked) -----------------------------------------

def test_packages_probe(tree):
    _touch(tree, "server/.venv/Scripts/python.exe")

    def runner(found, version="3.11.9"):
        payload = json.dumps({"version": version, "found": found})
        return lambda *a, **k: SimpleNamespace(stdout=payload + "\n", returncode=0)

    ok = runner({m: True for m in vs.PACKAGES})
    assert [r[0] for r in vs.check_packages(tree, ok)] == [vs.PASS, vs.PASS]
    no_model = runner({**{m: True for m in vs.PACKAGES}, "en_core_web_sm": False})
    status, _, detail = vs.check_packages(tree, no_model)[1]
    assert status == vs.FAIL and "en_core_web_sm" in detail
    old = runner({m: True for m in vs.PACKAGES}, "3.9.1")
    assert vs.check_packages(tree, old)[0][0] == vs.WARN


def test_no_venv(tree):
    assert vs.check_venv(tree)[0] == vs.FAIL
    assert vs.check_packages(tree)[0][0] == vs.SKIP


# --- live server (network faked) -------------------------------------------

def fake_server(analyze_status=200, llm=False, down=False, vision=True):
    calls = []

    def request(method, url, headers=None, body=None, timeout=5.0):
        calls.append((method, url, dict(headers or {})))
        if down:
            return 0, None
        if urlparse(url).path == "/health":
            return 200, {"status": "ok"}
        if (headers or {}).get("X-Aiva-Token") != FAKE_TOKEN:
            return 401, {"detail": "Missing or wrong X-Aiva-Token."}
        if url.endswith("/analyze"):
            if analyze_status != 200:
                return analyze_status, {"detail": "x"}
            return 200, {"action": "click", "targetRef": "button-0", "decidedBy": "rule-engine"}
        if url.endswith("/perceive/health"):
            return (200, {"available": True}) if vision else (404, None)
        if url.endswith("/health/llm"):
            return 200, {"reachable": llm, "model": "llama3.2:1b"}
        return 404, None

    request.calls = calls
    return request


def test_server_all_good_with_rule_engine_fallback():
    req = fake_server()
    names = _by_name(vs.check_server("http://127.0.0.1:8000/", FAKE_TOKEN, req))
    assert names["server /health"][0] == vs.PASS
    assert names["authenticated /analyze"][0] == vs.PASS
    assert names["vision /perceive"][0] == vs.PASS
    assert names["local LLM"][0] == vs.WARN and "rule-engine" in names["local LLM"][1]
    # /health is called without the token, everything else with it
    assert req.calls[0][2] == {}
    assert all(c[2].get("X-Aiva-Token") == FAKE_TOKEN for c in req.calls[1:])


def test_server_wrong_token_fails():
    names = _by_name(vs.check_server("http://127.0.0.1:8000", "wrong", fake_server()))
    assert names["authenticated /analyze"][0] == vs.FAIL and "401" in names["authenticated /analyze"][1]


def test_server_down_fails_and_skips_rest():
    results = vs.check_server("http://127.0.0.1:8000", FAKE_TOKEN, fake_server(down=True))
    assert [r[0] for r in results] == [vs.FAIL, vs.SKIP, vs.SKIP]


def test_llm_reachable_passes():
    names = _by_name(vs.check_server("http://x", FAKE_TOKEN, fake_server(llm=True)))
    assert names["local LLM"][0] == vs.PASS


# --- end to end: run_checks + render ---------------------------------------

def _ok_runner(*a, **k):
    return SimpleNamespace(stdout=json.dumps({"version": "3.11.9", "found": {m: True for m in vs.PACKAGES}}))


def test_run_checks_config_verified_and_token_hidden(tree):
    _touch(tree, "server/.venv/Scripts/python.exe")
    _touch(tree, "server/.aiva_token", FAKE_TOKEN)
    env = {"AIVA_EXTENSION_ORIGIN": f"chrome-extension://{EXT_ID}"}
    results = vs.run_checks(tree, "http://127.0.0.1:8000", None, env, request=fake_server(), runner=_ok_runner)
    text = vs.render(results)
    assert "[FAIL]" not in text
    assert text.rstrip().endswith("Config verified")
    assert FAKE_TOKEN not in text


def test_run_checks_not_verified_on_fail(tree):
    (tree / "extension" / "background.js").unlink()
    results = vs.run_checks(tree, None, None, {}, check_packages_flag=False)
    text = vs.render(results)
    assert "Config NOT verified" in text
    assert "[SKIP] live server" in text


def test_main_exit_codes(tree, capsys):
    _touch(tree, "server/.aiva_token", FAKE_TOKEN)
    assert vs.main(["--root", str(tree), "--no-server", "--skip-packages"]) == 1  # no venv -> FAIL
    _touch(tree, "server/.venv/Scripts/python.exe")
    assert vs.main(["--root", str(tree), "--no-server", "--skip-packages"]) == 0
    out = capsys.readouterr().out
    assert "Config verified" in out and FAKE_TOKEN not in out
