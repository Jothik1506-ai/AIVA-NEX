import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

# Must be set before main.py is imported: fixed token (no file written),
# a fake extension origin, and no cloud or real LLM calls.
TEST_TOKEN = "test-token-not-a-secret"
TEST_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
os.environ["AIVA_SHARED_TOKEN"] = TEST_TOKEN
os.environ["AIVA_EXTENSION_ORIGIN"] = TEST_ORIGIN
os.environ["AIVA_ALLOW_CLOUD"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
os.environ["LOCAL_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"  # discard port: always unreachable
os.environ.setdefault("AIVA_NER_WARM", "0")  # tests load the NER model synchronously on first use


@pytest.fixture()
def main_module(monkeypatch):
    import main

    # Never hit a network in tests - force the rule-engine / fallback paths.
    monkeypatch.setattr(main, "call_local_llm", lambda graph, model=None: None)
    monkeypatch.setattr(main, "call_local_llm_chat", lambda q, g, model=None: None)
    return main


@pytest.fixture()
def client(main_module):
    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


@pytest.fixture()
def auth():
    return {"X-Aiva-Token": TEST_TOKEN}
