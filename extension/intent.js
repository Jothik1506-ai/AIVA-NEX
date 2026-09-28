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
    let q = cleanQuery(rest.replace(/^for\s+/i, ""));
    if (!q) return null;
    if (allowSite) {
      // "search <q> in|on|at <site>"
      let m = q.match(/^(.+?)\s+(?:in|on|at)\s+(?:the\s+)?(\S+(?: overflow)?)(?:\s+(?:website|site|app))?$/i);
      if (m) {
        const site = resolveSite(m[2]);
        if (site) return { site, query: cleanQuery(m[1]) };
      }
      // "search <site> for <q>"
      m = q.match(/^(?:the\s+)?(\S+(?: overflow)?)\s+for\s+(.+)$/i);
      if (m) {
        const site = resolveSite(m[1]);
        if (site) return { site, query: cleanQuery(m[2]) };
      }
    }
    return { site: null, query: q };
  }

  // Tab modifiers can appear anywhere: "in new tab open ...", "... in a new tab".
  const NEW_TAB_RE = /(?:^|[\s,])(?:(?:in|on|using|with)\s+)?(?:a\s+)?new\s+tab(?=$|[\s,])/i;
  const SAME_TAB_RE = /(?:^|[\s,])(?:in|on)\s+(?:this|the\s+same|the\s+current|current|same)\s+tab(?=$|[\s,])/i;
  const HERE_RE = /(?:^\s*here\s*,?\s+|[\s,]+here$)/i;
  const FILLER_START = /^(?:please|pls|kindly|can you|could you|would you|will you|hey|ok|okay|just|i want to|i want you to)\s*,?\s+/i;
  const FILLER_END = /\s*,?\s+(?:please|for me|pls)$/i;

  function extractModifiers(t) {
    let newTab = false;
    let s = " " + t + " ";
    if (NEW_TAB_RE.test(s)) { newTab = true; s = s.replace(NEW_TAB_RE, " "); }
    s = s.replace(SAME_TAB_RE, " ");
    s = s.replace(/\s+/g, " ").trim().replace(HERE_RE, " ");
    return { newTab, text: tidy(s) };
  }

  function tidy(s) {
    return String(s).replace(/\s+/g, " ").replace(/\s+,/g, ",").replace(/^[\s,]+|[\s,]+$/g, "").trim();
  }

  function stripFiller(s) {
    let prev;
    do {
      prev = s;
      s = tidy(s.replace(FILLER_START, "").replace(FILLER_END, ""));
    } while (s !== prev);
    return s;
  }

  function cleanQuery(q) {
    return tidy(String(q || "").replace(/^(?:for|about)\s+/i, "").replace(FILLER_END, "").replace(/^["']|["']$/g, ""));
  }

  function wwwHost(host) {
    return host.split(".").length === 2 ? "www." + host : host;
  }

  /** Site phrase from "open <site>": drops "the", "website", trailing "phone"/"product". */
  function cleanSitePhrase(s) {
    return tidy(String(s).replace(/^(?:the)\s+/i, "").replace(/\s+(?:website|web ?site|site|app|homepage|home page|page|phone|product|products)$/i, ""));
  }

  const SEARCH_VERB = /(?:search(?:\s+for)?|look\s+for|look\s+up|find|show\s+me|get\s+me)/.source;
  const OPEN_RE = /^(?:open(?:\s+up)?|go\s+to|goto|visit|navigate\s+to|launch|take\s+me\s+to|browse\s+to)\s+(.+)$/i;
  const OPEN_THEN_SEARCH_RE = new RegExp(/^(.+?)\s*(?:,\s*|\s+)(?:(?:and\s+then|and|then)\s+)?/.source + SEARCH_VERB + /\s+(.+)$/.source, "i");
  const SEARCH_RE = new RegExp("^" + SEARCH_VERB + /\s+(.+)$/.source, "i");

  function buildSearch(site, query, newTab) {
    const url = site ? siteSearchUrl(site, query) : googleUrl(query);
    if (!isSafeUrl(url)) return null;
    return { type: "search", query, url, site: site ? site.host : null, siteName: site ? site.name : "Google", newTab };
  }

  /** "open <site> [and search <q>]" / "open https://x.org". */
  function parseOpen(rest, newTab) {
    rest = tidy(rest);
    const urlM = rest.match(/^(https?:\/\/\S+)$/i);
    if (urlM) {
      return isSafeUrl(urlM[1]) ? { type: "navigate", url: urlM[1], site: new URL(urlM[1]).host, siteName: new URL(urlM[1]).host, newTab } : null;
    }
    let sitePhrase = rest;
    let query = "";
    const m = rest.match(OPEN_THEN_SEARCH_RE);
    if (m) { sitePhrase = m[1]; query = cleanQuery(m[2]); }
    sitePhrase = cleanSitePhrase(sitePhrase);
    if (!sitePhrase) return null;
    let site = resolveSite(sitePhrase);
    const oneWord = /^[a-z0-9-]+$/i.test(sitePhrase);
    if (query) {
      if (!site && oneWord) site = { name: sitePhrase.toLowerCase(), host: sitePhrase.toLowerCase() + ".com", path: null };
      if (!site) return buildSearch(null, sitePhrase + " " + query, newTab);
      return buildSearch(site, query, newTab);
    }
    if (site) {
      const url = "https://" + (SITES[site.name] ? wwwHost(site.host) : site.host);
      return isSafeUrl(url) ? { type: "navigate", url, site: site.host, siteName: site.name, newTab } : null;
    }
    if (oneWord) {
      const host = sitePhrase.toLowerCase() + ".com";
      return { type: "navigate", url: "https://" + host, site: host, siteName: host, newTab };
    }
    return buildSearch(null, sitePhrase, newTab);
  }

  /**
   * parseIntent(text) ->
   *   { type: "search", query, url, site, siteName, newTab }  site = host or null (Google)
   *   { type: "navigate", url, site, siteName, newTab }
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

    const mods = extractModifiers(t);
    const body = stripFiller(mods.text);
    if (!body) return { type: "chat" };

    // "google <q>" always searches Google literally.
    m = body.match(/^google\s+(.+)$/i);
    if (m) {
      const q = cleanQuery(m[1]);
      const r = q && buildSearch(null, q, mods.newTab);
      if (r) return r;
    }

    m = body.match(OPEN_RE);
    if (m) {
      const r = parseOpen(m[1], mods.newTab);
      if (r) return r;
    }

    m = body.match(SEARCH_RE);
    if (m) {
      const parsed = parseSearch(m[1], true);
      if (parsed && parsed.query) {
        const r = buildSearch(parsed.site, parsed.query, mods.newTab);
        if (r) return r;
      }
    }

    // "<known site> <query>" only with an explicit tab modifier ("new tab youtube lofi").
    if (mods.newTab) {
      m = body.match(/^(\S+)\s+(.+)$/);
      const site = m && SITES[ALIASES[m[1].toLowerCase()] || m[1].toLowerCase()] && resolveSite(m[1]);
      if (site) {
        const r = buildSearch(site, cleanQuery(m[2]), true);
        if (r) return r;
      }
      if (resolveSite(body)) return parseOpen(body, true) || { type: "chat" };
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
