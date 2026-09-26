// node --test extension/tests
// The NER rule pass is hooked into pii-checks.js redactAllPII(): when
// ner-rules.js has been loaded first (as in the manifest), names and
// addresses are tokenised alongside the regex PII. Synthetic data only.
const test = require("node:test");
const assert = require("node:assert/strict");
require("../ner-rules.js"); // sets globalThis.AivaNerRules, as the content script does
const P = require("../pii-checks.js");

test("redactAllPII tokenises names, addresses and regex PII together", () => {
  const counters = {};
  const t = "My name is Anjali Sharma, call 9876543210, I live at 21 MG Road, Bengaluru.";
  const { redactedText, categoriesFound } = P.redactAllPII(t, counters);
  assert.ok(!redactedText.includes("Anjali"), redactedText);
  assert.ok(!redactedText.includes("9876543210"), redactedText);
  assert.ok(!redactedText.includes("MG Road"), redactedText);
  assert.equal(categoriesFound.NAME, 1);
  assert.equal(categoriesFound.PHONE, 1);
  assert.equal(categoriesFound.ADDRESS, 1);
  assert.match(redactedText, /NAME_1/);
  assert.match(redactedText, /ADDRESS_1/);
});

test("text with no PII is unchanged", () => {
  const t = "Click the submit button to continue.";
  assert.equal(P.redactAllPII(t, {}).redactedText, t);
});
