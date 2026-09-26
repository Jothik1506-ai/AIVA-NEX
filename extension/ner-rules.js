// ner-rules.js
// Layer 1 of the two-layer NAME/ADDRESS protection (layer 2 is the spaCy
// model + the same rules on the local server, server/ner/).
//
// A cheap, dependency-free port of server/ner/rules.py that runs in the
// content script BEFORE anything leaves the browser: Indian honorifics
// (Mr/Mrs/Shri/Smt/Dr...), relation markers (S/o, D/o, W/o), "my name is",
// salutations, a short Indian-surname list, and a scored address detector
// (6-digit PIN code, H.No/Flat/Plot numbers, Nagar/Colony/Road/Mandal/...,
// state and city names). No model, no network, microseconds per string.
//
// Exposes globalThis.AivaNerRules.detect(text) -> [{start, end, type, score}]
// (type is "NAME" or "ADDRESS"). Also a CommonJS export for node:test.

(function (root) {
  "use strict";

  const INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Orissa", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "New Delhi", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
  ];
  const MAJOR_CITIES = [
    "Hyderabad", "Secunderabad", "Bengaluru", "Bangalore", "Chennai", "Mumbai",
    "Kolkata", "Pune", "Ahmedabad", "Jaipur", "Lucknow", "Kanpur", "Nagpur",
    "Indore", "Bhopal", "Patna", "Vadodara", "Surat", "Visakhapatnam", "Vijayawada",
    "Warangal", "Guntur", "Tirupati", "Coimbatore", "Madurai", "Kochi", "Thiruvananthapuram",
    "Mysuru", "Mysore", "Mangaluru", "Noida", "Gurugram", "Gurgaon", "Ghaziabad",
    "Chandigarh", "Bhubaneswar", "Guwahati", "Ranchi", "Raipur", "Dehradun",
    "Vikarabad", "Karimnagar", "Nizamabad", "Khammam", "Nellore", "Kurnool",
  ];
  const ADDRESS_SUFFIXES = [
    "Road", "Rd", "Street", "St", "Nagar", "Colony", "Layout", "Marg", "Lane",
    "Chowk", "Cross", "Main", "Enclave", "Residency", "Apartments", "Apartment",
    "Apts", "Towers", "Tower", "Complex", "Society", "Heights", "Gardens", "Vihar",
    "Puram", "Pet", "Peta", "Palli", "Guda", "Bagh", "Ganj", "Mandal", "District",
    "Dist", "Taluk", "Taluka", "Tehsil", "Village", "Circle", "Extension", "Hills",
    "Mohalla", "Basti", "Bazar", "Bazaar", "Avenue", "Nivas", "Bhavan", "Villas",
    "Villa", "Township", "Halli", "Wadi", "Kunj",
  ];
  const ADDRESS_PREFIXES = ["Sector", "Block", "Phase", "Ward", "Mandal", "District", "Dist", "Village"];
  const INDIAN_SURNAMES = [
    "Kumar", "Reddy", "Rao", "Sharma", "Verma", "Singh", "Nair", "Pillai", "Iyer",
    "Iyengar", "Menon", "Patel", "Shah", "Gupta", "Agarwal", "Aggarwal", "Mehta",
    "Joshi", "Das", "Dutta", "Banerjee", "Chatterjee", "Mukherjee", "Bose", "Ghosh",
    "Naidu", "Chowdary", "Choudhary", "Chaudhary", "Yadav", "Mishra", "Pandey",
    "Tiwari", "Srivastava", "Khan", "Ahmed", "Hussain", "Devi", "Kaur", "Gill",
    "Sandhu", "Desai", "Kulkarni", "Patil", "Jadhav", "Deshmukh", "Goud", "Varma",
    "Murthy", "Krishnan", "Subramanian", "Raju", "Shetty", "Hegde", "Bhat",
    "Kamath", "Naik", "Sinha", "Jain", "Saxena", "Chopra", "Kapoor", "Malhotra",
  ];
  const COMMON_WORDS = new Set([
    "The", "This", "That", "These", "Those", "A", "An", "And", "Or", "Of", "In", "On",
    "At", "To", "For", "From", "With", "By", "About", "As", "Is", "Are", "Was", "It",
    "Please", "Contact", "Call", "Email", "Ask", "Meet", "Order", "Shop", "Buy",
    "Visit", "Thanks", "Thank", "Dear", "Hi", "Hello", "Hey", "Welcome", "Back",
    "Sign", "Log", "Login", "Submit", "Search", "Home", "Menu", "Cart", "Add",
    "Account", "Profile", "Settings", "Help", "Support", "Name", "Address", "Full",
    "First", "Last", "Middle", "User", "Guest", "Team", "Sir", "Madam", "Everyone",
    "All", "World", "There", "Friend", "Friends", "Customer", "Customers", "Admin",
    "Today", "Tomorrow", "Yesterday", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March", "April", "May",
    "June", "July", "August", "September", "October", "November", "December",
    "India", "Indian", "Weather", "News", "Price", "Offer", "Sale", "Deal", "Deals",
    "New", "Best", "Top", "More", "Details", "Free", "Delivery", "Now", "Get",
    "Your", "My", "Our", "We", "You", "He", "She", "They", "Mr", "Mrs", "Ms", "Dr",
    "Shri", "Sri", "Smt", "Prof", "Kumari", "Via", "Per", "Not", "No", "Yes",
    "Card", "Bank", "Pay", "Payment", "Total", "Amount", "Summary", "Continue",
    ...ADDRESS_SUFFIXES, ...ADDRESS_PREFIXES,
  ]);

  const NAME_WORD = "(?:[A-Z][a-z]+(?:-[A-Z][a-z]+)?|[A-Z]\\.?(?=\\s))";
  const NAME_SEQ = `${NAME_WORD}(?:\\s+${NAME_WORD}){0,3}`;

  const HONORIFIC_RE = new RegExp(
    `\\b(?:Mr|Mrs|Ms|Mx|Shri|Shrimati|Sri(?!\\s+Lanka)|Smt|Kumari|Km|Dr|Prof|Thiru|Thirumathi|Selvi)\\b\\.?\\s+(${NAME_SEQ})`, "g");
  const RELATION_RE = new RegExp(
    `(?:\\b[SDWCH]/[Oo]\\b|\\b(?:Son|Daughter|Wife|Husband|Care)\\s+of\\b)\\.?\\s*:?\\s*` +
    `(?:(?:Mr|Mrs|Ms|Shri|Sri|Smt|Late|Dr)\\b\\.?\\s+)?(${NAME_SEQ})`, "g");
  const BEFORE_RELATION_RE = new RegExp(`(${NAME_SEQ})\\s*,?\\s*(?=\\b[SDWC]/[Oo]\\b)`, "g");
  const NAME_CUE_RE = new RegExp(`(?:\\b[Mm]y\\s+name\\s+is|\\b[Nn]ame\\s*[:\\-]|\\b[Mm]yself)\\s+(${NAME_SEQ})`, "g");
  const IAM_RE = new RegExp(`(?:\\bI\\s+am|\\bI'm)\\s+(${NAME_WORD}\\s+${NAME_WORD}(?:\\s+${NAME_WORD})?)`, "g");
  const SALUTATION_RE = new RegExp(
    `\\b(?:Dear|Hi|Hello|Hey|Welcome(?:\\s+back)?|Thanks|Thank\\s+you)\\s*,?\\s+(${NAME_WORD}(?:\\s+${NAME_WORD}){0,2})`, "g");
  const SURNAME_RE = new RegExp(`((?:${NAME_WORD}\\s+){1,2}(?:${INDIAN_SURNAMES.join("|")}))\\b`, "g");

  const PIN_RE = /(?<![\d.,₹$])\b[1-9]\d{2}\s?\d{3}\b(?![.,]?\d)/g;
  const CURRENCY_BEFORE_RE = /(?:₹|Rs\.?|INR|\$|USD)\s*$/i;
  const HOUSE_RE = /(?:\b(?:H|D|House|Door|Flat|Plot|Shop|Room|Survey|Unit)\s?\.?\s?No\b\.?|\bFlat\b|\bPlot\b|#)\s*[:#.\-]?\s*[A-Za-z]?\d[\w/\-]*/gi;
  const DOOR_NUMBER_RE = /\b\d{1,4}(?:-\d{1,4}){1,3}(?:\/\w+)?\b/g;
  const SUFFIX_RE = new RegExp(`\\b(?:${ADDRESS_SUFFIXES.join("|")})\\b\\.?`, "gi");
  const SUFFIX_WORD_RE = new RegExp(`^(?:${ADDRESS_SUFFIXES.join("|")})$`, "i");
  const PREFIX_RE = new RegExp(`\\b(?:${ADDRESS_PREFIXES.join("|")})\\b\\s*[:\\-]?\\s*(?:No\\.?\\s*)?[A-Z0-9][\\w\\-]*`, "g");
  const STATE_RE = new RegExp(`\\b(?:${INDIAN_STATES.map((s) => s.replace(/ /g, "\\s+")).join("|")})\\b`, "g");
  const CITY_RE = new RegExp(`\\b(?:${MAJOR_CITIES.join("|")})\\b`, "g");
  const LANDMARK_RE = /\b(?:Near|Opp|Opposite|Behind|Beside|Next\s+to)\b\.?\s+(?=[A-Z])/gi;
  const ADDRESS_CUE_RE = /\b(?:li(?:ve|ves|ved|ving)\s+(?:at|in)|resid(?:e|es|ing)\s+at|stay(?:s|ing)?\s+at|deliver(?:ed|y)?\s+to|ship(?:ped)?\s+to|located\s+at|address(?:\s+is)?\s*[:\-]?|pin\s?code\s*[:\-]?|pin\s*[:\-])\s*/i;
  const SENTENCE_END_RE = /(?<=[a-z0-9]{2})[.!?](?=\s+[A-Z]|\s*$)/g;
  const SOFT_SPLIT_RE = /[,;\n|]/g;
  const TOKEN_LIKE_RE = /\b[A-Z]+(?:_[A-Z]+)*_(?:\d+|FIELD)\b/;
  const ADDRESS_THRESHOLD = 3.0;

  function* matches(re, text) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      yield m;
      if (m[0].length === 0) re.lastIndex++;
    }
  }

  // Start offset of capture group 1 inside match m.
  function groupStart(m) {
    return m.index + m[0].indexOf(m[1]);
  }

  function stripCommon(text, start, end) {
    const words = [];
    for (const m of matches(/\S+/g, text.slice(start, end))) {
      words.push([start + m.index, start + m.index + m[0].length, m[0]]);
    }
    const bare = (w) => w.replace(/[.,]+$/, "");
    while (words.length && COMMON_WORDS.has(bare(words[0][2]))) words.shift();
    while (words.length && COMMON_WORDS.has(bare(words[words.length - 1][2]))) words.pop();
    if (!words.length) return null;
    let s = words[0][0];
    let e = words[words.length - 1][1];
    while (e > s && ".,".includes(text[e - 1])) e--;
    if (/(?:'|\u2019)s$/.test(text.slice(s, e))) e -= 2;
    return e > s ? [s, e] : null;
  }

  function findNameSpans(text) {
    const spans = [];
    const add = (matchStart, nameStart, nameEnd, score, keepPrefix) => {
      const t = stripCommon(text, nameStart, nameEnd);
      if (!t) return;
      let [s, e] = t;
      if (keepPrefix) s = Math.min(s, matchStart);
      spans.push({ start: s, end: e, type: "NAME", score });
    };
    for (const m of matches(HONORIFIC_RE, text)) add(m.index, groupStart(m), groupStart(m) + m[1].length, 0.95, true);
    for (const m of matches(RELATION_RE, text)) add(0, groupStart(m), groupStart(m) + m[1].length, 0.95, false);
    for (const m of matches(BEFORE_RELATION_RE, text)) add(0, m.index, m.index + m[1].length, 0.9, false);
    for (const m of matches(NAME_CUE_RE, text)) add(0, groupStart(m), groupStart(m) + m[1].length, 0.9, false);
    for (const m of matches(IAM_RE, text)) add(0, groupStart(m), groupStart(m) + m[1].length, 0.8, false);
    for (const m of matches(SALUTATION_RE, text)) add(0, groupStart(m), groupStart(m) + m[1].length, 0.8, false);
    for (const m of matches(SURNAME_RE, text)) {
      let s = m.index;
      const words = m[1].split(/\s+/);
      if (words.length === 3 && /(?:^|[.!?]\s+)$/.test(text.slice(0, s))) {
        s = m.index + m[1].indexOf(words[1], words[0].length);
      }
      add(0, s, m.index + m[1].length, 0.85, false);
    }
    return spans.filter((sp) => {
      const frag = text.slice(sp.start, sp.end);
      const last = frag.split(/\s+/).pop().replace(/[.,]+$/, "");
      HONORIFIC_RE.lastIndex = 0;
      return !TOKEN_LIKE_RE.test(frag) && !(SUFFIX_WORD_RE.test(last) && !HONORIFIC_RE.test(frag));
    });
  }

  function segments(text) {
    const cuts = [];
    for (const m of matches(SENTENCE_END_RE, text)) cuts.push([m.index, m.index + m[0].length, true]);
    for (const m of matches(SOFT_SPLIT_RE, text)) cuts.push([m.index, m.index + m[0].length, false]);
    cuts.sort((a, b) => a[0] - b[0]);
    const segs = [];
    let pos = 0;
    let hard = true;
    for (const [cs, ce, isHard] of cuts) {
      if (cs < pos) continue;
      segs.push([pos, cs, hard]);
      pos = ce;
      hard = isHard;
    }
    segs.push([pos, text.length, hard]);
    return segs.map(([s, e, h]) => {
      while (s < e && /\s/.test(text[s])) s++;
      while (e > s && /\s/.test(text[e - 1])) e--;
      return [s, e, h];
    });
  }

  function properRunStart(text, kwStart, floor) {
    const words = [...matches(/[A-Za-z0-9][\w\-/.]*/g, text.slice(floor, kwStart))];
    let start = kwStart;
    let n = 0;
    for (let k = words.length - 1; k >= 0; k--) {
      const m = words[k];
      const gap = text.slice(floor + m.index + m[0].length, start);
      if (gap.trim()) break;
      const w = m[0];
      if (!(/[A-Z0-9]/.test(w[0])) || ["The", "A", "An"].includes(w.replace(/\.$/, ""))) break;
      start = floor + m.index;
      if (++n >= 4) break;
    }
    return start;
  }

  function segmentFeatures(text, s, e) {
    const seg = text.slice(s, e);
    let score = 0;
    const feats = [];
    let cueEnd = null;
    const cue = ADDRESS_CUE_RE.exec(seg);
    if (cue) {
      score += 1;
      cueEnd = s + cue.index + cue[0].length;
    }
    for (const m of matches(PIN_RE, seg)) {
      if (CURRENCY_BEFORE_RE.test(seg.slice(0, m.index))) continue;
      score += 2;
      feats.push([s + m.index, s + m.index + m[0].length]);
    }
    let house = false;
    for (const re of [HOUSE_RE, DOOR_NUMBER_RE]) {
      for (const m of matches(re, seg)) {
        house = true;
        feats.push([s + m.index, s + m.index + m[0].length]);
      }
    }
    if (house) score += 2;
    let nSuffix = 0;
    for (const m of matches(SUFFIX_RE, seg)) {
      const ks = s + m.index;
      const begin = properRunStart(text, ks, s);
      if (begin === ks) continue;
      if (++nSuffix <= 3) score += 1;
      if (/^\d/.test(text.slice(begin, ks))) score += 0.5;
      feats.push([begin, s + m.index + m[0].length]);
    }
    for (const [re, w] of [[PREFIX_RE, 1], [STATE_RE, 1], [CITY_RE, 0.5], [LANDMARK_RE, 0.5]]) {
      for (const m of matches(re, seg)) {
        score += w;
        feats.push([s + m.index, s + m.index + m[0].length]);
      }
    }
    return { score, feats, cueEnd };
  }

  function isFiller(text, s, e) {
    const words = text.slice(s, e).split(/\s+/).filter(Boolean);
    return words.length >= 1 && words.length <= 3 && words.every((w) => /[A-Z0-9]/.test(w[0]));
  }

  function findAddressSpans(text, nameSpans) {
    const names = nameSpans || [];
    const overlapsName = (s, e) => names.some((n) => s < n.end && e > n.start);
    const segs = segments(text);
    const info = segs.map(([s, e]) => segmentFeatures(text, s, e));
    const out = [];
    let i = 0;
    while (i < segs.length) {
      if (!info[i].feats.length) {
        i++;
        continue;
      }
      let first = i;
      let j = i;
      let runScore = info[i].score;
      let trailing = 0;
      while (j + 1 < segs.length && !segs[j + 1][2]) {
        const [ns, ne] = segs[j + 1];
        if (info[j + 1].feats.length) {
          runScore += info[j + 1].score;
          trailing = 0;
          j++;
        } else if (isFiller(text, ns, ne) && trailing < 2 && !overlapsName(ns, ne)) {
          trailing++;
          j++;
        } else break;
      }
      if (first > 0 && !segs[first][2]) {
        const [ps, pe] = segs[first - 1];
        if (!info[first - 1].feats.length && text.slice(ps, pe).split(/\s+/).length === 1 &&
            isFiller(text, ps, pe) && !overlapsName(ps, pe)) first--;
      }
      if (runScore >= ADDRESS_THRESHOLD) {
        const fstart = Math.min(...info[i].feats.map((f) => f[0]));
        const cueEnd = info[i].cueEnd;
        let start;
        if (first < i) start = segs[first][0];
        else start = cueEnd !== null && cueEnd <= fstart ? Math.max(fstart, cueEnd) : fstart;
        let lastAddr = i;
        for (let k = i; k <= j; k++) if (info[k].feats.length) lastAddr = k;
        let end = Math.max(...info[lastAddr].feats.map((f) => f[1]));
        if (j > lastAddr) end = segs[j][1];
        while (end > start && " .,;".includes(text[end - 1])) end--;
        out.push({ start, end, type: "ADDRESS", score: Math.min(0.99, 0.5 + runScore / 10) });
      }
      i = j + 1;
    }
    return out;
  }

  function mergeNames(spans) {
    spans.sort((a, b) => a.start - b.start || b.end - a.end);
    const merged = [];
    for (const sp of spans) {
      const prev = merged[merged.length - 1];
      if (prev && sp.start < prev.end) {
        prev.end = Math.max(prev.end, sp.end);
        prev.score = Math.max(prev.score, sp.score);
      } else merged.push({ ...sp });
    }
    return merged;
  }

  // Public: NAME/ADDRESS spans, non-overlapping, sorted by start.
  function detect(text) {
    if (!text || !text.trim()) return [];
    const names = mergeNames(findNameSpans(text));
    const addrs = findAddressSpans(text, names);
    const keptNames = [];
    for (const n of names) {
      let drop = false;
      for (let k = 0; k < addrs.length; k++) {
        const a = addrs[k];
        if (!a || !(n.start < a.end && n.end > a.start)) continue;
        if (a.start <= n.start && n.end <= a.end) drop = true;
        else if (n.start <= a.start && n.end < a.end) {
          let ns = n.end;
          while (ns < a.end && " ,;:-".includes(text[ns])) ns++;
          addrs[k] = { ...a, start: ns };
        } else addrs[k] = null;
      }
      if (!drop) keptNames.push(n);
    }
    return keptNames.concat(addrs.filter(Boolean)).sort((a, b) => a.start - b.start);
  }

  const api = { detect, findNameSpans, findAddressSpans };
  root.AivaNerRules = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
