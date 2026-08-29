// content.js
// Runs in the page context on every tab. Everything in this file executes
// entirely on-device: detection, tokenization, and redaction all happen here,
// BEFORE anything is handed to the popup or the server. No raw field value
// this file reads ever leaves the file unless it has already been classified
// as non-sensitive.

(() => {
  const REF_ATTR = "data-pa-ref";
  const OVERLAY_CLASS = "pa-redaction-overlay";

  // ---------------------------------------------------------------------
  // 1. Local detection patterns
  // ---------------------------------------------------------------------

  // Checked in this order (most specific / longest first) so a 16-digit card
  // number can never be mistaken for a 12-digit Aadhaar, etc. Word boundaries
  // (\b) keep these from matching partway through a longer digit run.
  const PATTERNS = {
    CARD: /\b(?:\d[ -]?){13,19}\b/,
    AADHAAR: /\b\d{4}\s?\d{4}\s?\d{4}\b/,
    PAN: /\b[A-Za-z]{5}[0-9]{4}[A-Za-z]\b/,
    PHONE: /(?:\+?91[\s-]?)?[6-9]\d{9}\b/,
    EMAIL: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/,
  };

  // Label/name/placeholder hints used to classify a *structured* form field
  // before ever looking at its value. This is far more reliable than regex
  // sniffing alone on a real form (e.g. a blank required field has no value
  // to match against yet, but its label already tells us what it's for).
  const LABEL_HINTS = [
    { type: "OTP", re: /otp|one[\s-]?time[\s-]?code/i },
    { type: "ID_NUMBER", re: /aadhaar|aadhar/i },
    { type: "ID_NUMBER", re: /\bpan\b/i },
    { type: "EMAIL", re: /e[\s-]?mail/i },
    { type: "PHONE", re: /phone|mobile|contact\s*number/i },
    { type: "CARD", re: /card\s*(number)?|credit|debit/i },
    { type: "ADDRESS", re: /address/i },
    { type: "PERSON", re: /\bname\b/i },
  ];

  // Used ONLY for local classification (never sent anywhere) - deliberately
  // includes name/placeholder/id too, since e.g. placeholder="Enter your
  // Aadhaar number" is a genuinely useful signal for what a field is for.
  function getFieldHint(el) {
    const parts = [];
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) parts.push(lbl.textContent);
    }
    const wrappingLabel = el.closest("label");
    if (wrappingLabel) parts.push(wrappingLabel.textContent);
    if (el.getAttribute("aria-label")) parts.push(el.getAttribute("aria-label"));
    if (el.name) parts.push(el.name);
    if (el.placeholder) parts.push(el.placeholder);
    if (el.id) parts.push(el.id);
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }

  // Human-readable label actually sent in the screen graph. Deliberately
  // NARROWER than getFieldHint above: it only uses the real <label>/
  // aria-label text, never placeholder/name/id. A placeholder like
  // "e.g. priya@example.com" is a perfectly normal thing for a real form to
  // have, and it is NOT something that should leave the browser raw just
  // because it happened to be used to help classify the field. Whatever
  // text this does produce is still run through the same PII redaction as
  // field values, as a defense-in-depth backstop.
  function getDisplayLabel(el, counters) {
    let text = "";
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) text = lbl.textContent;
    }
    if (!text) {
      const wrappingLabel = el.closest("label");
      if (wrappingLabel) text = wrappingLabel.textContent;
    }
    if (!text && el.getAttribute("aria-label")) text = el.getAttribute("aria-label");
    if (!text) text = el.name || el.id || ""; // last resort - a technical name, not example content
    text = text.replace(/\s+/g, " ").trim();
    if (!text) return "";
    const { redactedText } = redactAllPII(text, counters);
    return redactedText;
  }

  // "username" is a login handle, not a legal name - don't let it trigger
  // PERSON classification, but let it still match other hints if relevant.
  function classifyByHint(hint) {
    const skipPerson = /user\s*name/i.test(hint);
    for (const { type, re } of LABEL_HINTS) {
      if (type === "PERSON" && skipPerson) continue;
      if (re.test(hint)) return type;
    }
    return null;
  }

  // Fallback for free text (e.g. a "comments" box) that has no useful label:
  // scan the actual value for PII shapes. Kept for reference/tests - the
  // real work now happens in redactAllPII below, which (unlike this) finds
  // every PII span in the text, not just the first one.
  function scanValueForPII(text) {
    if (!text) return null;
    if (PATTERNS.CARD.test(text) && text.replace(/\D/g, "").length >= 13) return "CARD";
    if (PATTERNS.AADHAAR.test(text) && text.replace(/\D/g, "").length === 12) return "ID_NUMBER";
    if (PATTERNS.PAN.test(text)) return "ID_NUMBER";
    if (PATTERNS.PHONE.test(text)) return "PHONE";
    if (PATTERNS.EMAIL.test(text)) return "EMAIL";
    return null;
  }

  // ---------------------------------------------------------------------
  // 2. Tokenization
  // ---------------------------------------------------------------------

  // PERSON_1, EMAIL_1, PHONE_1, ID_NUMBER_1, CARD_1, ADDRESS_1 - incrementing
  // per type so multiple instances on one page stay distinguishable.
  function numberedToken(type, counters) {
    counters[type] = (counters[type] || 0) + 1;
    return `${type}_${counters[type]}`;
  }

  // PASSWORD_FIELD / OTP_FIELD / HIDDEN_FIELD - fixed literal tokens; only
  // gets a numeric suffix from the 2nd occurrence onward.
  function literalToken(base, counters) {
    counters[base] = (counters[base] || 0) + 1;
    return counters[base] === 1 ? base : `${base}_${counters[base]}`;
  }

  // Finds and replaces EVERY PII span in a free-text value, not just the
  // first match - a single "comments" field can easily contain both an
  // email and a phone number in one sentence, and both must be redacted,
  // not just whichever pattern happens to be checked first.
  //
  // Patterns are tried most-specific-first (CARD, then AADHAAR, then PAN,
  // then PHONE, then EMAIL) and a match is only accepted if it doesn't
  // overlap a span a higher-priority pattern already claimed - this is what
  // stops a 16-digit card number from also being read as a 12-digit
  // Aadhaar number for part of itself.
  function redactAllPII(text, counters) {
    if (!text) return { redactedText: text, categoriesFound: {} };

    const order = ["CARD", "AADHAAR", "PAN", "PHONE", "EMAIL"];
    const claimed = [];
    const matches = [];

    for (const type of order) {
      const re = new RegExp(PATTERNS[type].source, "g");
      let m;
      while ((m = re.exec(text)) !== null) {
        const start = m.index;
        let end = start + m[0].length;
        // CARD's pattern repeats "digit + optional separator", so its last
        // repetition can greedily swallow a trailing space/dash that isn't
        // actually part of the number (e.g. the space before the next
        // word). Trim it back off the match so reconstructed text doesn't
        // lose that space.
        while (end > start && / |-/.test(text[end - 1])) end--;
        const digits = text.slice(start, end).replace(/\D/g, "").length;
        if (type === "CARD" && digits < 13) continue;
        if (type === "AADHAAR" && digits !== 12) continue;
        const overlaps = claimed.some(([cs, ce]) => start < ce && end > cs);
        if (!overlaps) {
          claimed.push([start, end]);
          matches.push({ start, end, type: type === "AADHAAR" || type === "PAN" ? "ID_NUMBER" : type });
        }
        if (m[0].length === 0) re.lastIndex++; // guard against zero-length matches
      }
    }

    if (matches.length === 0) return { redactedText: text, categoriesFound: {} };

    matches.sort((a, b) => a.start - b.start);
    const categoriesFound = {};
    let redactedText = "";
    let cursor = 0;
    for (const { start, end, type } of matches) {
      redactedText += text.slice(cursor, start);
      redactedText += numberedToken(type, counters);
      categoriesFound[type] = (categoriesFound[type] || 0) + 1;
      cursor = end;
    }
    redactedText += text.slice(cursor);

    return { redactedText, categoriesFound };
  }

  function truncate(str, max) {
    if (!str) return str;
    return str.length > max ? str.slice(0, max) + "…" : str;
  }

  // ---------------------------------------------------------------------
  // 3. Per-field analysis
  // ---------------------------------------------------------------------

  // Every branch returns the same shape: { isSensitive, categories, safeValue }.
  // `categories` maps type -> count (usually just one entry, but a free-text
  // fallback match can legitimately produce several, e.g. {EMAIL:1, PHONE:1}
  // for one "comments" box). `safeValue` is always what's safe to send -
  // a bare token for a whole-field match, or the original text with each
  // PII span swapped for its token when only part of the value was sensitive.
  function analyzeField(el, type, hint, counters) {
    if (type === "password") {
      const token = literalToken("PASSWORD_FIELD", counters);
      return { isSensitive: true, categories: { PASSWORD_FIELD: 1 }, safeValue: token };
    }

    const isHidden =
      type === "hidden" ||
      getComputedStyle(el).display === "none" ||
      getComputedStyle(el).visibility === "hidden";
    if (isHidden) {
      const token = literalToken("HIDDEN_FIELD", counters);
      return { isSensitive: true, categories: { HIDDEN_FIELD: 1 }, safeValue: token };
    }

    const hintType = classifyByHint(hint);
    if (hintType === "OTP") {
      const token = literalToken("OTP_FIELD", counters);
      return { isSensitive: true, categories: { OTP_FIELD: 1 }, safeValue: token };
    }
    if (hintType) {
      const token = numberedToken(hintType, counters);
      return { isSensitive: true, categories: { [hintType]: 1 }, safeValue: token };
    }

    // No structural hint - fall back to scanning the raw value for any
    // number of embedded PII spans.
    const rawValue = readValue(el, type);
    const { redactedText, categoriesFound } = redactAllPII(rawValue, counters);
    if (Object.keys(categoriesFound).length > 0) {
      return { isSensitive: true, categories: categoriesFound, safeValue: redactedText };
    }

    return { isSensitive: false, categories: {}, safeValue: truncate(rawValue, 60) };
  }

  function readValue(el, type) {
    if (type === "select") {
      const opt = el.options && el.options[el.selectedIndex];
      return opt ? opt.text : "";
    }
    if (type === "checkbox" || type === "radio") {
      return el.checked ? "checked" : "unchecked";
    }
    return el.value || "";
  }

  // ---------------------------------------------------------------------
  // 4. Visual redaction overlay
  // ---------------------------------------------------------------------

  function clearRedactionOverlays() {
    document.querySelectorAll("." + OVERLAY_CLASS).forEach((o) => o.remove());
  }

  function approxRect(el) {
    const r = el.getBoundingClientRect();
    const round5 = (n) => Math.round(n / 5) * 5;
    return { x: round5(r.left), y: round5(r.top), width: round5(r.width), height: round5(r.height) };
  }

  function applyRedactionOverlay(el, token) {
    const rect = el.getBoundingClientRect();
    const overlay = document.createElement("div");
    overlay.className = OVERLAY_CLASS;
    overlay.textContent = "🔒 " + token;
    overlay.dataset.paFor = el.getAttribute(REF_ATTR);
    Object.assign(overlay.style, {
      position: "fixed",
      left: rect.left + "px",
      top: rect.top + "px",
      width: rect.width + "px",
      height: rect.height + "px",
      background:
        "repeating-linear-gradient(45deg, rgba(20,20,20,0.9), rgba(20,20,20,0.9) 8px, rgba(70,70,70,0.9) 8px, rgba(70,70,70,0.9) 16px)",
      color: "#fff",
      fontSize: "11px",
      fontFamily: "monospace",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      borderRadius: "4px",
      border: "1px solid #ffcc00",
      zIndex: 2147483647,
      pointerEvents: "none",
    });
    document.body.appendChild(overlay);
  }

  function repositionOverlays() {
    document.querySelectorAll("." + OVERLAY_CLASS).forEach((overlay) => {
      const ref = overlay.dataset.paFor;
      const el = document.querySelector(`[${REF_ATTR}="${ref}"]`);
      if (!el) {
        overlay.remove();
        return;
      }
      const rect = el.getBoundingClientRect();
      overlay.style.left = rect.left + "px";
      overlay.style.top = rect.top + "px";
      overlay.style.width = rect.width + "px";
      overlay.style.height = rect.height + "px";
    });
  }
  window.addEventListener("scroll", repositionOverlays, true);
  window.addEventListener("resize", repositionOverlays);

  // ---------------------------------------------------------------------
  // 5. Screen graph builder
  // ---------------------------------------------------------------------

  function collectFields(counters) {
    const els = Array.from(document.querySelectorAll("input, textarea, select"));
    const detectedTypes = {};
    const inputs = [];
    let sensitiveCount = 0;

    els.forEach((el, i) => {
      const ref = `input-${i}`;
      el.setAttribute(REF_ATTR, ref);
      const type =
        el.tagName.toLowerCase() === "select"
          ? "select"
          : el.tagName.toLowerCase() === "textarea"
          ? "textarea"
          : el.type || "text";
      const hint = getFieldHint(el); // classification only - never leaves this function
      const result = analyzeField(el, type, hint, counters);
      const displayLabel = getDisplayLabel(el, counters); // safe to send - already redacted

      if (result.isSensitive) {
        sensitiveCount++;
        for (const [category, count] of Object.entries(result.categories)) {
          detectedTypes[category] = (detectedTypes[category] || 0) + count;
        }
        applyRedactionOverlay(el, truncate(result.safeValue, 40));
      }

      inputs.push({
        ref,
        type,
        label: displayLabel ? truncate(displayLabel, 60) : null,
        required: !!el.required,
        isSensitive: result.isSensitive,
        sanitizedValue: result.safeValue,
        position: approxRect(el),
      });
    });

    const forms = Array.from(document.querySelectorAll("form")).map((f, i) => ({
      ref: `form-${i}`,
      fieldRefs: Array.from(f.querySelectorAll("input, textarea, select"))
        .map((el) => el.getAttribute(REF_ATTR))
        .filter(Boolean),
    }));

    return { forms, inputs, sensitiveCount, detectedTypes };
  }

  // Button/link text is redacted the same way field values are - a "Log out,
  // priya@example.com" style button on a real page is exactly as much a
  // leak risk as a form field, and there's no reason to trust it just
  // because it isn't an <input>.
  function collectButtons(counters) {
    const buttons = [];
    const detectedTypes = {};
    let sensitiveCount = 0;
    document.querySelectorAll('button, input[type="submit"], input[type="button"]').forEach((el, i) => {
      const ref = `button-${i}`;
      el.setAttribute(REF_ATTR, ref);
      const rawText = (el.textContent || el.value || "").trim();
      const { redactedText, categoriesFound } = redactAllPII(rawText, counters);
      if (Object.keys(categoriesFound).length > 0) {
        sensitiveCount++;
        for (const [cat, count] of Object.entries(categoriesFound)) {
          detectedTypes[cat] = (detectedTypes[cat] || 0) + count;
        }
      }
      buttons.push({ ref, text: truncate(redactedText, 60), position: approxRect(el) });
    });
    return { buttons, detectedTypes, sensitiveCount };
  }

  function collectLinks(counters) {
    const links = [];
    const detectedTypes = {};
    let sensitiveCount = 0;
    document.querySelectorAll("a[href]").forEach((el, i) => {
      const ref = `link-${i}`;
      el.setAttribute(REF_ATTR, ref);
      let hrefDomain = "";
      try {
        hrefDomain = new URL(el.href, location.href).hostname;
      } catch (e) {
        /* ignore malformed hrefs (e.g. javascript:void(0)) */
      }
      const rawText = (el.textContent || "").trim();
      const { redactedText, categoriesFound } = redactAllPII(rawText, counters);
      if (Object.keys(categoriesFound).length > 0) {
        sensitiveCount++;
        for (const [cat, count] of Object.entries(categoriesFound)) {
          detectedTypes[cat] = (detectedTypes[cat] || 0) + count;
        }
      }
      links.push({ ref, text: truncate(redactedText, 60), hrefDomain, position: approxRect(el) });
    });
    return { links, detectedTypes, sensitiveCount };
  }

  function buildScreenGraph() {
    clearRedactionOverlays();

    // One shared counter pool for the whole scan, so EMAIL_1/EMAIL_2/...
    // stay unique across fields, buttons, links, and the page title alike -
    // not just within each collector separately.
    const counters = {};
    const fields = collectFields(counters);
    const btns = collectButtons(counters);
    const lnks = collectLinks(counters);
    const { redactedText: pageTitle } = redactAllPII(document.title, counters);

    const detectedTypes = {};
    let sensitiveItemsCount = 0;
    for (const part of [fields, btns, lnks]) {
      sensitiveItemsCount += part.sensitiveCount;
      for (const [category, count] of Object.entries(part.detectedTypes)) {
        detectedTypes[category] = (detectedTypes[category] || 0) + count;
      }
    }

    // Only the domain is included, never the full URL (query strings/paths
    // can carry session tokens, IDs, or search terms).
    return {
      pageTitle: pageTitle || document.title, // redactAllPII returns the original text unchanged when nothing matched
      domain: location.hostname,
      scannedAt: new Date().toISOString(),
      forms: fields.forms,
      inputs: fields.inputs,
      buttons: btns.buttons,
      links: lnks.links,
      sensitiveItemsCount,
      detectedTypes,
    };
  }

  // ---------------------------------------------------------------------
  // 6. Action execution (server tells us *what*, never touches the page)
  // ---------------------------------------------------------------------

  function showSummaryBanner(text) {
    const existing = document.getElementById("pa-summary-banner");
    if (existing) existing.remove();

    const banner = document.createElement("div");
    banner.id = "pa-summary-banner";
    banner.textContent = "🤖 " + text;
    Object.assign(banner.style, {
      position: "fixed",
      top: "12px",
      left: "50%",
      transform: "translateX(-50%)",
      maxWidth: "560px",
      background: "#111827",
      color: "#e5e7eb",
      padding: "12px 18px",
      borderRadius: "10px",
      border: "1px solid #374151",
      boxShadow: "0 10px 30px rgba(0,0,0,0.35)",
      fontFamily: "sans-serif",
      fontSize: "13px",
      lineHeight: "1.4",
      zIndex: 2147483647,
      cursor: "pointer",
    });
    banner.title = "Click to dismiss";
    banner.onclick = () => banner.remove();
    document.body.appendChild(banner);
    setTimeout(() => banner.remove(), 12000);
  }

  function executeAction(action) {
    if (!action || !action.action) return { ok: false, error: "No action provided." };

    switch (action.action) {
      case "click": {
        const el = document.querySelector(`[${REF_ATTR}="${action.targetRef}"]`);
        if (!el) return { ok: false, error: "Target not found: " + action.targetRef };
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.click();
        return { ok: true, message: "Clicked " + action.targetRef };
      }
      case "focus": {
        const el = document.querySelector(`[${REF_ATTR}="${action.targetRef}"]`);
        if (!el) return { ok: false, error: "Target not found: " + action.targetRef };
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.focus();
        return { ok: true, message: "Focused " + action.targetRef };
      }
      case "scroll": {
        const amount = action.direction === "up" ? -400 : 400;
        window.scrollBy({ top: amount, behavior: "smooth" });
        return { ok: true, message: "Scrolled " + (action.direction || "down") };
      }
      case "summarize": {
        showSummaryBanner(action.summary || "No summary provided.");
        return { ok: true, message: "Summary displayed on page." };
      }
      default:
        return { ok: false, error: "Unknown action: " + action.action };
    }
  }

  // ---------------------------------------------------------------------
  // 7. Message bridge to the popup
  // ---------------------------------------------------------------------

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "SCAN_PAGE") {
      sendResponse({ ok: true, graph: buildScreenGraph() });
      return true;
    }
    if (msg.type === "EXECUTE_ACTION") {
      sendResponse(executeAction(msg.action));
      return true;
    }
    return false;
  });
})();
