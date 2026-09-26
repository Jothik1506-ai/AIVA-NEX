// node --test extension/tests
// All names/addresses here are INVENTED synthetic examples.
const test = require("node:test");
const assert = require("node:assert/strict");
const { detect } = require("../ner-rules.js");

const found = (text) => detect(text).map((s) => [text.slice(s.start, s.end), s.type]);

test("classic Indian name + address", () => {
  const t = "Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080";
  assert.deepEqual(found(t), [
    ["Ravi Kumar", "NAME"],
    ["H.No 12-3-45, Gandhi Nagar, Hyderabad 500080", "ADDRESS"],
  ]);
});

test("honorific, relation marker and multi-part address", () => {
  const t = "Please deliver to Smt. Lakshmi Devi, W/o Suresh Reddy, Flat 4B, Sai Residency, Ameerpet Road, Hyderabad, Telangana 500016";
  const f = found(t);
  assert.ok(f.some(([s, ty]) => s === "Smt. Lakshmi Devi" && ty === "NAME"), JSON.stringify(f));
  assert.ok(f.some(([s, ty]) => s === "Suresh Reddy" && ty === "NAME"), JSON.stringify(f));
  assert.ok(f.some(([s, ty]) => s === "Flat 4B, Sai Residency, Ameerpet Road, Hyderabad, Telangana 500016" && ty === "ADDRESS"));
});

test("cue phrases", () => {
  assert.deepEqual(found("My name is Anjali Sharma and I live at 21 MG Road, Bengaluru."), [
    ["Anjali Sharma", "NAME"],
    ["21 MG Road, Bengaluru", "ADDRESS"],
  ]);
  assert.deepEqual(found("Contact Priya Nair for details."), [["Priya Nair", "NAME"]]);
  assert.deepEqual(found("Plot No 7, Sector 5, Noida, Uttar Pradesh 201301"), [
    ["Plot No 7, Sector 5, Noida, Uttar Pradesh 201301", "ADDRESS"],
  ]);
});

test("no false positives on UI / product / news text", () => {
  for (const t of [
    "Submit", "Sign in", "Hyderabad weather", "Add to cart", "Google Search",
    "Apple iPhone 15 Pro Max", "Price: Rs 500080 only", "Road Safety Week begins",
    "Sri Lanka tour packages", "Hello World", "Punjab National Bank ATM",
    "Rains lash Hyderabad, Telangana; IMD issues alert", "Best laptops under 50000 in Hyderabad",
    "EMAIL_1 and PHONE_1 were redacted; PERSON_1 is the account holder",
  ]) {
    assert.deepEqual(detect(t), [], t);
  }
});

test("spans are well-formed and non-overlapping", () => {
  const t = "Kiran S/o Ramesh Babu, Village Kondapur, Mandal Shankarpally, District Rangareddy";
  const spans = detect(t);
  assert.ok(spans.length >= 3);
  for (let k = 0; k < spans.length; k++) {
    const s = spans[k];
    assert.ok(s.start < s.end && s.end <= t.length);
    assert.ok(["NAME", "ADDRESS"].includes(s.type));
    assert.ok(s.score > 0 && s.score <= 1);
    if (k) assert.ok(spans[k - 1].end <= s.start);
  }
});

test("cheap enough for every string on a page", () => {
  const t = ("Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080. Buy Samsung Galaxy M14 at best price. ").repeat(25).slice(0, 2048);
  detect(t);
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) detect(t);
  const ms = (performance.now() - t0) / 20;
  assert.ok(ms < 50, `2 KB took ${ms.toFixed(1)} ms`);
});
