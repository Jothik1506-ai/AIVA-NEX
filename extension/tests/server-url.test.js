// node --test extension/tests/server-url.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../server-url.js");

test("empty input falls back to the local default", () => {
  assert.deepEqual(S.validateServerUrl(""), { ok: true, url: "http://127.0.0.1:8000" });
  assert.deepEqual(S.validateServerUrl(undefined), { ok: true, url: "http://127.0.0.1:8000" });
});

test("http/https URLs are accepted and normalised", () => {
  assert.equal(S.validateServerUrl(" http://localhost:9000/ ").url, "http://localhost:9000");
  assert.equal(S.validateServerUrl("https://aiva.example.com/api/").url, "https://aiva.example.com/api");
});

test("other schemes and malformed input are rejected", () => {
  for (const bad of ["ftp://host", "javascript:alert(1)", "file:///c:/x", "127.0.0.1:8000", "not a url", "http://u:p@host", "http://host/?x=1", "http://host/#a"]) {
    assert.equal(S.validateServerUrl(bad).ok, false, bad);
  }
});

test("isLocalhost", () => {
  assert.ok(S.isLocalhost("http://127.0.0.1:8000"));
  assert.ok(S.isLocalhost("http://localhost:8000"));
  assert.ok(!S.isLocalhost("https://aiva.example.com"));
});
