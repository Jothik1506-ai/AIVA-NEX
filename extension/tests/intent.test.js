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
