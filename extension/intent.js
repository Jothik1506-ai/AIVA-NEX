// intent.js
// Small deterministic intent parser for the side-panel chat. Runs BEFORE any
// server/LLM call so plain commands ("search X", "search X on amazon",
// "summarize this page", "scroll down", "click Sign in", "fill form") are
// handled locally and predictably. Anything it doesn't recognise returns
// { type: "chat" } and goes to the existing /chat path.
// Pure (no DOM, no chrome.*): loaded by popup.html (globalThis.AivaIntent)
// and required directly by the Node unit tests (module.exports).

(function (root) {
  // Known site-search URL patterns. `hosts` are the domains (without "www.")
  // that resolve to this entry; the first one is used for a bare name.
  const SITES = {
    google: { hosts: ["google.com", "google.co.in"], path: "/search?q=" },
    amazon: { hosts: ["amazon.in", "amazon.com", "amazon.co.uk"], path: "/s?k=" },
    flipkart: { hosts: ["flipkart.com"], path: "/search?q=" },
    youtube: { hosts: ["youtube.com"], path: "/results?search_query=" },
    wikipedia: { hosts: ["en.wikipedia.org", "wikipedia.org"], path: "/wiki/Special:Search?search=" },
    github: { hosts: ["github.com"], path: "/search?q=" },
    ebay: { hosts: ["ebay.com", "ebay.in"], path: "/sch/i.html?_nkw=" },
    reddit: { hosts: ["reddit.com"], path: "/search/?q=" },
    stackoverflow: { hosts: ["stackoverflow.com"], path: "/search?q=" },
    bing: { hosts: ["bing.com"], path: "/search?q=" },
    duckduckgo: { hosts: ["duckduckgo.com"], path: "/?q=" },
  };
  const ALIASES = { yt: "youtube", wiki: "wikipedia", "stack overflow": "stackoverflow", ddg: "duckduckgo" };

  const DOMAIN_RE = /^(?:https?:\/\/)?((?:[a-z0-9-]+\.)+[a-z]{2,})\/?$/i;

  function googleUrl(q) {
    return "https://www.google.com/search?q=" + encodeURIComponent(q);
  }

  /** Resolves "amazon", "Amazon.in", "www.github.com" -> {name, host, path|null};
   * null if the text is neither a known site name nor domain-shaped. */
  function resolveSite(text) {
    const t = String(text || "").trim().toLowerCase();
    if (!t) return null;
    const name = ALIASES[t] || t;
    if (SITES[name]) return { name, host: SITES[name].hosts[0], path: SITES[name].path };
    const m = t.match(DOMAIN_RE);
    if (!m) return null;
    const host = m[1].replace(/^www\./, "");
    for (const [key, s] of Object.entries(SITES)) {
      if (s.hosts.includes(host)) return { name: key, host, path: s.path };
    }
    return { name: host, host, path: null };
  }

  function siteSearchUrl(site, q) {
    if (site.path) return "https://" + (site.host.includes(".") && site.host.split(".").length === 2 ? "www." + site.host : site.host) + site.path + encodeURIComponent(q);
    return googleUrl("site:" + site.host + " " + q);
  }

  /** Only http/https URLs may be opened from chat. */
  function isSafeUrl(url) {
    try {
      const u = new URL(url);
      return u.protocol === "https:" || u.protocol === "http:";
    } catch (e) {
      return false;
    }
  }

  function normalise(text) {
    return String(text || "")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/[.!?]+$/, "")
      .trim();
  }

  const SUMMARY_RE =
    /^(?:please\s+|can you\s+|could you\s+)?(?:summari[sz]e|summary(?: of)?|give me a summary of|tl;?dr)(?:\s+(?:the|this|current|this current))?(?:\s+(?:page|tab|site|website|article|web ?page))?(?:\s+for me)?$/i;

  function parseSearch(rest, allowSite) {
    let q = rest.replace(/^for\s+/i, "").trim();
    if (!q) return null;
    if (allowSite) {
      // "search <q> in|on <site>"
      let m = q.match(/^(.+?)\s+(?:in|on|at)\s+(\S+(?: overflow)?)$/i);
      if (m) {
        const site = resolveSite(m[2]);
        if (site) return { site, query: m[1].trim() };
      }
      // "search <site> for <q>"
      m = q.match(/^(\S+)\s+for\s+(.+)$/i);
      if (m) {
        const site = resolveSite(m[1]);
        if (site) return { site, query: m[2].trim() };
      }
    }
    return { site: null, query: q };
  }

  /**
   * parseIntent(text) ->
   *   { type: "search", query, url, site }   site = host or null (Google)
   *   { type: "summarize" }
   *   { type: "scroll", direction: "up"|"down" }
   *   { type: "click", label }
   *   { type: "fill_form" }
   *   { type: "chat" }
   */
  function parseIntent(text) {
    const t = normalise(text);
    if (!t) return { type: "chat" };
    const lower = t.toLowerCase();

    if (SUMMARY_RE.test(t)) return { type: "summarize" };

    let m = lower.match(/^scroll(?:\s+(?:the\s+page\s+)?(up|down|to (?:the )?(?:top|bottom)))?(?:\s+a bit)?$/);
    if (m) {
      const d = m[1] || "down";
      return { type: "scroll", direction: d === "up" || /top/.test(d) ? "up" : "down" };
    }

    if (/^(?:please\s+)?(?:auto\s*-?fill|fill(?:\s+(?:in|out))?)(?:\s+(?:the|this|my))?(?:\s+(?:form|details|fields))?$/i.test(t) && lower !== "fill") {
      return { type: "fill_form" };
    }

    m = t.match(/^(?:please\s+)?click(?:\s+on)?\s+(?:the\s+)?(.+?)(?:\s+(?:button|link))?$/i);
    if (m && m[1]) return { type: "click", label: m[1].replace(/^["']|["']$/g, "").trim() };

    m = t.match(/^(?:please\s+)?(google|search|look up|find)\s+(.+)$/i);
    if (m) {
      const verb = m[1].toLowerCase();
      const parsed = parseSearch(m[2], verb !== "google");
      if (parsed && parsed.query) {
        const url = parsed.site ? siteSearchUrl(parsed.site, parsed.query) : googleUrl(parsed.query);
        if (isSafeUrl(url)) return { type: "search", query: parsed.query, url, site: parsed.site ? parsed.site.host : null };
      }
    }

    return { type: "chat" };
  }

  /** Finds the scanned element whose visible text/label best matches `label`
   * in a sanitized screen graph (buttons, links, inputs). Returns its ref. */
  function findTargetRef(graph, label) {
    if (!graph || !label) return null;
    const want = String(label).toLowerCase().trim();
    const items = []
      .concat(graph.buttons || [], graph.links || [], graph.inputs || [])
      .map((i) => ({ ref: i.ref, text: String(i.text || i.label || "").toLowerCase().trim() }))
      .filter((i) => i.ref && i.text);
    const exact = items.find((i) => i.text === want);
    if (exact) return exact.ref;
    const starts = items.find((i) => i.text.startsWith(want));
    if (starts) return starts.ref;
    const contains = items.find((i) => i.text.includes(want));
    return contains ? contains.ref : null;
  }

  const api = { parseIntent, resolveSite, isSafeUrl, findTargetRef, SITES };
  root.AivaIntent = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
