"""Tests for the on-device visual perception pipeline (SIH26171).

OCR tests load the RapidOCR ONNX models (bundled in the pip wheel), which
takes ~1-2 s once per session; they are marked ``slow``.
Run everything:          pytest
Skip the OCR ones:       pytest -m "not slow"
"""

import json

import pytest

from conftest import TEST_TOKEN, make_form_image, to_b64
from vision import attach_visual_target, perceive
from vision.detect import detect_rects, iou
from vision.pii import mask_pii_text
from vision.pipeline import PerceptionError, decode_image, downscale


@pytest.fixture()
def client(main_module):
    # /perceive and /analyze need the shared token like every route but /health.
    from fastapi.testclient import TestClient

    return TestClient(main_module.app, headers={"X-Aiva-Token": TEST_TOKEN})


def _squash(s):
    # PP-OCR sometimes drops inter-word spaces on JPEG input; compare without them.
    return "".join((s or "").lower().split())


def _near(bbox, target, min_iou=0.5):
    return iou(list(bbox), list(target)) >= min_iou


# ---------------------------------------------------------------- fast tests


def test_pii_mask_text():
    masked, types = mask_pii_text("Mail priya.sharma@example.com or call 9876543210")
    assert "@" not in masked and "9876543210" not in masked
    assert set(types) == {"email", "phone"}
    assert mask_pii_text("Submit") == ("Submit", [])


def test_pii_mask_survives_ocr_dropped_spaces():
    masked, types = mask_pii_text("Phone9876543210forhelp")
    assert "9876543210" not in masked and types == ["phone"]
    assert mask_pii_text("PAN ABCDE1234F")[1] == ["pan"]
    assert mask_pii_text("Step2of3") == ("Step2of3", [])


def test_decode_rejects_bad_input():
    with pytest.raises(PerceptionError):
        decode_image("")
    with pytest.raises(PerceptionError):
        decode_image("bm90IGFuIGltYWdl")  # "not an image"


def test_decode_accepts_data_url_and_downscales():
    from PIL import Image

    big = Image.new("RGB", (2560, 1600), "white")
    img = decode_image("data:image/png;base64," + to_b64(big, "PNG"))
    small = downscale(img)
    assert max(small.shape[:2]) == 1280 and small.shape[1] == 1280


def test_cv_detects_inputs_and_buttons():
    img, layout = make_form_image()
    import cv2
    import numpy as np

    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    boxes = [r["bbox"] for r in detect_rects(bgr)]
    for key in ("name_input", "email_input", "submit", "reset"):
        assert any(_near(b, layout[key]) for b in boxes), f"{key} not detected in {boxes}"


def test_extension_style_mask_is_recognised_and_hides_content():
    # A black box over the email field, as vision.js draws it on-device.
    img, layout = make_form_image(with_pii_text=True, masked_box=(76, 281, 508, 53))
    import cv2
    import numpy as np

    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    x, y, w, h = layout["email_input"]
    region = bgr[y : y + h, x : x + w]
    assert region.max() < 10, "masked region must be solid black"
    masked = [r for r in detect_rects(bgr) if r["masked"]]
    assert masked and _near(masked[0]["bbox"], (76, 281, 508, 53))


def test_attach_visual_target_maps_visual_id_to_dom_ref():
    graph = {
        "visualElements": [
            {"id": "v1", "type_guess": "input", "text": "", "bbox_css": [10, 10, 200, 30], "domRef": "input-0"},
            {"id": "v2", "type_guess": "button", "text": "Submit", "bbox_css": [10, 60, 100, 30]},
        ]
    }
    a = attach_visual_target({"action": "click", "targetRef": "v2", "reason": "llm"}, graph)
    assert a["targetVisualId"] == "v2" and "targetRef" not in a and a["bbox"] == [10, 60, 100, 30]
    b = attach_visual_target({"action": "focus", "targetRef": "v1"}, graph)
    assert b["targetRef"] == "input-0" and b["targetVisualId"] == "v1"
    c = attach_visual_target({"action": "focus", "targetRef": "input-0"}, graph)
    assert c["targetVisualId"] == "v1"
    d = attach_visual_target({"action": "summarize", "summary": "x", "decidedBy": "rule-engine"}, graph)
    assert d["action"] == "click" and d["targetVisualId"] == "v2" and d["decidedBy"] == "rule-engine+vision"
    assert attach_visual_target({"action": "scroll"}, {}) == {"action": "scroll"}


def test_analyze_accepts_visual_elements(client, monkeypatch):
    import main

    monkeypatch.setattr(main, "call_local_llm", lambda graph, model=None: None)
    graph = {
        "pageTitle": "Canvas app",
        "inputs": [],
        "buttons": [],
        "visualElements": [
            {"id": "v1", "type_guess": "heading", "text": "Checkout", "bbox_css": [20, 20, 300, 40], "confidence": 0.7},
            {"id": "v2", "type_guess": "button", "text": "Proceed", "bbox_css": [20, 100, 120, 40], "confidence": 0.9},
        ],
    }
    res = client.post("/analyze", json=graph)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["action"] == "click" and body["targetVisualId"] == "v2"
    assert body["bbox"] == [20, 100, 120, 40]


def test_known_refs_include_visual_ids():
    import main

    refs = main._known_refs({"inputs": [{"ref": "input-0"}], "visualElements": [{"id": "v4"}]})
    assert refs == {"input-0", "v4"}


def test_perceive_route_rejects_bad_image(client):
    res = client.post("/perceive", json={"image": "bm90IGFuIGltYWdl"})
    assert res.status_code == 400


# ---------------------------------------------------------------- OCR (slow)


@pytest.mark.slow
def test_ocr_finds_expected_text(form_b64):
    b64, _ = form_b64
    result = perceive(b64)
    assert result["ocr"]["available"], result["ocr"]
    texts = " | ".join(e["text"] for e in result["elements"])
    for expected in ("Scholarship Application", "Full Name", "Email Address", "Submit", "Reset"):
        assert _squash(expected) in _squash(texts), f"{expected!r} missing from {texts}"


@pytest.mark.slow
def test_pipeline_classifies_controls(form_b64):
    b64, layout = form_b64
    els = perceive(b64)["elements"]
    submit = next(e for e in els if e["text"].lower() == "submit")
    assert submit["type_guess"] == "button" and _near(submit["bbox"], layout["submit"])
    inputs = [e for e in els if e["type_guess"] == "input"]
    assert len(inputs) >= 2
    labels = {_squash(e.get("label")) for e in inputs}
    assert "fullname" in labels and "emailaddress" in labels
    assert all(set(e) >= {"id", "text", "bbox", "type_guess", "confidence"} for e in els)


@pytest.mark.slow
def test_server_side_pii_recheck_masks_ocr_text():
    img, _ = make_form_image(with_pii_text=True)  # extension "forgot" to mask
    result = perceive(to_b64(img))
    dumped = json.dumps(result["elements"])
    assert "priya.sharma@example.com" not in dumped and "9876543210" not in dumped
    assert "email" in result["piiMaskedServerSide"] and "phone" in result["piiMaskedServerSide"]


@pytest.mark.slow
def test_perceive_route_with_dom_fusion(client, form_b64, monkeypatch):
    import main

    monkeypatch.setattr(main, "call_local_llm", lambda graph, model=None: None)
    b64, layout = form_b64
    x, y, w, h = layout["submit"]
    # Page rendered at devicePixelRatio 2: CSS viewport is half the image width.
    dom = [{"ref": "button-0", "kind": "button", "text": "Submit", "x": x / 2, "y": y / 2, "width": w / 2, "height": h / 2}]
    res = client.post("/perceive", json={"image": b64, "domElements": dom, "viewport": {"width": 640, "height": 400}})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["scaleCss"] == 2.0
    submit = next(e for e in body["elements"] if e.get("domRef") == "button-0")
    assert submit["type_guess"] == "button" and "+dom" in submit["source"]
    assert _near(submit["bbox_css"], (x / 2, y / 2, w / 2, h / 2))
    assert body["summary"].startswith("Screen shows")
    # the perceived elements are accepted back by /analyze (no PII false positive)
    graph = {"inputs": [], "buttons": [], "visualElements": body["elements"], "visualSummary": body["summary"]}
    assert client.post("/analyze", json=graph).status_code == 200


@pytest.mark.slow
def test_latency_budget(form_b64):
    b64, _ = form_b64
    perceive(b64)  # warm-up (model load)
    total = perceive(b64)["timingsMs"]["total"]
    assert total < 4000, f"perception took {total} ms"
