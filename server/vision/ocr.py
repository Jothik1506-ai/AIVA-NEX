"""On-device OCR wrapper (RapidOCR = PaddleOCR PP-OCR models exported to ONNX).

Why RapidOCR: ~15 MB of ONNX models that ship inside the pip wheel (no
separate download, nothing fetched at runtime), runs on onnxruntime's CPU
provider (no GPU/CUDA), and returns text + quadrilateral boxes, which is what
we need to locate UI text. The engine is created lazily once per process and
reused, so only the first request pays the ~1 s model-load cost.
"""

import math
import threading
from typing import List, Optional, TypedDict

import numpy as np


class OcrBox(TypedDict):
    text: str
    bbox: List[int]  # [x, y, w, h] in image pixels
    score: float


_engine = None
_engine_lock = threading.Lock()
_engine_error: Optional[str] = None


REC_WIDTH_BUCKET_PX = 160


def _bucket_rec_widths(engine) -> None:
    """Round each recogniser input width up to a multiple of 160 px.

    With batch size 1 every text line has its own width, and onnxruntime pays
    a one-off cost for every new input shape it sees - a fresh text-dense page
    took 5.4 s on first sight vs 1.9 s once seen. Bucketing (zero padding,
    exactly what PaddleOCR does inside a batch) caps the distinct shapes at
    ~15, which the startup warm-up covers.
    """
    rec = getattr(engine, "text_rec", None)
    if rec is None or not hasattr(rec, "resize_norm_img"):
        return
    original = rec.resize_norm_img
    img_h = rec.rec_image_shape[1]

    def bucketed(img, max_wh_ratio):
        width = math.ceil(img_h * max_wh_ratio / REC_WIDTH_BUCKET_PX) * REC_WIDTH_BUCKET_PX
        return original(img, width / float(img_h))

    rec.resize_norm_img = bucketed


def _get_engine():
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None and _engine_error is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                # rec_batch_num=1: batching pads every crop to the widest line
                # in the batch; on a text-dense 1280px page (80 lines) this
                # measured 1.45 s rec time vs 2.6 s with the default of 6.
                _engine = RapidOCR(rec_batch_num=1)
                _bucket_rec_widths(_engine)
            except Exception as exc:  # pragma: no cover - depends on install
                _engine_error = f"{type(exc).__name__}: {exc}"
    return _engine


def warmup_async() -> threading.Thread:
    """Load the models + run one tiny inference in the background at startup,
    so the first /perceive call doesn't pay the ~1 s cold start."""

    def _run():
        engine = _get_engine()
        if engine is not None:
            try:
                # Full-size frame with text, so both det and rec sessions are
                # warmed at realistic shapes (a tiny image left ~1 s on the table).
                import cv2

                img = np.full((800, 1280, 3), 255, np.uint8)
                # Lines of increasing length hit most rec width buckets.
                sentence = "Warm up the on device OCR engine for visual perception " * 3
                for i in range(11):
                    n = 4 + i * 11
                    cv2.putText(img, sentence[:n], (20, 50 + 70 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
                for _ in range(2):
                    engine(img, use_cls=False)
            except Exception:
                pass

    thread = threading.Thread(target=_run, name="ocr-warmup", daemon=True)
    thread.start()
    return thread


def ocr_available() -> bool:
    return _get_engine() is not None


def ocr_status() -> dict:
    return {"available": ocr_available(), "engine": "rapidocr-onnxruntime (CPU)", "error": _engine_error}


def run_ocr(image_bgr: np.ndarray, min_score: float = 0.5) -> List[OcrBox]:
    """Detect + recognise text lines. Returns [] if OCR is unavailable."""
    engine = _get_engine()
    if engine is None:
        return []
    # Web pages are upright; skipping the angle classifier saves ~20-30%.
    result, _elapse = engine(image_bgr, use_cls=False)
    boxes: List[OcrBox] = []
    for item in result or []:
        quad, text, score = item[0], str(item[1]).strip(), float(item[2])
        if not text or score < min_score:
            continue
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        boxes.append({"text": text, "bbox": [x0, y0, x1 - x0, y1 - y0], "score": round(score, 2)})
    return boxes
