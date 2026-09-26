"""Classical-CV UI control detection (OpenCV, CPU, no model weights).

Web UI controls (text inputs, buttons, checkboxes, and the black privacy
masks the extension draws) are axis-aligned rectangles with a strong edge
along their whole perimeter. We find them with Canny edges + contours and
keep only rectangles whose border is mostly edge pixels, which rejects
glyph/word blobs. This costs ~20-40 ms on a 1280px screenshot, far cheaper
than a learned detector, and is fused with OCR text downstream.
"""

from typing import List, TypedDict

import cv2
import numpy as np


class Rect(TypedDict):
    bbox: List[int]  # [x, y, w, h]
    mean_gray: float
    std_gray: float
    saturation: float
    masked: bool


def iou(a: List[int], b: List[int]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _border_edge_ratio(edges: np.ndarray, x: int, y: int, w: int, h: int, band: int = 2) -> float:
    """Fraction of the rectangle's perimeter that has an edge pixel nearby."""
    H, W = edges.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W - 1, x + w - 1), min(H - 1, y + h - 1)
    top = edges[y0 : min(H, y0 + band), x0 : x1 + 1].max(axis=0) if y0 < H else np.zeros(1)
    bottom = edges[max(0, y1 - band + 1) : y1 + 1, x0 : x1 + 1].max(axis=0)
    left = edges[y0 : y1 + 1, x0 : min(W, x0 + band)].max(axis=1)
    right = edges[y0 : y1 + 1, max(0, x1 - band + 1) : x1 + 1].max(axis=1)
    hits = [(s > 0).mean() for s in (top, bottom, left, right) if s.size]
    return float(min(hits)) if hits else 0.0


def _region_stats(img_bgr: np.ndarray, gray: np.ndarray, bbox: List[int]):
    x, y, w, h = bbox
    pad = 3 if min(w, h) > 10 else 0
    g = gray[y + pad : y + h - pad, x + pad : x + w - pad]
    c = img_bgr[y + pad : y + h - pad, x + pad : x + w - pad]
    if g.size == 0:
        return 255.0, 0.0, 0.0
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    return float(g.mean()), float(g.std()), float(hsv[..., 1].mean())


def detect_rects(img_bgr: np.ndarray) -> List[Rect]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    edges = cv2.Canny(gray, 30, 100)
    edges_d = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges_d, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates: List[List[int]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < 14 or h < 12 or h > 140 or w > 0.9 * W:
            continue
        ar = w / float(h)
        if ar > 40 or (ar < 1.2 and not (0.8 <= ar <= 1.25 and h <= 32)):
            continue  # allow small squares (checkboxes/radios) only
        if _border_edge_ratio(edges_d, x, y, w, h) < 0.85:
            continue
        candidates.append([x, y, w, h])

    # Largest first; drop near-duplicates (inner/outer outline of one control).
    candidates.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept: List[List[int]] = []
    for b in candidates:
        if any(iou(b, k) > 0.6 for k in kept):
            continue
        kept.append(b)

    rects: List[Rect] = []
    for b in kept:
        mean_g, std_g, sat = _region_stats(img_bgr, gray, b)
        rects.append(
            {
                "bbox": [int(v) for v in b],
                "mean_gray": round(mean_g, 1),
                "std_gray": round(std_g, 1),
                "saturation": round(sat, 1),
                "masked": mean_g < 20 and std_g < 12,
            }
        )
    return rects


def text_ink_is_blue(img_bgr: np.ndarray, bbox: List[int]) -> bool:
    """Heuristic link detector: the darkest (ink) pixels of a text box are blue."""
    x, y, w, h = bbox
    crop = img_bgr[max(0, y) : y + h, max(0, x) : x + w]
    if crop.size == 0:
        return False
    # Background = median of the box border; ink = the pixels furthest from it.
    # (Taking the darkest pixels would call white-on-blue nav text a "link".)
    px = crop.reshape(-1, 3).astype(np.int32)
    border = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]]).astype(np.int32)
    bg = np.median(border, axis=0)
    dist = np.abs(px - bg).sum(axis=1)
    if dist.max() < 60:
        return False
    ink = px[dist >= np.percentile(dist, 85)]
    if ink.size == 0:
        return False
    b, g, r = ink[:, 0].mean(), ink[:, 1].mean(), ink[:, 2].mean()
    return b > r + 40 and b > g + 10
