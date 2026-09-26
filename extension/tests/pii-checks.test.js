// Unit tests for extension/pii-checks.js - run with: node --test extension/tests/
// Only published test numbers / synthetic values are used, never real data:
//   999941057058  UIDAI's published sandbox Aadhaar number
//   234567890124  synthetic, check digit computed with Verhoeff
//   4111111111111111, 5555555555554444, 378282246310005, 4222222222222
//                 card-network published test numbers (Luhn-valid)
//   ABCPE1234F    fictional PAN in the correct format

const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../pii-checks.js");

test("Verhoeff: known vectors", () => {
  assert.equal(P.verhoeffValid("2363"), true);
  assert.equal(P.verhoeffValid("2364"), false);
  assert.equal(P.verhoeffCheckDigit("236"), "3");
  assert.equal(P.verhoeffValid("999941057058"), true);
  assert.equal(P.verhoeffValid("999941057059"), false);
});

test("Aadhaar: 12 digits, first 2-9, Verhoeff", () => {
  assert.equal(P.aadhaarValid("9999 4105 7058"), true);
  assert.equal(P.aadhaarValid("234567890124"), true);
  assert.equal(P.aadhaarValid("234567890123"), false, "bad check digit");
  const base = "12345678901";
  assert.equal(P.aadhaarValid(base + P.verhoeffCheckDigit(base)), false, "first digit 1 not allowed");
  assert.equal(P.aadhaarValid("99994105705"), false, "11 digits");
});

test("Luhn / card: 13-19 digits", () => {
  for (const n of ["4111111111111111", "4111-1111-1111-1111", "5555 5555 5555 4444", "378282246310005", "4222222222222"]) {
    assert.equal(P.cardValid(n), true, n);
  }
  assert.equal(P.cardValid("4111111111111112"), false);
  assert.equal(P.cardValid("411111111111"), false, "too short");
  assert.equal(P.luhnValid("79927398713"), true);
});

test("PAN: strict format incl. 4th-char holder type", () => {
  assert.equal(P.panValid("ABCPE1234F"), true);
  assert.equal(P.panValid("abcpe1234f"), true, "case-insensitive input");
  assert.equal(P.panValid("ABCXE1234F"), false, "X is not a valid holder type");
  assert.equal(P.panValid("ABCP1234F"), false);
});

test("detectPII marks validated vs candidate", () => {
  const types = (t) => P.detectPII(t).map((m) => `${m.type}:${m.validated}`);
  assert.deepEqual(types("id 9999 4105 7058"), ["AADHAAR:true"]);
  assert.deepEqual(types("order 234567890123"), ["AADHAAR:false"]);
  assert.deepEqual(types("ref 4111111111111112"), ["CARD:false"]);
  assert.deepEqual(types("pay 4111 1111 1111 1111 now"), ["CARD:true"]);
  assert.deepEqual(types("call 9876543210"), ["PHONE:true"]);
  assert.deepEqual(types("call +91-98765 43210"), ["PHONE:true"]);
  assert.deepEqual(types("mail priya@example.com"), ["EMAIL:true"]);
  assert.deepEqual(types("PAN ABCPE1234F"), ["PAN:true"]);
  assert.deepEqual(types("code ABCXE1234F"), ["PAN:false"]);
});

test("detectPII ignores non-PII numbers", () => {
  assert.deepEqual(P.detectPII("order 123456789012 costs 1299 on 2026-09-26"), []);
  assert.deepEqual(P.detectPII("landline 0401234567"), []);
  assert.deepEqual(P.detectPII("already tokenised PHONE_1 EMAIL_2 ID_NUMBER_3"), []);
});

test("a card is not split into an Aadhaar", () => {
  const m = P.detectPII("4111 1111 1111 1111");
  assert.equal(m.length, 1);
  assert.equal(m[0].type, "CARD");
});

test("redactAllPII replaces every span and keeps spacing", () => {
  const counters = {};
  const { redactedText, categoriesFound } = P.redactAllPII(
    "Mail a.b@example.com or call 9876543210, card 4111 1111 1111 1111 and id 9999 4105 7058.",
    counters
  );
  assert.equal(redactedText, "Mail EMAIL_1 or call PHONE_1, card CARD_1 and id ID_NUMBER_1.");
  assert.deepEqual(categoriesFound, { EMAIL: 1, PHONE: 1, CARD: 1, ID_NUMBER: 1 });
  // shared counters keep numbering unique across calls
  assert.equal(P.redactAllPII("x@y.in", counters).redactedText, "EMAIL_2");
});

test("redactAllPII tokenises unvalidated candidates too (over-redaction is safe)", () => {
  assert.equal(P.redactAllPII("order 234567890123", {}).redactedText, "order ID_NUMBER_1");
});

test("redactAllPII leaves clean text untouched", () => {
  const r = P.redactAllPII("Apple iPhone 17 - 128 GB", {});
  assert.equal(r.redactedText, "Apple iPhone 17 - 128 GB");
  assert.deepEqual(r.categoriesFound, {});
  assert.equal(P.redactAllPII("", {}).redactedText, "");
});

test("scanValueForPII returns the first category or null", () => {
  assert.equal(P.scanValueForPII("ABCPE1234F"), "ID_NUMBER");
  assert.equal(P.scanValueForPII("hello"), null);
});
