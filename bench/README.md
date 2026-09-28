# bench/ - AIVA-NEX accuracy benchmark

Scores the privacy layers and the action picker on a small **synthetic,
self-labelled** dataset. The pages, names, addresses and labels were written by
the benchmark author; numbers are documented test values (UIDAI test Aadhaar
`9999 4105 7058`, public Luhn test cards) or generated with correct/incorrect
check digits. The detectors were **not** tuned to this data. Treat results as a
sanity check, not a field evaluation.

## Files
| File | What |
|---|---|
| `gen_dataset.py` | Deterministic generator (seed 26171) for `dataset.json` |
| `dataset.json` | 40 pages: KYC, bank, railway, job, login, checkout + 10 no-PII distractors. Each text item lists its PII spans (AADHAAR, PAN, CARD, PHONE, EMAIL, NAME, ADDRESS; unlabelled = none) and each page its expected action + target |
| `run_bench.py` | Runs everything and writes `results.md` (slide-ready tables) and `results.json` |
| `js_harness.js` | Node harness for `extension/pii-checks.js` + `extension/ner-rules.js` |

Hard negatives included: 12-digit order/UPI ids with a wrong Verhoeff digit,
16-digit non-Luhn transaction ids, a PNR and ticket numbers shaped like mobile
numbers, a toll-free number, an EAN barcode, and invalid-checksum Aadhaar/cards.

## Run (repo root, PowerShell)
```powershell
server\.venv\Scripts\python bench\gen_dataset.py   # only if you change the generator
server\.venv\Scripts\python bench\run_bench.py      # --no-js / --no-llm to skip parts
```
Node (any recent version) on PATH enables the JS scoring. If
`LOCAL_LLM_BASE_URL` (default `http://localhost:11434/v1`) answers `/models`,
the local LLM's action choice is scored too; `results.md` states which decider ran.

## Method
- A predicted span is a true positive if it has the same type and overlaps a
  labelled span. Each input value is scanned on its own (no label context), so
  the extension's label-hint classification in `content.js` is not credited.
- *Candidate* mode counts every regex hit (the extension tokenises all of them);
  *validated* mode counts only checksum/format-validated hits (what the server rejects).
- Actions: screen graphs are built in the same shape as `content.js` output
  (`isSensitive`, `sanitizedValue`) and passed to `main.decide_action_rules`.
  Expected-action policy: focus the first empty required field; else click the
  primary submit-like button; else scroll if the page says more follows; else summarize.
  This policy is the author's choice; another labeller could differ on a few pages.
- The action rules in `decide_action_rules` were revised after seeing this set's misses, so the
  action score is not held-out. A second row scores *content.js-faithful* graphs: there, password
  and label-classified fields (Email, PAN, Aadhaar, OTP...) are bare tokens even when empty, so the
  server cannot see that they are empty. Closing that gap needs the extension to send a non-PII
  `hasValue` flag per input (the rule engine already reads it).
- Latency: wall-clock per page on CPU, excluding the one-off spaCy model load.
