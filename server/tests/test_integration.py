"""Cross-feature checks after merging privacy-hardening + NER + vision.

All names and addresses are INVENTED synthetic examples.
"""

import json

import pytest

from conftest import TEST_TOKEN, _font, to_b64


# ------------------------------------------------ token / body cap on new routes


@pytest.mark.parametrize("path", ["/perceive", "/ner/scan"])
def test_new_routes_require_token(client, path):
    body = {"image": "bm90IGFuIGltYWdl"} if path == "/perceive" else {"text": "hello"}
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"X-Aiva-Token": "nope"}).status_code == 401


def test_perceive_health_requires_token_but_health_is_open(client, auth):
    assert client.get("/perceive/health").status_code == 401
    assert client.get("/perceive/health", headers=auth).status_code == 200
    assert client.get("/health").status_code == 200


def test_ner_scan_with_token(client, auth):
    r = client.post("/ner/scan", json={"text": "Contact Priya Nair for details."}, headers=auth)
    assert r.status_code == 200
    assert "Priya" not in r.json()["tokenized"]


def test_perceive_has_own_bounded_body_cap(client, auth):
    # A screenshot is bigger than the 256 KB JSON cap: /perceive accepts it
    # (400 = reached the route and failed to decode, not 413)...
    big = {"image": "A" * (400 * 1024)}
    assert client.post("/perceive", json=big, headers=auth).status_code == 400
    # ...other routes still get 413 at that size, and /perceive has a ceiling too.
    assert client.post("/analyze", json={"pageTitle": "x", "textSnippets": ["a" * 900] * 450}, headers=auth).status_code == 413
    huge = {"image": "A" * (5 * 1024 * 1024)}
    assert client.post("/perceive", json=huge, headers=auth).status_code == 413


def test_perceive_cors_preflight_for_extension(client):
    from conftest import TEST_ORIGIN

    r = client.options(
        "/perceive",
        headers={
            "Origin": TEST_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-aiva-token",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == TEST_ORIGIN


# ------------------------------------------------ NER over OCR text (the gap)


def test_sanitize_texts_policies(monkeypatch):
    from ner import sanitize_texts

    texts = ["Applicant: Priya Sharma", "Submit", "Priya Sharma, 21 MG Road, Bengaluru"]
    monkeypatch.setenv("AIVA_NER_POLICY", "tokenize")
    out, counts = sanitize_texts(texts)
    assert out[1] == "Submit"
    assert "Priya" not in " ".join(out) and "NAME_1" in out[0]
    assert out[0].count("NAME_1") == 1 and "NAME_1" in out[2]  # same person -> same token
    assert counts.get("NAME", 0) >= 2

    monkeypatch.setenv("AIVA_NER_POLICY", "reject")
    out, _ = sanitize_texts(texts)
    assert "[NAME]" in out[0] and "Priya" not in " ".join(out)

    monkeypatch.setenv("AIVA_NER_POLICY", "off")
    assert sanitize_texts(texts)[0] == texts


def test_vision_uses_ner_sanitizer_once_main_is_loaded(main_module):
    from vision.pii import sanitize_names

    out, counts = sanitize_names(["Applicant: Priya Sharma"])
    assert "Priya" not in out[0] and counts.get("NAME") == 1


def _name_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (1280, 400), "white")
    d = ImageDraw.Draw(img)
    d.text((80, 60), "Applicant Details", fill="black", font=_font(40))
    d.text((80, 170), "Applicant: Priya Sharma", fill="black", font=_font(36))
    d.rectangle((80, 280, 300, 335), fill=(37, 99, 235))
    d.text((115, 290), "Submit", fill="white", font=_font(28))
    return img


@pytest.mark.slow
def test_perceive_tokenises_names_in_ocr_text(client, auth, monkeypatch):
    monkeypatch.setenv("AIVA_NER_POLICY", "tokenize")
    res = client.post("/perceive", json={"image": to_b64(_name_image())}, headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    dumped = json.dumps(body)
    assert "Priya" not in dumped and "Sharma" not in dumped, dumped
    assert body["nerMaskedServerSide"].get("NAME", 0) >= 1
    assert "NAME_1" in dumped
