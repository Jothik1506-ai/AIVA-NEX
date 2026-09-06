// memory.js
// The structured, per-fact memory store - replaces the old flat `localProfile`
// object (one blob, one chrome.storage.local key, no metadata) with normalized
// fact objects carrying category, sensitivity, provenance, and lifecycle.
// See docs/MEMORY-ARCHITECTURE-PLAN.md for the full design (§5-§8).
//
// Everything here runs in the popup's JS context (loaded before popup.js) and
// talks only to chrome.storage.local - nothing in this file makes a network
// call. Exposed as a single global, AivaMemory, to keep the buildless/no-module
// setup this project deliberately uses (see README) - not a bare global per
// function, but the same named operations the plan calls for as methods.

const AivaMemory = (() => {
  const STORAGE_KEY = "aivaMemory";

  // -------------------------------------------------------------------
  // Sensitivity classification (plan §6)
  // -------------------------------------------------------------------
  // Mirrors the field-type-first, then-label-hint approach content.js already
  // uses for page fields (PATTERNS/LABEL_HINTS) - here it's category-first
  // (the caller states what kind of fact this is) with a value-shape check as
  // a defense-in-depth backstop, the same "don't just trust one signal"
  // posture the server's find_raw_pii() re-check already uses.

  // Category name fragments that mean "never persist this, full stop" -
  // regardless of what the caller says the value is. Checked against the
  // category string, not the value, because these are things that should
  // never even be OFFERED a "remember this?" prompt.
  // No \b around "otp" on purpose - matches content.js's own LABEL_HINTS
  // convention (OTP: /otp|one[\s-]?time[\s-]?code/i, also unbounded), and a
  // \b would fail to match a compound category name like "form.otpCode"
  // (no word-boundary between "otp" and the following "C").
  const NEVER_STORE_CATEGORY_HINTS = [
    /password/i,
    /otp/i,
    /one[\s-]?time[\s-]?code/i,
    /\bcvv2?\b/i,
    /security[\s-]?code/i,
    /auth[\s-]?code/i,
    /\btoken\b/i,
    /session/i,
    /secret/i,
    /private[\s-]?key/i,
    /api[\s-]?key/i,
    /security[\s-]?(question|answer)/i,
  ];

  // Value-shape backstop: even if a category slips through unlabeled (e.g. a
  // caller passes category "misc.note" for something that is actually a
  // password), refuse anything that looks like a JWT or an obviously
  // secret-shaped token. This is deliberately narrow - it is a backstop, not
  // the primary defense, exactly like the server's raw-PII re-check.
  const NEVER_STORE_VALUE_SHAPES = [
    /^eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+$/, // JWT-shaped
  ];

  // Category prefixes that are Sensitive per the plan's examples (full home
  // address, relationship contacts' details, government IDs, financial,
  // medical). governmentId/financial/medical additionally require the
  // stronger second confirmation the plan calls for at the Sensitive/
  // Never-Store boundary (requiresStrongConfirmation).
  const SENSITIVE_CATEGORY_RULES = [
    { re: /^relationship\./i, strong: false },
    { re: /^identity\.governmentid\./i, strong: true },
    { re: /^financial\./i, strong: true },
    { re: /^medical\./i, strong: true },
    { re: /^contact\.address\b/i, strong: false },
  ];

  // Category prefixes that are Normal (no confirmation beyond the standard
  // remember-prompt, and never masked in the UI).
  const NORMAL_CATEGORY_RULES = [
    /^preferences\./i,
    /^identity\.preferredname$/i,
    /^identity\.nickname$/i,
  ];

  /**
   * Classify a (category, value) pair per the plan's four-tier system.
   * Returns { level: "normal"|"personal"|"sensitive", requiresStrongConfirmation: bool }
   * or throws for "never_store" - a caller MUST NOT silently swallow this and
   * proceed, matching §6: "the agent should refuse and explain why, not
   * silently drop it."
   */
  function classifySensitivity(category, value) {
    const cat = String(category || "");

    if (NEVER_STORE_CATEGORY_HINTS.some((re) => re.test(cat))) {
      throw new MemoryRefusedError(
        `"${category}" looks like a credential/secret (password, OTP, CVV, token, key, or security answer). ` +
          "Aiva Nex Agent never stores this kind of value, even on explicit request."
      );
    }
    if (typeof value === "string" && NEVER_STORE_VALUE_SHAPES.some((re) => re.test(value.trim()))) {
      throw new MemoryRefusedError(
        "That value looks like a secret/session token by its shape, not its label. Refusing to store it."
      );
    }

    for (const rule of SENSITIVE_CATEGORY_RULES) {
      if (rule.re.test(cat)) {
        return { level: "sensitive", requiresStrongConfirmation: rule.strong };
      }
    }
    if (NORMAL_CATEGORY_RULES.some((re) => re.test(cat))) {
      return { level: "normal", requiresStrongConfirmation: false };
    }
    // Default: most identity/contact/professional facts (email, phone, name,
    // job title, company) are Personal - stored on request, no special
    // handling, per §6's Personal row.
    return { level: "personal", requiresStrongConfirmation: false };
  }

  /** Thrown by classifySensitivity/createFact for a never_store category or shape. */
  class MemoryRefusedError extends Error {
    constructor(message) {
      super(message);
      this.name = "MemoryRefusedError";
    }
  }

  // -------------------------------------------------------------------
  // Storage helpers - one array under one chrome.storage.local key
  // (plan §5.3). No query API exists on chrome.storage, so filtering
  // happens in memory once the (small) array is loaded.
  // -------------------------------------------------------------------

  function readAll() {
    return new Promise((resolve) => {
      if (!chrome.storage || !chrome.storage.local) {
        resolve([]);
        return;
      }
      chrome.storage.local.get([STORAGE_KEY], (res) => {
        resolve((res && Array.isArray(res[STORAGE_KEY])) ? res[STORAGE_KEY] : []);
      });
    });
  }

  function writeAll(facts) {
    return new Promise((resolve) => {
      if (!chrome.storage || !chrome.storage.local) {
        resolve();
        return;
      }
      chrome.storage.local.set({ [STORAGE_KEY]: facts }, () => resolve());
    });
  }

  function makeId() {
    return "mem_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 8);
  }

  function nowIso() {
    return new Date().toISOString();
  }

  // -------------------------------------------------------------------
  // Public operations (plan §8.2 lifecycle)
  // -------------------------------------------------------------------

  /**
   * Create a new fact. Throws MemoryRefusedError for never_store categories/
   * value shapes (per classifySensitivity) - callers must catch this and
   * show the refusal to the user, never swallow it (§6).
   *
   * opts: { category, key, value, aliases, source, consent, domainScope }
   * consent "once" facts are classified (so the caller can still show the
   * right confirmation copy) but are NEVER written to storage - they're
   * handed back as a plain in-memory object for the current action only.
   */
  async function createFact(opts) {
    const { category, key, value, aliases, source, consent, domainScope } = opts || {};
    if (!category || !key) throw new Error("createFact requires both category and key.");

    const classification = classifySensitivity(category, value); // throws on never_store

    const fact = {
      id: makeId(),
      category,
      key,
      value,
      aliases: Array.isArray(aliases) ? aliases : [],
      sensitivity: classification.level,
      requiresStrongConfirmation: !!classification.requiresStrongConfirmation,
      source: source || "user_explicit",
      confidence: 1.0,
      consent: consent === "once" ? "once" : "persistent",
      domainScope: domainScope || null,
      createdAt: nowIso(),
      updatedAt: nowIso(),
      lastUsedAt: null,
      usageCount: 0,
      supersededBy: null,
    };

    if (fact.consent === "once") {
      // Never touches chrome.storage.local - lives only in the caller's hands
      // for the duration of the current action (§5.1 "consent").
      return fact;
    }

    const all = await readAll();
    all.push(fact);
    await writeAll(all);
    return fact;
  }

  /**
   * Get non-superseded facts for a category (exact match) or a category
   * prefix (pass matchPrefix: true) - e.g. getFactsByCategory("relationship.father", true)
   * returns every relationship.father.* fact.
   */
  async function getFactsByCategory(category, matchPrefix) {
    const all = await readAll();
    const live = all.filter((f) => f.supersededBy == null);
    if (!category) return live;
    return live.filter((f) =>
      matchPrefix ? f.category === category || f.category.startsWith(category + ".") : f.category === category
    );
  }

  /**
   * Find a live fact by category+key, also checking aliases against a raw
   * phrase (e.g. "dad's number") - used by retrieval (plan §7) so a fact
   * saved under one phrasing is still found under another.
   */
  async function findFact(category, key, phrase) {
    const all = await readAll();
    const live = all.filter((f) => f.supersededBy == null);
    const exact = live.find((f) => f.category === category && f.key === key);
    if (exact) return exact;
    if (phrase) {
      const p = phrase.toLowerCase();
      return live.find((f) => (f.aliases || []).some((a) => a.toLowerCase() === p)) || null;
    }
    return null;
  }

  /**
   * Update a fact by creating a new one with the new value and pointing the
   * old fact's supersededBy at it (plan §8.2 "Update") - the old record is
   * kept, not deleted, so history stays answerable, while getFactsByCategory/
   * findFact only ever return the current (non-superseded) record.
   */
  async function updateFact(oldFactId, newValue, opts) {
    const all = await readAll();
    const old = all.find((f) => f.id === oldFactId);
    if (!old) throw new Error(`updateFact: no fact with id ${oldFactId}`);

    const classification = classifySensitivity(old.category, newValue); // throws on never_store
    const replacement = {
      ...old,
      id: makeId(),
      value: newValue,
      sensitivity: classification.level,
      requiresStrongConfirmation: !!classification.requiresStrongConfirmation,
      source: (opts && opts.source) || old.source,
      createdAt: nowIso(),
      updatedAt: nowIso(),
      lastUsedAt: null,
      usageCount: 0,
      supersededBy: null,
    };

    old.supersededBy = replacement.id;
    old.updatedAt = nowIso();
    all.push(replacement);
    await writeAll(all);
    return replacement;
  }

  /**
   * Hard delete - plan §8.2 draws an explicit line between Update
   * (supersede, keep history) and Delete ("forget my X" removes it
   * entirely). Deletes every fact with this id, wherever it sits in the
   * supersession chain.
   */
  async function deleteFact(factId) {
    const all = await readAll();
    const next = all.filter((f) => f.id !== factId);
    const removed = next.length !== all.length;
    await writeAll(next);
    return removed;
  }

  async function listAllFacts(includeSuperseded) {
    const all = await readAll();
    return includeSuperseded ? all : all.filter((f) => f.supersededBy == null);
  }

  /** Record that a fact was actually used (for lastUsedAt/usageCount). */
  async function recordUsage(factId) {
    const all = await readAll();
    const fact = all.find((f) => f.id === factId);
    if (!fact) return;
    fact.lastUsedAt = nowIso();
    fact.usageCount = (fact.usageCount || 0) + 1;
    await writeAll(all);
  }

  async function clearAll() {
    await writeAll([]);
  }

  // -------------------------------------------------------------------
  // Export / Import (plan §5.4) - Markdown is a human-readable export
  // format, never the source of truth (chrome.storage.local is).
  // -------------------------------------------------------------------

  const CATEGORY_SECTION_TITLES = {
    identity: "Identity",
    contact: "Contact",
    professional: "Professional",
    relationship: "Relationships",
    preferences: "Preferences",
  };

  function topLevelCategory(category) {
    return String(category).split(".")[0];
  }

  function titleCaseKey(key) {
    return String(key)
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/^./, (c) => c.toUpperCase());
  }

  /**
   * Produce the Markdown export (plan §5.4). Sensitive-tier facts are
   * excluded unless includeSensitive is explicitly true - exporting is not
   * an implicit "yes, include everything."
   */
  async function exportMemory(includeSensitive) {
    const facts = await listAllFacts(false);
    const eligible = facts.filter((f) => includeSensitive || f.sensitivity !== "sensitive");

    const byTop = {};
    eligible.forEach((f) => {
      const top = topLevelCategory(f.category);
      (byTop[top] = byTop[top] || []).push(f);
    });

    const lines = ["# Aiva Nex Agent — Memory Export", `Exported: ${nowIso()} · Version: 1`, ""];

    Object.keys(byTop).forEach((top) => {
      lines.push(`## ${CATEGORY_SECTION_TITLES[top] || titleCaseKey(top)}`, "");
      // Group relationship facts by the person (relationship.father.phone -> "Father")
      if (top === "relationship") {
        const byPerson = {};
        byTop[top].forEach((f) => {
          const parts = f.category.split(".");
          const person = parts[1] || "other";
          (byPerson[person] = byPerson[person] || []).push(f);
        });
        Object.keys(byPerson).forEach((person) => {
          lines.push(`### ${titleCaseKey(person)}`);
          byPerson[person].forEach((f) => {
            lines.push(`- **${titleCaseKey(f.key)}:** ${f.value} _(${f.source}, added ${f.createdAt.slice(0, 10)})_`);
          });
          lines.push("");
        });
      } else {
        byTop[top].forEach((f) => {
          lines.push(`- **${titleCaseKey(f.key)}:** ${f.value} _(${f.source}, added ${f.createdAt.slice(0, 10)})_`);
        });
        lines.push("");
      }
    });

    lines.push(
      "---",
      "Sensitive fields (government IDs, financial account numbers) are",
      "included in this export only if you explicitly chose \"include sensitive",
      "data\" when exporting. Handle this file the same way you'd handle a",
      "password export — it's your data in plain text."
    );

    return lines.join("\n");
  }

  /**
   * Parse a Markdown export back into fact objects and create them via
   * createFact - deliberately reuses the normal create path (source:
   * "import") rather than a bulk-trust bypass, per §8.2 "Import goes
   * through the same Create confirmation flow."
   *
   * Returns { created: Fact[], refused: {line, reason}[] } so the caller can
   * show the user what was skipped and why.
   */
  async function importMemory(markdownText) {
    const created = [];
    const refused = [];

    let currentTop = null;
    let currentPerson = null;

    const lines = String(markdownText || "").split(/\r?\n/);
    for (const rawLine of lines) {
      const line = rawLine.trim();
      const h2 = line.match(/^##\s+(.+)$/);
      const h3 = line.match(/^###\s+(.+)$/);
      const item = line.match(/^-\s+\*\*(.+?):\*\*\s*(.+?)(?:\s+_\(.*\))?$/);

      if (h2) {
        const title = h2[1].toLowerCase();
        currentTop =
          Object.keys(CATEGORY_SECTION_TITLES).find((k) => CATEGORY_SECTION_TITLES[k].toLowerCase() === title) ||
          title.replace(/\s+/g, "");
        currentPerson = null;
        continue;
      }
      if (h3) {
        currentPerson = h3[1].toLowerCase().replace(/\s+/g, "");
        continue;
      }
      if (item && currentTop) {
        const key = item[1].trim().replace(/\s+(.)/g, (_, c) => c.toUpperCase()).replace(/^./, (c) => c.toLowerCase());
        const value = item[2].trim();
        const category =
          currentTop === "relationship" && currentPerson ? `relationship.${currentPerson}.${key}` : `${currentTop}.${key}`;
        try {
          const fact = await createFact({ category, key, value, source: "import", consent: "persistent" });
          created.push(fact);
        } catch (err) {
          refused.push({ line: rawLine, reason: err.message });
        }
      }
    }

    return { created, refused };
  }

  return {
    MemoryRefusedError,
    classifySensitivity,
    createFact,
    getFactsByCategory,
    findFact,
    updateFact,
    deleteFact,
    listAllFacts,
    recordUsage,
    clearAll,
    exportMemory,
    importMemory,
  };
})();
