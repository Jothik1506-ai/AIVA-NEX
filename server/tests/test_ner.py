"""
NER tests. Every name and address below is INVENTED for testing - they are
synthetic examples, not real people or real households.
"""

import time

import pytest
from fastapi.testclient import TestClient

import main
import ner
from ner import detect_names_addresses, sanitize_payload, tokenize_text
from ner.rules import find_address_spans, find_name_spans

FAKE_ADDR = "Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080"


def _types(text):
    return [(text[s["start"]:s["end"]], s["type"]) for s in detect_names_addresses(text)]


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------- detection

def test_model_is_loaded():
    assert ner.get_nlp() is not None, "run: python -m spacy download en_core_web_sm"
    assert ner.engine_name().startswith("spacy:")


def test_name_and_address_in_classic_indian_format():
    found = _types(FAKE_ADDR)
    assert ("Ravi Kumar", "NAME") in found
    assert ("H.No 12-3-45, Gandhi Nagar, Hyderabad 500080", "ADDRESS") in found
    # "Gandhi Nagar" is a locality inside the address, not a separate NAME
    assert not any(t == "NAME" and "Nagar" in s for s, t in found)


def test_span_shape():
    spans = detect_names_addresses(FAKE_ADDR)
    for sp in spans:
        assert set(sp) == {"start", "end", "type", "score"}
        assert sp["type"] in ("NAME", "ADDRESS")
        assert 0.0 < sp["score"] <= 1.0
        assert 0 <= sp["start"] < sp["end"] <= len(FAKE_ADDR)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please deliver to Smt. Lakshmi Devi, W/o Suresh Reddy, Flat 4B, Sai Residency, "
         "Ameerpet Road, Hyderabad, Telangana 500016",
         [("Smt. Lakshmi Devi", "NAME"), ("Suresh Reddy", "NAME"),
          ("Flat 4B, Sai Residency, Ameerpet Road, Hyderabad, Telangana 500016", "ADDRESS")]),
        ("My name is Anjali Sharma and I live at 21 MG Road, Bengaluru.",
         [("Anjali Sharma", "NAME"), ("21 MG Road, Bengaluru", "ADDRESS")]),
        ("Plot No 7, Sector 5, Noida, Uttar Pradesh 201301",
         [("Plot No 7, Sector 5, Noida, Uttar Pradesh 201301", "ADDRESS")]),
        ("Kiran S/o Ramesh Babu, Village Kondapur, Mandal Shankarpally, District Rangareddy",
         [("Kiran", "NAME"), ("Ramesh Babu", "NAME"),
          ("Village Kondapur, Mandal Shankarpally, District Rangareddy", "ADDRESS")]),
        ("Contact Priya Nair for details.", [("Priya Nair", "NAME")]),
        ("Welcome back, Arjun!", [("Arjun", "NAME")]),
        ("Dr. Meena Iyer will see you now", [("Dr. Meena Iyer", "NAME")]),
    ],
)
def test_detection_examples(text, expected):
    found = _types(text)
    for item in expected:
        assert item in found, f"missing {item} in {found}"


@pytest.mark.parametrize(
    "text",
    [
        "Submit", "Sign in", "Hyderabad weather", "Add to cart", "Google Search",
        "Terms and Conditions", "Apple iPhone 15 Pro Max", "Buy Samsung Galaxy M14 5G at best price",
        "Rains lash Hyderabad, Telangana; IMD issues alert", "Price: Rs 500080 only",
        "Road Safety Week begins", "Sri Lanka tour packages", "Don't Miss Out on deals",
        "Hello World", "Punjab National Bank ATM", "Best laptops under 50000 in Hyderabad",
        "Forgot password?", "Continue to payment", "Showing results for flights to Delhi",
        "EMAIL_1 and PHONE_1 were redacted; PERSON_1 is the account holder",
    ],
)
def test_no_false_positive_on_ui_and_product_text(text):
    assert detect_names_addresses(text) == []


def test_rules_work_without_the_model():
    """Fallback path (model missing): the Indian rule layer alone still catches these."""
    text = "Smt. Lakshmi Devi, Flat 4B, Sai Residency, Ameerpet Road, Hyderabad 500016"
    assert any(text[s.start:s.end] == "Smt. Lakshmi Devi" for s in find_name_spans(text))
    addrs = find_address_spans(text)
    assert len(addrs) == 1 and text[addrs[0].start:addrs[0].end].endswith("500016")
    assert find_address_spans("Hyderabad weather") == []


# ------------------------------------------------------------- tokenisation

def test_tokenise_replaces_spans():
    out, spans = tokenize_text(FAKE_ADDR)
    assert out == "NAME_1, ADDRESS_1"
    assert len(spans) == 2


def test_tokenisation_consistent_within_request():
    text = "Mr. Ravi Kumar paid. Later Ravi Kumar called Anita Rao, then ravi kumar again? Mr. Ravi Kumar left."
    out, _ = tokenize_text(text)
    assert "Ravi" not in out and "Anita" not in out
    assert out.count("NAME_1") >= 3 and "NAME_2" in out
    assert "NAME_3" not in out


def test_sanitize_payload_shares_tokens_and_avoids_collisions():
    payload = {
        "pageTitle": "Order for Vikram Singh",
        "headings": ["Ship to Vikram Singh"],
        "textSnippets": [FAKE_ADDR, "Free delivery on orders above 499"],
        "inputs": [{"ref": "input-0", "type": "text", "label": "Name", "sanitizedValue": "NAME_1"}],
        "domain": "shop.example.test",
    }
    clean, found = sanitize_payload(payload)
    # existing NAME_1 from the extension is reserved -> server numbering starts at 2
    assert clean["pageTitle"] == "Order for NAME_2"
    assert clean["headings"] == ["Ship to NAME_2"]
    assert clean["textSnippets"][0] == "NAME_3, ADDRESS_1"
    assert clean["textSnippets"][1] == "Free delivery on orders above 499"
    assert clean["inputs"][0] == payload["inputs"][0]
    assert clean["domain"] == payload["domain"]
    assert found == {"NAME": 3, "ADDRESS": 1}
    assert payload["pageTitle"] == "Order for Vikram Singh"  # input not mutated


# ------------------------------------------------------------------ routes

def test_ner_scan_route(client):
    r = client.post("/ner/scan", json={"text": FAKE_ADDR})
    assert r.status_code == 200
    body = r.json()
    assert body["tokenized"] == "NAME_1, ADDRESS_1"
    assert [s["type"] for s in body["spans"]] == ["NAME", "ADDRESS"]
    assert body["engine"].startswith("spacy:")
    assert "Ravi" not in r.text  # raw input is never echoed


def test_ner_scan_rejects_oversized(client):
    r = client.post("/ner/scan", json={"text": "a" * 20001})
    assert r.status_code == 422


def test_analyze_tokenises_names_before_llm(client, monkeypatch):
    seen = {}

    def fake_llm(graph, model=None):
        seen["graph"] = graph
        return None

    monkeypatch.setattr(main, "call_local_llm", fake_llm)
    monkeypatch.delenv("AIVA_NER_POLICY", raising=False)
    r = client.post("/analyze", json={"pageTitle": "Profile", "textSnippets": [FAKE_ADDR]})
    assert r.status_code == 200
    assert seen["graph"]["textSnippets"] == ["NAME_1, ADDRESS_1"]


def test_analyze_reject_policy(client, monkeypatch):
    monkeypatch.setenv("AIVA_NER_POLICY", "reject")
    r = client.post("/analyze", json={"pageTitle": "Profile", "textSnippets": [FAKE_ADDR]})
    assert r.status_code == 400
    assert "name" in r.json()["detail"] and "address" in r.json()["detail"]
    assert "Ravi" not in r.text
    ok = client.post("/analyze", json={"pageTitle": "Hyderabad weather", "buttons": [{"ref": "b1", "text": "Submit"}]})
    assert ok.status_code == 200


def test_chat_tokenises_message_and_graph(client, monkeypatch):
    seen = {}

    def fake_decide(query, graph_dict, model=None):
        seen["q"], seen["g"] = query, graph_dict
        return {"reply": "ok", "action": "chat_reply"}

    monkeypatch.setattr(main, "decide_chat_response", fake_decide)
    monkeypatch.delenv("AIVA_NER_POLICY", raising=False)
    r = client.post("/chat", json={"message": "Is Anita Rao's parcel for Anita Rao here?",
                                   "graph": {"textSnippets": ["Deliver to Anita Rao"]}})
    assert r.status_code == 200
    assert seen["q"] == "Is NAME_1's parcel for NAME_1 here?"
    assert seen["g"]["textSnippets"] == ["Deliver to NAME_1"]


def test_regex_pii_rejection_still_first(client):
    r = client.post("/analyze", json={"textSnippets": ["mail ravi@example.test now"]})
    assert r.status_code == 400 and "email" in r.json()["detail"]


# -------------------------------------------------------------- performance

def test_latency_2kb_text():
    base = (FAKE_ADDR + ". Contact Priya Nair for details. Buy Samsung Galaxy M14 5G at best price. "
            "Showing results for flights to Delhi. ")
    text = (base * 20)[:2048]
    ner.detect_spans(text)  # warm
    t0 = time.perf_counter()
    for _ in range(5):
        ner.detect_spans(text)
    avg_ms = (time.perf_counter() - t0) / 5 * 1000
    assert avg_ms < 1000, f"2 KB NER took {avg_ms:.0f} ms"
