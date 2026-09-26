"""Visual perception pipeline: masked screenshot -> visual UI elements.

Steps (all on the local CPU, nothing leaves the machine):
  1. decode + (re)downscale the already-masked JPEG to <= 1280 px
  2. OCR text lines (RapidOCR / ONNX)          -> text boxes
  3. OpenCV rectangle detection                 -> control candidates
  4. server-side PII re-check on every OCR string (mask, never return raw)
  5. fuse OCR + CV (+ the sanitized DOM boxes, when the extension sends them)
     into {id, text, bbox, type_guess, confidence}, and write a short summary
The image itself is never written to disk or returned.
"""

import base64
import re
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from .detect import detect_rects, iou, text_ink_is_blue
from .ocr import ocr_status, run_ocr
from .pii import mask_pii_text

MAX_SIDE = 1280
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 150

BUTTON_WORDS = re.compile(
    r"\b(submit|continue|next|log ?in|sign ?in|sign ?up|register|pay|proceed|apply|search|send|save|"
    r"confirm|place order|buy|add to cart|checkout|ok|cancel|reset|back|go|upload|verify|get otp)\b",
    re.IGNORECASE,
)


class PerceptionError(ValueError):
    pass


def decode_image(image_b64: str) -> np.ndarray:
    if not image_b64:
        raise PerceptionError("image is required")
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(image_b64, validate=False)
    except Exception as exc:
        raise PerceptionError(f"image is not valid base64: {exc}") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise PerceptionError("image too large (max 8 MB)")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PerceptionError("could not decode image (send PNG or JPEG)")
    return img


def downscale(img: np.ndarray, max_side: int = MAX_SIDE) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max_side / float(max(h, w))
    if scale >= 1:
        return img
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _center_inside(inner: List[int], outer: List[int]) -> bool:
    cx, cy = inner[0] + inner[2] / 2, inner[1] + inner[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]


def _classify_rect(rect: Dict[str, Any], texts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    x, y, w, h = rect["bbox"]
    ar = w / float(h)
    text = " ".join(t["text"] for t in sorted(texts, key=lambda t: (t["bbox"][1], t["bbox"][0])))
    if rect["masked"]:
        return {"type_guess": "masked_sensitive", "text": "[MASKED ON DEVICE]", "confidence": 0.95}
    filled = rect["mean_gray"] < 200 or rect["saturation"] > 50
    if texts:
        if BUTTON_WORDS.search(text) and len(text.split()) <= 5:
            return {"type_guess": "button", "text": text, "confidence": 0.9 if filled else 0.75}
        if filled and len(text.split()) <= 5:
            return {"type_guess": "button", "text": text, "confidence": 0.7}
        # Outline button: short text centred in the box (input text is left-aligned).
        tx0 = min(t["bbox"][0] for t in texts)
        tx1 = max(t["bbox"][0] + t["bbox"][2] for t in texts)
        centred = abs((tx0 + tx1) / 2 - (x + w / 2)) <= 0.05 * w and (tx0 - x) >= 0.06 * w
        if centred and len(text.split()) <= 5:
            return {"type_guess": "button", "text": text, "confidence": 0.65}
        if ar >= 2.5 and h <= 80:
            # bright box with text inside: an input showing a placeholder/value
            return {"type_guess": "input", "text": text, "confidence": 0.6}
        return None  # a card/panel around paragraphs - not an actionable control
    if 0.8 <= ar <= 1.25 and h <= 32:
        return {"type_guess": "checkbox", "text": "", "confidence": 0.5}
    if ar >= 2.5 and h <= 80 and not filled:
        return {"type_guess": "input", "text": "", "confidence": 0.65}
    if filled and ar >= 1.5:
        return {"type_guess": "button", "text": "", "confidence": 0.4}
    return None


def _nearest_label(inp: Dict[str, Any], text_elems: List[Dict[str, Any]]) -> Optional[str]:
    """A label is a text line just above, or to the left on the same row."""
    x, y, w, h = inp["bbox"]
    best, best_d = None, 1e9
    for t in text_elems:
        tx, ty, tw, th = t["bbox"]
        above = -8 <= y - (ty + th) <= 45 and tx < x + w and tx + tw > x - 10
        left = abs((ty + th / 2) - (y + h / 2)) <= h / 2 + 4 and 0 <= x - (tx + tw) <= 120
        if not (above or left):
            continue
        d = max(0, y - (ty + th)) if above else (x - (tx + tw))
        if d < best_d:
            best, best_d = t["text"], d
    return best


def _fuse(img: np.ndarray, ocr_boxes, rects, dom_elements, scale_css: Optional[float]):
    elements: List[Dict[str, Any]] = []
    used_text = set()

    for rect in rects:
        inside = [i for i, t in enumerate(ocr_boxes) if _center_inside(t["bbox"], rect["bbox"])]
        cls = _classify_rect(rect, [ocr_boxes[i] for i in inside])
        if cls is None:
            continue
        used_text.update(inside)
        elements.append({**cls, "bbox": rect["bbox"], "source": "cv+ocr" if inside else "cv"})

    text_elems = []
    heights = sorted(t["bbox"][3] for t in ocr_boxes) or [0]
    median_h = heights[len(heights) // 2]
    for i, t in enumerate(ocr_boxes):
        if i in used_text:
            continue
        if BUTTON_WORDS.fullmatch(t["text"].strip()):
            kind, conf = "button", 0.55  # borderless text button
        elif text_ink_is_blue(img, t["bbox"]):
            kind, conf = "link", 0.6
        elif median_h and t["bbox"][3] >= 1.5 * median_h:
            kind, conf = "heading", 0.7
        else:
            kind, conf = "text", 0.8
        el = {"type_guess": kind, "text": t["text"], "bbox": t["bbox"], "confidence": conf, "source": "ocr"}
        elements.append(el)
        text_elems.append(el)

    for el in elements:
        if el["type_guess"] == "input":
            label = _nearest_label(el, text_elems)
            if label:
                el["label"] = label

    # Fuse with the sanitized DOM boxes (CSS px -> image px).
    if dom_elements and scale_css:
        H, W = img.shape[:2]
        for d in dom_elements:
            try:
                db = [int(round(float(d[k]) * scale_css)) for k in ("x", "y", "width", "height")]
            except (KeyError, TypeError, ValueError):
                continue
            if db[2] <= 2 or db[3] <= 2 or db[0] >= W or db[1] >= H or db[0] + db[2] <= 0 or db[1] + db[3] <= 0:
                continue  # off-screen / invisible
            best, best_iou = None, 0.0
            for el in elements:
                if el.get("domRef"):
                    continue
                score = iou(db, el["bbox"])
                if score > best_iou:
                    best, best_iou = el, score
            dom_text = mask_pii_text(str(d.get("text") or ""))[0][:80]
            kind = str(d.get("kind") or "")
            if best is not None and best_iou >= 0.3:
                best["domRef"] = d.get("ref")
                best["source"] += "+dom"
                best["confidence"] = min(0.99, best["confidence"] + 0.15)
                if kind in ("input", "button", "link") and best["type_guess"] not in ("masked_sensitive",):
                    best["type_guess"] = kind
                if not best["text"] and dom_text:
                    best["text"] = dom_text
            else:
                elements.append(
                    {
                        "type_guess": kind or "element",
                        "text": dom_text,
                        "bbox": db,
                        "confidence": 0.5,
                        "source": "dom",
                        "domRef": d.get("ref"),
                    }
                )

    elements.sort(key=lambda e: (e["bbox"][1] // 10, e["bbox"][0]))
    elements = elements[:MAX_ELEMENTS]
    for i, el in enumerate(elements, 1):
        el["id"] = f"v{i}"
        el["confidence"] = round(float(el["confidence"]), 2)
        if scale_css:
            el["bbox_css"] = [int(round(v / scale_css)) for v in el["bbox"]]
    return elements


def summarize(elements: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for e in elements:
        counts[e["type_guess"]] = counts.get(e["type_guess"], 0) + 1
    heading = next((e["text"] for e in elements if e["type_guess"] == "heading" and e["text"]), None)
    if heading is None:
        texts = [e for e in elements if e["type_guess"] in ("text", "heading") and e["text"]]
        heading = max(texts, key=lambda e: e["bbox"][3])["text"] if texts else None
    buttons = [e["text"] for e in elements if e["type_guess"] == "button" and e["text"]][:3]
    inputs = [e.get("label") or e["text"] for e in elements if e["type_guess"] == "input"]
    parts = []
    if heading:
        parts.append(f"Screen shows '{heading[:80]}'")
    else:
        parts.append("Screen")
    bits = []
    if counts.get("input"):
        labelled = [l for l in inputs if l][:4]
        bits.append(f"{counts['input']} input field(s)" + (f" ({', '.join(labelled)})" if labelled else ""))
    if counts.get("button"):
        bits.append(f"{counts['button']} button(s)" + (f" ({', '.join(buttons)})" if buttons else ""))
    if counts.get("link"):
        bits.append(f"{counts['link']} link(s)")
    if counts.get("masked_sensitive"):
        bits.append(f"{counts['masked_sensitive']} region(s) masked on-device")
    text_n = counts.get("text", 0) + counts.get("heading", 0)
    if text_n:
        bits.append(f"{text_n} text block(s)")
    return parts[0] + (" with " + ", ".join(bits) if bits else "") + "."


def perceive(
    image_b64: str,
    dom_elements: Optional[List[Dict[str, Any]]] = None,
    viewport: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    img = downscale(decode_image(image_b64))
    H, W = img.shape[:2]
    t_decode = time.perf_counter()

    scale_css = None
    if viewport and viewport.get("width"):
        try:
            scale_css = W / float(viewport["width"])
        except (TypeError, ValueError, ZeroDivisionError):
            scale_css = None

    ocr_boxes = run_ocr(img)
    t_ocr = time.perf_counter()

    pii_found: Dict[str, int] = {}
    for box in ocr_boxes:
        masked, types = mask_pii_text(box["text"])
        box["text"] = masked
        for t in types:
            pii_found[t] = pii_found.get(t, 0) + 1

    rects = detect_rects(img)
    t_cv = time.perf_counter()

    elements = _fuse(img, ocr_boxes, rects, dom_elements or [], scale_css)
    summary = summarize(elements)
    t_end = time.perf_counter()

    ms = lambda a, b: int(round((b - a) * 1000))  # noqa: E731
    return {
        "elements": elements,
        "summary": summary,
        "imageSize": {"width": W, "height": H},
        "scaleCss": round(scale_css, 4) if scale_css else None,
        "piiMaskedServerSide": pii_found,
        "timingsMs": {
            "decode": ms(t0, t_decode),
            "ocr": ms(t_decode, t_ocr),
            "cv": ms(t_ocr, t_cv),
            "fuse": ms(t_cv, t_end),
            "total": ms(t0, t_end),
        },
        "ocr": ocr_status(),
    }
