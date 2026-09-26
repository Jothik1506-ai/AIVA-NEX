// vision-content.js - Visual perception helper (SIH26171), runs in the page.
//
// Deliberately separate from content.js (which owns detection/tokenisation):
// this file only reports RECTANGLES, never values or text, so the side panel
// can black out sensitive regions of the screenshot before it leaves the
// browser, and it draws the highlight for the element the agent chose.
(() => {
  if (window.__aivaVisionLoaded) return;
  window.__aivaVisionLoaded = true;

  const REF_ATTR = "data-pa-ref"; // set by content.js during a scan
  const HIGHLIGHT_CLASS = "aiva-vision-highlight";

  // Anything that must never be visible in a screenshot, even if the scan
  // missed it: fields content.js flagged, its redaction overlays, and
  // password / card / OTP inputs by type or autocomplete hint.
  const ALWAYS_MASK_SELECTOR = [
    ".pa-redaction-overlay",
    'input[type="password"]',
    'input[autocomplete^="cc-"]',
    'input[autocomplete="one-time-code"]',
  ].join(",");

  function rectOf(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, width: r.width, height: r.height };
  }

  function inViewport(r) {
    return r.width > 0 && r.height > 0 && r.x < innerWidth && r.y < innerHeight && r.x + r.width > 0 && r.y + r.height > 0;
  }

  function getMaskRects(sensitiveRefs) {
    const maskEls = new Set(document.querySelectorAll(ALWAYS_MASK_SELECTOR));
    (sensitiveRefs || []).forEach((ref) => {
      const el = document.querySelector(`[${REF_ATTR}="${CSS.escape(ref)}"]`);
      if (el) maskEls.add(el);
    });
    const maskRects = Array.from(maskEls).map(rectOf).filter(inViewport);

    // Fresh positions (no text) of every element the scan tagged, so the
    // server can fuse visual boxes with DOM refs even after scrolling.
    const domBoxes = [];
    document.querySelectorAll(`[${REF_ATTR}]`).forEach((el) => {
      const r = rectOf(el);
      if (inViewport(r)) domBoxes.push({ ref: el.getAttribute(REF_ATTR), ...r });
    });

    return {
      viewport: { width: innerWidth, height: innerHeight },
      devicePixelRatio: devicePixelRatio,
      maskRects,
      domBoxes,
    };
  }

  function clearHighlights() {
    document.querySelectorAll("." + HIGHLIGHT_CLASS).forEach((n) => n.remove());
  }

  function highlight(bbox, label) {
    clearHighlights();
    if (!bbox) return;
    const [x, y, w, h] = bbox;
    const box = document.createElement("div");
    box.className = HIGHLIGHT_CLASS;
    Object.assign(box.style, {
      position: "fixed",
      left: x - 4 + "px",
      top: y - 4 + "px",
      width: w + 8 + "px",
      height: h + 8 + "px",
      border: "3px solid #7c3aed",
      borderRadius: "6px",
      boxShadow: "0 0 0 4px rgba(124,58,237,0.25)",
      zIndex: 2147483647,
      pointerEvents: "none",
    });
    if (label) {
      const tag = document.createElement("div");
      tag.textContent = label;
      Object.assign(tag.style, {
        position: "absolute",
        top: "-22px",
        left: "0",
        background: "#7c3aed",
        color: "#fff",
        font: "11px/18px system-ui, sans-serif",
        padding: "0 6px",
        borderRadius: "4px",
        whiteSpace: "nowrap",
      });
      box.appendChild(tag);
    }
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 6000);
  }

  // Acts on a visual-only target (no DOM ref, e.g. a canvas-drawn button):
  // resolves the element under the bbox centre and clicks/focuses it.
  function actAt(action, bbox) {
    if (!bbox) return { ok: false, error: "No bbox for visual target." };
    const [x, y, w, h] = bbox;
    clearHighlights();
    const el = document.elementFromPoint(x + w / 2, y + h / 2);
    if (!el) return { ok: false, error: "Nothing at the visual target's position." };
    if (action === "focus") {
      el.focus();
      return { ok: true, message: "Focused visual target." };
    }
    el.click();
    return { ok: true, message: "Clicked visual target." };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "VISION_GET_MASK_RECTS") {
      sendResponse({ ok: true, ...getMaskRects(msg.sensitiveRefs) });
      return true;
    }
    if (msg.type === "VISION_HIGHLIGHT") {
      highlight(msg.bbox, msg.label);
      sendResponse({ ok: true });
      return true;
    }
    if (msg.type === "VISION_ACT_AT") {
      sendResponse(actAt(msg.action, msg.bbox));
      return true;
    }
    return false;
  });
})();
