// Node harness: runs the extension's JS detectors over bench/dataset.json.
// Usage: node bench/js_harness.js  -> JSON on stdout {pageId: [{item, spans}], ...}
const path = require("path");
const fs = require("fs");
const pii = require(path.join(__dirname, "..", "extension", "pii-checks.js"));
const ner = require(path.join(__dirname, "..", "extension", "ner-rules.js"));
const data = JSON.parse(fs.readFileSync(path.join(__dirname, "dataset.json"), "utf8"));
const out = {};
for (const p of data.pages) {
  const t0 = process.hrtime.bigint();
  const items = p.items.map((it) => {
    const text = it.text || "";
    const spans = [];
    for (const m of pii.detectPII(text)) spans.push({ type: m.type, start: m.start, end: m.end, validated: !!m.validated });
    for (const m of ner.detect(text)) spans.push({ type: m.type, start: m.start, end: m.end, validated: true });
    return spans;
  });
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  out[p.id] = { items, ms };
}
process.stdout.write(JSON.stringify(out));
