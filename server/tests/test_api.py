import logging

from conftest import TEST_ORIGIN, TEST_TOKEN

SANITIZED_GRAPH = {
    "pageTitle": "Checkout",
    "domain": "localhost",
    "scannedAt": "2026-09-26T10:11:12.000Z",
    "headings": ["Shipping details"],
    "textSnippets": ["Contact PHONE_1 or EMAIL_1 for help", "Order 234567890123 ships in 2 days"],
    "inputs": [
        {"ref": "input-0", "type": "text", "label": "Name", "isSensitive": True, "sanitizedValue": "PERSON_1"},
        {"ref": "input-1", "type": "text", "label": "Card", "isSensitive": True, "sanitizedValue": "CARD_1"},
    ],
    "buttons": [{"ref": "button-0", "text": "Continue"}],
    "links": [],
    "sensitiveItemsCount": 2,
    "detectedTypes": {"PERSON": 1, "CARD": 1},
}


# --- token handshake ------------------------------------------------------

def test_health_is_open(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_token_401(client):
    assert client.post("/analyze", json=SANITIZED_GRAPH).status_code == 401
    assert client.get("/models").status_code == 401
    assert client.post("/chat", json={"message": "hi"}).status_code == 401


def test_wrong_token_401(client):
    r = client.post("/analyze", json=SANITIZED_GRAPH, headers={"X-Aiva-Token": "nope"})
    assert r.status_code == 401


# --- /analyze -------------------------------------------------------------

def test_analyze_accepts_tokenised_payload(client, auth):
    r = client.post("/analyze", json=SANITIZED_GRAPH, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decidedBy"] == "rule-engine"
    assert body["action"] == "click" and body["targetRef"] == "button-0"


def _with_value(value):
    g = {**SANITIZED_GRAPH, "inputs": [dict(SANITIZED_GRAPH["inputs"][0], sanitizedValue=value)]}
    return g


def test_analyze_rejects_raw_pii(client, auth):
    for raw, kind in [
        ("priya@example.com", "email"),
        ("9876543210", "phone"),
        ("9999 4105 7058", "aadhaar"),
        ("ABCPE1234F", "pan"),
        ("4111 1111 1111 1111", "card"),
    ]:
        r = client.post("/analyze", json=_with_value(raw), headers=auth)
        assert r.status_code == 400, raw
        assert kind in r.json()["detail"]
        assert raw not in r.text  # the value itself is never echoed


def test_analyze_does_not_reject_unvalidated_lookalikes(client, auth):
    r = client.post("/analyze", json=_with_value("order 234567890123 ref 4111111111111112"), headers=auth)
    assert r.status_code == 200


def test_chat_rejects_raw_pii_in_message(client, auth):
    r = client.post("/chat", json={"message": "my card is 4111111111111111"}, headers=auth)
    assert r.status_code == 400


def test_chat_accepts_sanitized(client, auth):
    r = client.post("/chat", json={"message": "hello", "graph": SANITIZED_GRAPH}, headers=auth)
    assert r.status_code == 200
    assert r.json()["action"] == "chat_reply"


# --- size / validation limits ----------------------------------------------

def test_body_over_256kb_413(client, auth):
    big = {**SANITIZED_GRAPH, "padding": "x" * (300 * 1024)}
    r = client.post("/analyze", json=big, headers=auth)
    assert r.status_code == 413


def test_field_length_limits_422_without_echo(client, auth):
    secret_ish = "Z" * 5000
    r = client.post("/chat", json={"message": secret_ish}, headers=auth)
    assert r.status_code == 422
    assert secret_ish not in r.text


def test_list_size_limit(client, auth):
    g = {**SANITIZED_GRAPH, "headings": ["h"] * 500}
    assert client.post("/analyze", json=g, headers=auth).status_code == 422


# --- CORS -------------------------------------------------------------------

def _preflight(client, origin):
    return client.options(
        "/analyze",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-aiva-token",
        },
    )


def test_cors_allows_extension_and_localhost(client):
    for origin in (TEST_ORIGIN, "http://localhost:5500", "http://127.0.0.1:8000"):
        r = _preflight(client, origin)
        assert r.status_code == 200, origin
        assert r.headers.get("access-control-allow-origin") == origin


def test_cors_blocks_other_origins(client):
    r = _preflight(client, "https://evil.example")
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r = _preflight(client, "chrome-extension://someotherextensionidxxxxxxxxxxxx")
    assert "access-control-allow-origin" not in r.headers


# --- privacy: no off-device calls by default, no PII in logs ---------------

def test_cloud_fallback_off_by_default(main_module, monkeypatch):
    assert main_module.ALLOW_CLOUD is False
    monkeypatch.setattr(main_module, "GEMINI_API_KEY", "fake-key")

    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(main_module.urllib.request, "urlopen", boom)
    assert main_module.call_gemini_chat("summarize", SANITIZED_GRAPH) is None


def test_non_loopback_llm_refused_without_opt_in(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "LOCAL_LLM_BASE_URL", "http://10.0.0.5:11434/v1")
    assert main_module._local_llm_allowed() is False
    monkeypatch.setattr(main_module, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    assert main_module._local_llm_allowed() is True


def test_no_raw_pii_in_logs(client, auth, caplog):
    caplog.set_level(logging.DEBUG)
    raw = "priya@example.com"
    client.post("/analyze", json=_with_value(raw), headers=auth)
    client.post("/chat", json={"message": f"email {raw}"}, headers=auth)
    assert raw not in caplog.text
    assert TEST_TOKEN not in caplog.text


def test_token_file_created_on_first_run(tmp_path, monkeypatch):
    import security

    monkeypatch.delenv("AIVA_SHARED_TOKEN", raising=False)
    f = tmp_path / ".aiva_token"
    t1 = security.load_or_create_token(f)
    assert f.exists() and len(t1) >= 32
    assert security.load_or_create_token(f) == t1  # stable across restarts
    monkeypatch.setenv("AIVA_SHARED_TOKEN", "from-env")
    assert security.load_or_create_token(f) == "from-env"
