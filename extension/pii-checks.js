// pii-checks.js
// Pure PII detection + tokenisation, no DOM access. Loaded as a content
// script before content.js (exposes globalThis.AivaPII) and required
// directly by the Node unit tests (module.exports).
//
// Mirrors server/pii_checks.py - same patterns, same validators, same
// two-pass overlap rule. Change both together.
//
// Policy: every candidate of the right SHAPE is tokenised here (over-
// redaction is safe). The server only hard-rejects VALIDATED items
// (Aadhaar with a good Verhoeff digit, cards passing Luhn, strict PAN,
// phone, email), so a random 12-digit order number is not a reason to
// reject a request.

(function (root) {
  "use strict";

  const VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
  ];
  const VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
  ];
  const VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9];

  const digitsOf = (s) => String(s || "").replace(/\D/g, "");

  function verhoeffValid(number) {
    const d = digitsOf(number);
    if (!d) return false;
    let c = 0;
    const rev = d.split("").reverse();
    for (let i = 0; i < rev.length; i++) {
      c = VERHOEFF_D[c][VERHOEFF_P[i % 8][Number(rev[i])]];
    }
    return c === 0;
  }

  function verhoeffCheckDigit(number) {
    let c = 0;
    const rev = digitsOf(number).split("").reverse();
    for (let i = 0; i < rev.length; i++) {
      c = VERHOEFF_D[c][VERHOEFF_P[(i + 1) % 8][Number(rev[i])]];
    }
    return String(VERHOEFF_INV[c]);
  }

  function luhnValid(number) {
    const d = digitsOf(number);
    if (!d) return false;
    let total = 0;
    const rev = d.split("").reverse();
    for (let i = 0; i < rev.length; i++) {
      let n = Number(rev[i]);
      if (i % 2 === 1) {
        n *= 2;
        if (n > 9) n -= 9;
      }
      total += n;
    }
    return total % 10 === 0;
  }

  const PAN_STRICT = /^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$/;
  const panValid = (v) => PAN_STRICT.test(String(v || "").toUpperCase());

  function aadhaarValid(v) {
    const d = digitsOf(v);
    return d.length === 12 && /^[2-9]/.test(d) && verhoeffValid(d);
  }

  function cardValid(v) {
    const d = digitsOf(v);
    return d.length >= 13 && d.length <= 19 && luhnValid(d);
  }

  // Order = priority. Must stay identical to CANDIDATE_PATTERNS in
  // server/pii_checks.py.
  const CANDIDATE_PATTERNS = [
    ["EMAIL", /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g],
    ["CARD", /(?<!\d)(?<!\d[ -])\d(?:[ -]?\d){12,18}(?![ -]?\d)/g],
    ["AADHAAR", /(?<!\d)(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)/g],
    ["PAN", /(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])/g],
    ["PHONE", /(?<![\d+])(?:\+?91[ -]?)?[6-9]\d{4}[ -]?\d{5}(?![ -]?\d)/g],
  ];

  const VALIDATORS = {
    EMAIL: () => true,
    CARD: cardValid,
    AADHAAR: aadhaarValid,
    PAN: panValid,
    PHONE: () => true,
  };

  // All non-overlapping PII spans, sorted by position. Pass 1 takes
  // validated matches, pass 2 adds unvalidated candidates that don't overlap.
  function detectPII(text) {
    if (!text) return [];
    const found = [];
    for (const [type, re] of CANDIDATE_PATTERNS) {
      const g = new RegExp(re.source, "g");
      let m;
      while ((m = g.exec(text)) !== null) {
        found.push({ type, start: m.index, end: m.index + m[0].length, validated: !!VALIDATORS[type](m[0]) });
        if (m[0].length === 0) g.lastIndex++;
      }
    }
    const claimed = [];
    for (const wantValidated of [true, false]) {
      for (const item of found) {
        if (item.validated !== wantValidated) continue;
        if (claimed.some((c) => item.start < c.end && item.end > c.start)) continue;
        claimed.push(item);
      }
    }
    claimed.sort((a, b) => a.start - b.start);
    return claimed;
  }

  // Token category shown to the server/LLM. Aadhaar and PAN share one
  // ID_NUMBER category (as before), the rest keep their own name.
  const tokenCategory = (type) => (type === "AADHAAR" || type === "PAN" ? "ID_NUMBER" : type);

  function numberedToken(type, counters) {
    counters[type] = (counters[type] || 0) + 1;
    return `${type}_${counters[type]}`;
  }

  // Replaces EVERY candidate span with a numbered token (PHONE_1, EMAIL_2...).
  function redactAllPII(text, counters) {
    counters = counters || {};
    if (!text) return { redactedText: text, categoriesFound: {} };
    let matches = detectPII(text);

    // --- feature/ner: client-side NAME/ADDRESS rule pass (ner-rules.js) ---
    // A name/address span wins over any regex match it overlaps (the whole
    // span is redacted, which is strictly more redaction, never less).
    // ner-rules.js is loaded before content.js and sets root.AivaNerRules.
    if (root.AivaNerRules) {
      for (const s of root.AivaNerRules.detect(text)) {
        matches = matches.filter((m) => !(s.start < m.end && s.end > m.start));
        matches.push({ start: s.start, end: s.end, type: s.type });
      }
      matches.sort((a, b) => a.start - b.start);
    }
    // --- end feature/ner ---

    if (matches.length === 0) return { redactedText: text, categoriesFound: {} };
    const categoriesFound = {};
    let redactedText = "";
    let cursor = 0;
    for (const { start, end, type } of matches) {
      const cat = tokenCategory(type);
      redactedText += text.slice(cursor, start) + numberedToken(cat, counters);
      categoriesFound[cat] = (categoriesFound[cat] || 0) + 1;
      cursor = end;
    }
    redactedText += text.slice(cursor);
    return { redactedText, categoriesFound };
  }

  // First category found in a value, or null (kept for callers that only
  // need a yes/no classification).
  function scanValueForPII(text) {
    const m = detectPII(text);
    return m.length ? tokenCategory(m[0].type) : null;
  }

  const api = {
    verhoeffValid,
    verhoeffCheckDigit,
    luhnValid,
    panValid,
    aadhaarValid,
    cardValid,
    detectPII,
    redactAllPII,
    scanValueForPII,
    numberedToken,
    tokenCategory,
    CANDIDATE_PATTERNS,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.AivaPII = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
