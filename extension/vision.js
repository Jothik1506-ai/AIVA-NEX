// vision.js - "Visual mode" for the side panel (SIH26171: on-device visual
// perception for a lightweight browser agent).
//
// Flow when Visual mode is ON and the user sends context:
//   1. ask vision-content.js for the on-screen rectangles of every sensitive
//      field (flagged by content.js during the scan) + fresh DOM boxes
//   2. chrome.tabs.captureVisibleTab -> OffscreenCanvas
//   3. downscale to <= 1280 px and paint SOLID BLACK boxes over every
//      sensitive rect - on-device, before the pixels go anywhere
//   4. send the masked JPEG only to the LOCAL server (POST /perceive), which
//      runs OCR + CV on the CPU and returns visual elements with bboxes
//   5. the elements ride along to /analyze; the chosen element is outlined
//      on the page
// If step 1 fails (no content script, e.g. chrome:// pages) NO screenshot is
// taken or sent.
/* global chrome */
(function () {
  const STORAGE_KEY = "aivaVisualMode";
  const MAX_SIDE = 1280;
  const MASK_PAD_CSS = 4;
  let enabled = false;

  function sendToTab(tabId, msg) {
    return new Promise((resolve, reject) => {
      chrome.tabs.sendMessage(tabId, msg, (res) => {
        if (chrome.runtime.lastError || !res) {
          reject(new Error((chrome.runtime.lastError && chrome.runtime.lastError.message) || "No response from page"));
        } else resolve(res);
      });
    });
  }

  function sendToBackground(msg) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(msg, (res) => {
        if (chrome.runtime.lastError || !res) reject(new Error("Background did not respond"));
        else if (!res.ok) reject(new Error(res.error || "Request failed"));
        else resolve(res.data);
      });
    });
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",", 2)[1]);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
  }

  // Exported for reuse/testing: draws the screenshot downscaled and masks
  // every rect (CSS px, viewport-relative) with opaque black.
  async function maskAndEncode(dataUrl, viewport, maskRects) {
    const bmp = await createImageBitmap(await (await fetch(dataUrl)).blob());
    const scaleDown = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
    const ow = Math.round(bmp.width * scaleDown);
    const oh = Math.round(bmp.height * scaleDown);
    const canvas = new OffscreenCanvas(ow, oh);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(bmp, 0, 0, ow, oh);
    bmp.close();

    // CSS px -> output px (covers devicePixelRatio, page zoom and downscale).
    const s = ow / viewport.width;
    ctx.fillStyle = "#000";
    for (const r of maskRects) {
      ctx.fillRect(
        Math.floor((r.x - MASK_PAD_CSS) * s),
        Math.floor((r.y - MASK_PAD_CSS) * s),
        Math.ceil((r.width + 2 * MASK_PAD_CSS) * s),
        Math.ceil((r.height + 2 * MASK_PAD_CSS) * s)
      );
    }
    const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.9 } /* <0.9 blurs glyph gaps; OCR then drops spaces */);
    return { base64: await blobToBase64(blob), previewUrl: URL.createObjectURL(blob), width: ow, height: oh };
  }

  function domElementsFrom(graph, domBoxes) {
    const meta = {};
    (graph.inputs || []).forEach((i) => (meta[i.ref] = { kind: "input", text: i.label || "" }));
    (graph.buttons || []).forEach((b) => (meta[b.ref] = { kind: "button", text: b.text || "" }));
    (graph.links || []).forEach((l) => (meta[l.ref] = { kind: "link", text: l.text || "" }));
    return domBoxes
      .filter((b) => meta[b.ref])
      .map((b) => ({ ref: b.ref, kind: meta[b.ref].kind, text: meta[b.ref].text, x: b.x, y: b.y, width: b.width, height: b.height }));
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function renderCard(result, encoded, maskedCount) {
    if (typeof addCard !== "function") return;
    const t = result.timingsMs || {};
    const listId = "vision-" + Date.now();
    const rows = (result.elements || [])
      .slice(0, 40)
      .map((e) => `<div>${esc(e.id)} · <b>${esc(e.type_guess)}</b> ${esc(e.label ? e.label + ": " : "")}${esc(e.text)}${e.domRef ? " <i>(" + esc(e.domRef) + ")</i>" : ""}</div>`)
      .join("");
    const card = addCard(`
      <div class="card-label">👁 Visual perception (on-device)</div>
      <img class="vision-thumb" src="${encoded.previewUrl}" alt="Masked screenshot sent to the local server" />
      <div class="vision-note">Exactly what left the browser: ${encoded.width}×${encoded.height} JPEG, ${maskedCount} sensitive region(s) blacked out locally.</div>
      <div>${esc(result.summary)}</div>
      <div class="vision-note">${(result.elements || []).length} elements · OCR ${t.ocr} ms · CV ${t.cv} ms · total ${t.total} ms (local CPU)</div>
      <button class="json-toggle" data-target="${listId}">View visual elements</button>
      <div class="json-preview hidden" id="${listId}">${rows}</div>
    `);
    const btn = card && card.querySelector(`[data-target="${listId}"]`);
    if (btn) btn.addEventListener("click", () => document.getElementById(listId).classList.toggle("hidden"));
  }

  async function augmentGraph(tabId, graph) {
    const sensitiveRefs = (graph.inputs || []).filter((i) => i.isSensitive).map((i) => i.ref);
    // Step 1 is mandatory: without mask rects we refuse to capture at all.
    const rects = await sendToTab(tabId, { type: "VISION_GET_MASK_RECTS", sensitiveRefs });
    const tab = await chrome.tabs.get(tabId);
    if (!tab.active) throw new Error("Tab is not visible - switch back to it for Visual mode.");
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const encoded = await maskAndEncode(dataUrl, rects.viewport, rects.maskRects);

    const result = await sendToBackground({
      type: "PERCEIVE",
      payload: {
        image: encoded.base64,
        viewport: rects.viewport,
        domElements: domElementsFrom(graph, rects.domBoxes),
        masked: true,
      },
    });
    renderCard(result, encoded, rects.maskRects.length);

    const visualElements = (result.elements || []).map((e) => ({
      id: e.id,
      type_guess: e.type_guess,
      text: e.text,
      label: e.label,
      bbox_css: e.bbox_css,
      domRef: e.domRef,
      confidence: e.confidence,
    }));
    return { ...graph, visualElements, visualSummary: result.summary };
  }

  function afterAction(tabId, action) {
    if (!action || !action.bbox || tabId == null) return;
    const label = `${action.action}${action.visualText ? ": " + action.visualText : ""} (${action.targetVisualId || ""})`;
    chrome.tabs.sendMessage(tabId, { type: "VISION_HIGHLIGHT", bbox: action.bbox, label, ref: action.targetRef || null }, () => void chrome.runtime.lastError);
  }

  function handlesAction(action) {
    return !!(action && action.targetVisualId && !action.targetRef && action.bbox);
  }

  function executeAction(tabId, action) {
    return sendToTab(tabId, { type: "VISION_ACT_AT", action: action.action, bbox: action.bbox });
  }

  function setEnabled(v) {
    enabled = !!v;
    const btn = document.getElementById("visualModeBtn");
    if (btn) {
      btn.classList.toggle("on", enabled);
      btn.title = enabled
        ? "Visual mode ON: a masked screenshot is analysed by the local server"
        : "Visual mode OFF: DOM-only (click to enable on-device visual perception)";
    }
    try {
      chrome.storage.local.set({ [STORAGE_KEY]: enabled });
    } catch (e) {
      /* storage unavailable - keep in-memory state */
    }
  }

  function mountToggle() {
    const style = document.createElement("style");
    style.textContent = `
      #visualModeBtn{border:1px solid #d4d4d8;background:transparent;border-radius:999px;padding:2px 8px;font-size:11px;cursor:pointer;margin-left:6px;color:inherit}
      #visualModeBtn.on{background:#7c3aed;color:#fff;border-color:#7c3aed}
      .vision-thumb{width:100%;border-radius:6px;border:1px solid #e4e4e7;margin:6px 0}
      .vision-note{font-size:11px;opacity:.75;margin:4px 0}`;
    document.head.appendChild(style);
    const host = document.querySelector(".composer-left");
    if (!host) return;
    const btn = document.createElement("button");
    btn.id = "visualModeBtn";
    btn.type = "button";
    btn.textContent = "👁 Visual";
    btn.addEventListener("click", () => setEnabled(!enabled));
    host.appendChild(btn);
    try {
      chrome.storage.local.get(STORAGE_KEY, (v) => setEnabled(!!(v && v[STORAGE_KEY])));
    } catch (e) {
      setEnabled(false);
    }
  }

  window.AivaVision = {
    isEnabled: () => enabled,
    augmentGraph,
    afterAction,
    handlesAction,
    executeAction,
    maskAndEncode,
  };

  mountToggle();
})();
