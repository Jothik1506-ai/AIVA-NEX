# AIVA-NEX accuracy benchmark - results

> **Synthetic, self-labelled data.** 40 invented Indian web pages written by the benchmark author (test Aadhaar/cards, invented names). Detectors were NOT tuned to this set. Treat numbers as an indicative sanity check, not a field evaluation.

- Pages: **40** (131 text items, 51 labelled PII spans; 10 distractor pages with no PII)
- Server NER engine: `spacy:en_core_web_sm+rules` (cold load 1626 ms)
- Match rule: predicted span counts as correct if same type and overlapping a labelled span.
- *Candidate* mode = every regex hit is tokenised (what the extension does). *Validated* mode = only checksum/format-validated hits (what the server rejects with 400).

## 1. PII detection - server (Python: pii_checks.py + ner/)

### Candidate mode (regex hits + NER)

| Type | Precision | Recall | F1 | FP | Support |
|---|---|---|---|---|---|
| AADHAAR | 0.42 | 1.00 | 0.59 | 7 | 5 |
| PAN | 1.00 | 1.00 | 1.00 | 0 | 3 |
| CARD | 0.44 | 1.00 | 0.62 | 5 | 4 |
| PHONE | 0.73 | 1.00 | 0.84 | 3 | 8 |
| EMAIL | 1.00 | 1.00 | 1.00 | 0 | 6 |
| NAME | 0.69 | 1.00 | 0.82 | 8 | 18 |
| ADDRESS | 1.00 | 0.71 | 0.83 | 0 | 7 |
| **Overall (micro)** | 0.68 | 0.96 | 0.80 | 23 | 51 |

### Validated mode (checksum-validated regex + NER)

| Type | Precision | Recall | F1 | FP | Support |
|---|---|---|---|---|---|
| AADHAAR | 1.00 | 1.00 | 1.00 | 0 | 5 |
| PAN | 1.00 | 1.00 | 1.00 | 0 | 3 |
| CARD | 0.80 | 1.00 | 0.89 | 1 | 4 |
| PHONE | 0.73 | 1.00 | 0.84 | 3 | 8 |
| EMAIL | 1.00 | 1.00 | 1.00 | 0 | 6 |
| NAME | 0.69 | 1.00 | 0.82 | 8 | 18 |
| ADDRESS | 1.00 | 0.71 | 0.83 | 0 | 7 |
| **Overall (micro)** | 0.80 | 0.96 | 0.88 | 12 | 51 |

## 2. PII detection - extension (JS: pii-checks.js + ner-rules.js, Node)

### Candidate mode

| Type | Precision | Recall | F1 | FP | Support |
|---|---|---|---|---|---|
| AADHAAR | 0.42 | 1.00 | 0.59 | 7 | 5 |
| PAN | 1.00 | 1.00 | 1.00 | 0 | 3 |
| CARD | 0.44 | 1.00 | 0.62 | 5 | 4 |
| PHONE | 0.73 | 1.00 | 0.84 | 3 | 8 |
| EMAIL | 1.00 | 1.00 | 1.00 | 0 | 6 |
| NAME | 0.94 | 0.94 | 0.94 | 1 | 18 |
| ADDRESS | 1.00 | 0.71 | 0.83 | 0 | 7 |
| **Overall (micro)** | 0.75 | 0.94 | 0.83 | 16 | 51 |

### Validated mode

| Type | Precision | Recall | F1 | FP | Support |
|---|---|---|---|---|---|
| AADHAAR | 1.00 | 1.00 | 1.00 | 0 | 5 |
| PAN | 1.00 | 1.00 | 1.00 | 0 | 3 |
| CARD | 0.80 | 1.00 | 0.89 | 1 | 4 |
| PHONE | 0.73 | 1.00 | 0.84 | 3 | 8 |
| EMAIL | 1.00 | 1.00 | 1.00 | 0 | 6 |
| NAME | 0.94 | 0.94 | 0.94 | 1 | 18 |
| ADDRESS | 1.00 | 0.71 | 0.83 | 0 | 7 |
| **Overall (micro)** | 0.91 | 0.94 | 0.92 | 5 | 51 |

## 3. Action choice

| Decider | Correct | Accuracy |
|---|---|---|
| Rule engine (`decide_action_rules`, no LLM) | 40/40 | 100% |
| Rule engine, content.js-faithful tokens (tokens + boolean hasValue) | 40/40 | 100% |
| Local LLM | not run (not reachable at http://localhost:11434/v1) | - |

Expected action policy: focus the first empty required field; else click the primary submit-like button; else scroll if the page says more content follows; else summarize. Password/CVV/PIN/OTP count as fields.

Note: the rules were revised after seeing this set's misses, so the rule-engine score is not held-out. The content.js-faithful row tokenises labelled/password fields even when empty, as the real extension does; emptiness is visible only through the boolean `hasValue` content.js sends.

| Expected action | Rule engine correct |
|---|---|
| click | 11/11 |
| focus | 16/16 |
| scroll | 3/3 |
| summarize | 10/10 |

## 4. Latency per page (CPU, ms)

| Stage | Median | p95 | Max |
|---|---|---|---|
| Regex + checksums (Py) | 0.1 | 0.2 | 0.3 |
| NER spaCy + rules (Py) | 20.5 | 37.1 | 42.6 |
| Rule-engine action (Py) | 0.1 | 0.2 | 0.3 |
| **Server total** | 20.6 | 37.3 | 42.9 |
| Extension JS regex + NER (Node) | 0.1 | 1.0 | 14.0 |

## 5. Errors (server, candidate mode)

| Page | Error | Type |
|---|---|---|
| kyc-04 | FP | NAME |
| kyc-05 | FP | AADHAAR |
| bank-02 | FP | CARD |
| bank-04 | FP | AADHAAR |
| bank-04 | FP | NAME |
| bank-04 | FP | AADHAAR |
| bank-04 | FP | NAME |
| bank-05 | FP | CARD |
| bank-06 | FP | CARD |
| rail-02 | FP | PHONE |
| rail-04 | FP | PHONE |
| job-02 | FP | NAME |
| job-02 | FP | NAME |
| co-01 | FN | ADDRESS |
| co-01 | FN | ADDRESS |
| co-03 | FP | AADHAAR |
| co-04 | FP | CARD |
| co-04 | FP | NAME |
| co-05 | FP | AADHAAR |
| co-05 | FP | NAME |
| news-02 | FP | AADHAAR |
| shop-01 | FP | CARD |
| wiki-01 | FP | NAME |
| stats-01 | FP | AADHAAR |
| support-01 | FP | PHONE |

### Rule-engine action misses

None.

### Rule-engine action misses, content.js-faithful tokens

None.
