// node --test extension/tests/intent.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const I = require("../intent.js");

test("search <q> opens Google with an encoded query", () => {
  const r = I.parseIntent("search wireless earbuds & case");
  assert.equal(r.type, "search");
  assert.equal(r.site, null);
  assert.equal(r.url, "https://www.google.com/search?q=wireless%20earbuds%20%26%20case");
});

test("google <q> always searches Google, even with 'on <site>'", () => {
  const r = I.parseIntent("google laptops on amazon");
  assert.equal(r.url, "https://www.google.com/search?q=laptops%20on%20amazon");
});

test("search <q> in|on <known site> uses the site's own search", () => {
  assert.equal(I.parseIntent("search rtx 5060 in amazon").url, "https://www.amazon.in/s?k=rtx%205060");
  assert.equal(I.parseIntent("search lofi music on YouTube").url, "https://www.youtube.com/results?search_query=lofi%20music");
  assert.equal(I.parseIntent("search for react hooks on github").url, "https://www.github.com/search?q=react%20hooks");
  assert.equal(I.parseIntent("search wikipedia for Chandrayaan").url, "https://en.wikipedia.org/wiki/Special:Search?search=Chandrayaan");
  assert.equal(I.parseIntent("search shoes on flipkart.com").url, "https://www.flipkart.com/search?q=shoes");
});

test("unknown domains fall back to Google site: search", () => {
  const r = I.parseIntent("search rust async on docs.rs");
  assert.equal(r.site, "docs.rs");
  assert.equal(r.url, "https://www.google.com/search?q=" + encodeURIComponent("site:docs.rs rust async"));
});

test("'in <place>' that isn't a site stays part of a Google query", () => {
  const r = I.parseIntent("search cheap hotels in goa");
  assert.equal(r.site, null);
  assert.equal(r.query, "cheap hotels in goa");
});

test("summarize variants never become a web search", () => {
  for (const t of ["summarize the page", "Summarize the page.", "summarise this page", "summary of this page", "summarize", "please summarize this page", "summarize page", "tl;dr"]) {
    assert.equal(I.parseIntent(t).type, "summarize", t);
  }
});

test("scroll, click and fill form map to local actions", () => {
  assert.deepEqual(I.parseIntent("scroll up"), { type: "scroll", direction: "up" });
  assert.deepEqual(I.parseIntent("scroll"), { type: "scroll", direction: "down" });
  assert.deepEqual(I.parseIntent("scroll to the top"), { type: "scroll", direction: "up" });
  assert.deepEqual(I.parseIntent("click on the Sign in button"), { type: "click", label: "Sign in" });
  assert.equal(I.parseIntent("fill form").type, "fill_form");
  assert.equal(I.parseIntent("autofill the form").type, "fill_form");
});

test("anything else goes to chat", () => {
  for (const t of ["what is on this page?", "hello", "search", "", "how do I reset my password"]) {
    assert.equal(I.parseIntent(t).type, "chat", t);
  }
});

test("only http/https URLs are safe", () => {
  assert.ok(I.isSafeUrl("https://example.com"));
  assert.ok(!I.isSafeUrl("javascript:alert(1)"));
  assert.ok(!I.isSafeUrl("file:///c:/x"));
  assert.ok(!I.isSafeUrl("not a url"));
});

test("findTargetRef matches button/link/input labels", () => {
  const g = { buttons: [{ ref: "b1", text: "Sign in" }], links: [{ ref: "l1", text: "Help centre" }], inputs: [{ ref: "i1", label: "Email" }] };
  assert.equal(I.findTargetRef(g, "sign in"), "b1");
  assert.equal(I.findTargetRef(g, "help"), "l1");
  assert.equal(I.findTargetRef(g, "email"), "i1");
  assert.equal(I.findTargetRef(g, "checkout"), null);
});

// ---- natural-language navigation / search (table-driven) ----
const AMZ = (q) => "https://www.amazon.in/s?k=" + encodeURIComponent(q);
const G = (q) => "https://www.google.com/search?q=" + encodeURIComponent(q);
const CASES = [
  ["in new tab open amazon and search for I phone 16 pro phone", "search", AMZ("I phone 16 pro phone"), true],
  ["In a new tab, open Amazon and search for iphone 16 pro phone.", "search", AMZ("iphone 16 pro phone"), true],
  ["open amazon and search iphone 16 pro", "search", AMZ("iphone 16 pro"), false],
  ["open amazon then search iphone 16 pro", "search", AMZ("iphone 16 pro"), false],
  ["open amazon phone and search for iphone", "search", AMZ("iphone"), false],
  ["go to flipkart, find running shoes", "search", "https://www.flipkart.com/search?q=running%20shoes", false],
  ["search for iphone 16 on amazon in a new tab", "search", AMZ("iphone 16"), true],
  ["can you please look up rtx 5060 on amazon", "search", AMZ("rtx 5060"), false],
  ["could you find earbuds on amazon for me", "search", AMZ("earbuds"), false],
  ["open youtube", "navigate", "https://www.youtube.com", false],
  ["Open YouTube in new tab", "navigate", "https://www.youtube.com", true],
  ["open github.com", "navigate", "https://www.github.com", false],
  ["go to https://x.org", "navigate", "https://x.org", false],
  ["navigate to www.Amazon.in", "navigate", "https://www.amazon.in", false],
  ["visit wikipedia and search isro", "search", "https://en.wikipedia.org/wiki/Special:Search?search=isro", false],
  ["new tab google best laptops 2026", "search", G("best laptops 2026"), true],
  ["new tab youtube lofi music", "search", "https://www.youtube.com/results?search_query=lofi%20music", true],
  ["search amazon for earbuds", "search", AMZ("earbuds"), false],
  ["open example.org and search foo", "search", G("site:example.org foo"), false],
  ["launch netflix", "navigate", "https://netflix.com", false],
  ["open my bank website", "search", G("my bank"), false],
  ["here open amazon and show me laptops", "search", AMZ("laptops"), false],
  ["open amazon and search earbuds in this tab", "search", AMZ("earbuds"), false],
  ["take me to the flipkart website", "navigate", "https://www.flipkart.com", false],
];

for (const [text, type, url, newTab] of CASES) {
  test(`NL: ${text}`, () => {
    const r = I.parseIntent(text);
    assert.equal(r.type, type);
    assert.equal(r.url, url);
    assert.equal(r.newTab, newTab);
  });
}

test("NL negatives stay local/chat; PII query is a search the panel blocks", () => {
  assert.equal(I.parseIntent("summarize this page").type, "summarize");
  assert.equal(I.parseIntent("what is on this page").type, "chat");
  assert.equal(I.parseIntent("open").type, "chat");
  assert.equal(I.parseIntent("new tab").type, "chat");
  assert.equal(I.parseIntent("open javascript:alert(1)").type !== "navigate" || false, true);
  const r = I.parseIntent("search 9999 4105 7058");
  assert.equal(r.type, "search");
  assert.equal(r.query, "9999 4105 7058"); // popup.js tokeniseChatText blocks this before navigating
});
