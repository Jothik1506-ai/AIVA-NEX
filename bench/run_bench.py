"""
AIVA-NEX accuracy benchmark (synthetic, self-labelled data - see README.md).

Run from the repo root:
    server\\.venv\\Scripts\\python bench\\run_bench.py [--no-js] [--no-llm]

Scores:
  1. Python privacy layers: server/pii_checks.py (regex + checksums) and
     server/ner/ (spaCy + Indian rules) - per-type precision / recall / F1.
  2. Extension JS layers (extension/pii-checks.js + ner-rules.js) via Node.
  3. Action choice: server main.decide_action_rules (rule engine), and the
     local LLM too if LOCAL_LLM_BASE_URL answers.
Writes bench/results.md and bench/results.json.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server")
sys.path.insert(0, SERVER)

from pii_checks import detect_pii  # noqa: E402
from ner.detector import detect_batch  # noqa: E402
from ner import model as ner_model  # noqa: E402

TYPES = ["AADHAAR", "PAN", "CARD", "PHONE", "EMAIL", "NAME", "ADDRESS"]
REGEX_TYPES = TYPES[:5]


# --------------------------------------------------------------------------- scoring
def gt_spans(item):
    spans = []
    for e in item["entities"]:
        s = item["text"].find(e["value"])
        if s < 0:
            raise ValueError(f"entity {e['value']!r} not in text {item['text']!r}")
        spans.append({"type": e["type"], "start": s, "end": s + len(e["value"])})
    return spans


class Counter:
    def __init__(self):
        self.c = {t: {"tp": 0, "fp": 0, "fn": 0} for t in TYPES}
        self.misses = []  # (page, kind, type, detail)

    def add(self, page_id, gts, preds, types=TYPES):
        gts = [g for g in gts if g["type"] in types]
        preds = [p for p in preds if p["type"] in types]
        used = set()
        for g in gts:
            hit = None
            for i, p in enumerate(preds):
                if i not in used and p["type"] == g["type"] and p["start"] < g["end"] and p["end"] > g["start"]:
                    hit = i
                    break
            if hit is None:
                self.c[g["type"]]["fn"] += 1
                self.misses.append((page_id, "FN", g["type"]))
            else:
                used.add(hit)
                self.c[g["type"]]["tp"] += 1
        for i, p in enumerate(preds):
            if i not in used:
                self.c[p["type"]]["fp"] += 1
                self.misses.append((page_id, "FP", p["type"]))

    def table(self, types):
        rows, T = [], {"tp": 0, "fp": 0, "fn": 0}
        for t in types:
            c = self.c[t]
            for k in T:
                T[k] += c[k]
            rows.append((t, *prf(c), c["tp"] + c["fn"]))
        rows.append(("**Overall (micro)**", *prf(T), T["tp"] + T["fn"]))
        return rows


def prf(c):
    p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else float("nan")
    r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else float("nan")
    f = 2 * p * r / (p + r) if p == p and r == r and p + r else float("nan")
    return p, r, f, c["fp"]


def fmt(x):
    return "n/a" if x != x else f"{x:.2f}"


# --------------------------------------------------------------------------- actions
# Label hints copied from extension/content.js LABEL_HINTS / classifyByHint.
_LABEL_HINTS = [("OTP", r"otp|one[\s-]?time[\s-]?code"), ("ID_NUMBER", r"aadhaar|aadhar"),
                ("ID_NUMBER", r"\bpan\b"), ("EMAIL", r"e[\s-]?mail"),
                ("PHONE", r"phone|mobile|contact\s*number"), ("CARD", r"card\s*(number)?|credit|debit"),
                ("ADDRESS", r"address"), ("PERSON", r"\bname\b")]


def _content_js_value(it, counters):
    """(isSensitive, sanitizedValue) as content.js analyzeField emits it.

    A password field or a label-classified field is ALWAYS a bare token, even
    when empty, so the server cannot tell empty from filled for those fields."""
    def tok(base, literal):
        counters[base] = counters.get(base, 0) + 1
        if literal:
            return base if counters[base] == 1 else f"{base}_{counters[base]}"
        return f"{base}_{counters[base]}"
    if it["inputType"] == "password":
        return True, tok("PASSWORD_FIELD", True)
    hint = it["label"] or ""
    for typ, rx in _LABEL_HINTS:
        if typ == "PERSON" and re.search(r"user\s*name", hint, re.I):
            continue
        if re.search(rx, hint, re.I):
            return True, (tok("OTP_FIELD", True) if typ == "OTP" else tok(typ, False))
    if it["entities"] and it["text"]:
        return True, tok(it["entities"][0]["type"], False)
    return False, it["text"]


def build_graph(page, faithful=False):
    """Screen graph shaped like extension/content.js output (values already tokenised).

    Default: a filled sensitive field becomes "[REDACTED]" and an empty one stays
    "", so the graph reveals emptiness. faithful=True reproduces content.js
    tokenisation, where label-classified and password fields are tokens whether
    or not they hold a value, plus the boolean hasValue content.js sends."""
    inputs, snippets, headings = [], [], []
    labels = {}
    counters = {}
    for it in page["items"]:
        if it["kind"] == "input":
            ref = f"input-{len(inputs)}"
            if faithful:
                sens, val = _content_js_value(it, counters)
            else:
                sens = bool(it["entities"]) or it["inputType"] == "password"
                val = it["text"]
                if sens and val:
                    val = "[REDACTED]"
            inputs.append({"ref": ref, "type": it["inputType"], "label": it["label"],
                           "required": it["required"], "isSensitive": sens,
                           "sanitizedValue": val})
            if faithful:  # content.js sends a boolean hasValue alongside the token
                inputs[-1]["hasValue"] = str(it["text"] or "").strip() != ""
            labels[ref] = it["label"]
        elif it["kind"] == "heading":
            headings.append(it["text"])
        else:
            snippets.append(it["text"])
    buttons = []
    for i, b in enumerate(page["buttons"]):
        buttons.append({"ref": f"button-{i}", "text": b})
        labels[f"button-{i}"] = b
    graph = {"pageTitle": page["title"], "domain": "example.test", "headings": headings,
             "textSnippets": snippets, "inputs": inputs, "buttons": buttons, "links": [],
             "sensitiveItemsCount": sum(1 for x in inputs if x["isSensitive"])}
    return graph, labels


def action_correct(pred, labels, expected):
    if pred.get("action") != expected["action"]:
        return False
    if expected["action"] in ("click", "focus"):
        return labels.get(pred.get("targetRef")) == expected["target"]
    return True


def llm_reachable():
    base = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/models", timeout=2) as r:
            return r.status == 200, base
    except Exception:
        return False, base


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-js", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    with open(os.path.join(HERE, "dataset.json"), encoding="utf-8") as f:
        data = json.load(f)
    pages = data["pages"]

    t0 = time.perf_counter()
    ner_model.get_nlp()
    load_ms = (time.perf_counter() - t0) * 1000
    engine = ner_model.engine_name()
    detect_batch(["Warm up Ravi Kumar, Hyderabad 500080"])

    import main as srv  # server/main.py (FastAPI app; we only call its functions)

    py_cand, py_val, js_cand, js_val = Counter(), Counter(), Counter(), Counter()
    lat_pii, lat_ner, lat_act, lat_total, lat_js = [], [], [], [], []
    rule_ok, rule_rows = 0, []
    faithful_ok, faithful_rows = 0, []
    use_llm, llm_base = (False, None) if args.no_llm else llm_reachable()
    llm_ok, lat_llm, llm_used = 0, [], None

    js = None
    node = shutil.which("node")
    if not args.no_js and node:
        res = subprocess.run([node, os.path.join(HERE, "js_harness.js")], capture_output=True, text=True, check=True)
        js = json.loads(res.stdout)

    for p in pages:
        texts = [it["text"] for it in p["items"]]
        a = time.perf_counter()
        regex = [detect_pii(t) for t in texts]
        b = time.perf_counter()
        ner = detect_batch(texts)
        c = time.perf_counter()
        graph, labels = build_graph(p)
        act = srv.decide_action_rules(graph)
        d = time.perf_counter()
        lat_pii.append((b - a) * 1000)
        lat_ner.append((c - b) * 1000)
        lat_act.append((d - c) * 1000)
        lat_total.append((d - a) * 1000)

        for i, it in enumerate(p["items"]):
            gts = gt_spans(it)
            nspans = [{"type": s.type, "start": s.start, "end": s.end} for s in ner[i]]
            py_cand.add(p["id"], gts, regex[i] + nspans)
            py_val.add(p["id"], gts, [r for r in regex[i] if r["validated"]] + nspans)
            if js:
                jsp = js[p["id"]]["items"][i]
                js_cand.add(p["id"], gts, jsp)
                js_val.add(p["id"], gts, [s for s in jsp if s["validated"]])
        if js:
            lat_js.append(js[p["id"]]["ms"])

        ok = action_correct(act, labels, p["expected"])
        rule_ok += ok
        fgraph, _ = build_graph(p, faithful=True)
        fact = srv.decide_action_rules(fgraph)
        fok = action_correct(fact, labels, p["expected"])
        faithful_ok += fok
        faithful_rows.append((p["id"], p["expected"], fact, labels, fok))
        rule_rows.append((p["id"], p["expected"], act, labels, ok))

        if use_llm:
            e = time.perf_counter()
            la = srv.call_local_llm(graph)
            lat_llm.append((time.perf_counter() - e) * 1000)
            if la is not None:
                llm_used = la.pop("_model", None) or llm_used
                llm_ok += action_correct(la, labels, p["expected"])

    n = len(pages)
    pct = lambda xs, q: sorted(xs)[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]  # noqa: E731
    n_items = sum(len(p["items"]) for p in pages)
    n_ents = sum(len(it["entities"]) for p in pages for it in p["items"])

    L = []
    L.append("# AIVA-NEX accuracy benchmark - results\n")
    L.append("> **Synthetic, self-labelled data.** 40 invented Indian web pages written by the benchmark "
             "author (test Aadhaar/cards, invented names). Detectors were NOT tuned to this set. "
             "Treat numbers as an indicative sanity check, not a field evaluation.\n")
    L.append(f"- Pages: **{n}** ({n_items} text items, {n_ents} labelled PII spans; "
             f"{sum(1 for p in pages if p['category'] == 'distractor')} distractor pages with no PII)")
    L.append(f"- Server NER engine: `{engine}` (cold load {load_ms:.0f} ms)")
    L.append("- Match rule: predicted span counts as correct if same type and overlapping a labelled span.")
    L.append("- *Candidate* mode = every regex hit is tokenised (what the extension does). "
             "*Validated* mode = only checksum/format-validated hits (what the server rejects with 400).\n")

    def sec(title, cnt, types):
        L.append(f"### {title}\n")
        L.append("| Type | Precision | Recall | F1 | FP | Support |")
        L.append("|---|---|---|---|---|---|")
        for t, pp, rr, ff, fp, sup in cnt.table(types):
            L.append(f"| {t} | {fmt(pp)} | {fmt(rr)} | {fmt(ff)} | {fp} | {sup} |")
        L.append("")

    L.append("## 1. PII detection - server (Python: pii_checks.py + ner/)\n")
    sec("Candidate mode (regex hits + NER)", py_cand, TYPES)
    sec("Validated mode (checksum-validated regex + NER)", py_val, TYPES)
    if js:
        L.append("## 2. PII detection - extension (JS: pii-checks.js + ner-rules.js, Node)\n")
        sec("Candidate mode", js_cand, TYPES)
        sec("Validated mode", js_val, TYPES)
    else:
        L.append("## 2. Extension JS detectors\n\nSkipped (node not found or --no-js).\n")

    L.append("## 3. Action choice\n")
    L.append("| Decider | Correct | Accuracy |")
    L.append("|---|---|---|")
    L.append(f"| Rule engine (`decide_action_rules`, no LLM) | {rule_ok}/{n} | {rule_ok / n:.0%} |")
    L.append(f"| Rule engine, content.js-faithful tokens (tokens + boolean hasValue) "
             f"| {faithful_ok}/{n} | {faithful_ok / n:.0%} |")
    if use_llm:
        L.append(f"| Local LLM `{llm_used}` @ {llm_base} | {llm_ok}/{n} | {llm_ok / n:.0%} |")
    else:
        L.append(f"| Local LLM | not run ({'disabled' if args.no_llm else 'not reachable at ' + str(llm_base)}) | - |")
    L.append("\nExpected action policy: " + data["meta"]["action_policy"] + "\n")
    L.append("Note: the rules were revised after seeing this set's misses, so the rule-engine score is not "
             "held-out. The content.js-faithful row tokenises labelled/password fields even when empty, as the "
             "real extension does; emptiness is visible only through the boolean `hasValue` content.js sends.\n")
    by_act = {}
    for _, exp, act, _, ok in rule_rows:
        k = exp["action"]
        by_act.setdefault(k, [0, 0])
        by_act[k][0] += ok
        by_act[k][1] += 1
    L.append("| Expected action | Rule engine correct |")
    L.append("|---|---|")
    for k, (a_, b_) in sorted(by_act.items()):
        L.append(f"| {k} | {a_}/{b_} |")
    L.append("")

    L.append("## 4. Latency per page (CPU, ms)\n")
    L.append("| Stage | Median | p95 | Max |")
    L.append("|---|---|---|---|")
    stages = [("Regex + checksums (Py)", lat_pii), ("NER spaCy + rules (Py)", lat_ner),
              ("Rule-engine action (Py)", lat_act), ("**Server total**", lat_total)]
    if js:
        stages.append(("Extension JS regex + NER (Node)", lat_js))
    if use_llm and lat_llm:
        stages.append(("Local LLM action", lat_llm))
    for name, xs in stages:
        L.append(f"| {name} | {statistics.median(xs):.1f} | {pct(xs, 0.95):.1f} | {max(xs):.1f} |")
    L.append("")

    L.append("## 5. Errors (server, candidate mode)\n")
    L.append("| Page | Error | Type |")
    L.append("|---|---|---|")
    for pid, kind, t in py_cand.misses:
        L.append(f"| {pid} | {kind} | {t} |")
    for title, rows in (("Rule-engine action misses", rule_rows),
                        ("Rule-engine action misses, content.js-faithful tokens", faithful_rows)):
        L.append(f"\n### {title}\n")
        bad = [r for r in rows if not r[4]]
        if not bad:
            L.append("None.")
            continue
        L.append("| Page | Expected | Got |")
        L.append("|---|---|---|")
        for pid, exp, act, labels, ok in bad:
            got = act["action"] + (f" '{labels.get(act.get('targetRef'))}'" if act.get("targetRef") else "")
            L.append(f"| {pid} | {exp['action']} {repr(exp['target']) if exp['target'] else ''} | {got} |")
    L.append("")

    with open(os.path.join(HERE, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    summary = {
        "engine": engine, "pages": n,
        "py_candidate": {t: prf(py_cand.c[t])[:3] for t in TYPES},
        "py_validated": {t: prf(py_val.c[t])[:3] for t in TYPES},
        "js_candidate": {t: prf(js_cand.c[t])[:3] for t in TYPES} if js else None,
        "rule_action_acc": rule_ok / n,
        "rule_action_acc_faithful": faithful_ok / n,
        "llm": {"used": llm_used, "acc": llm_ok / n} if use_llm else None,
        "latency_ms_median": {"server_total": statistics.median(lat_total),
                              "js": statistics.median(lat_js) if js else None},
    }
    with open(os.path.join(HERE, "results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print("\n".join(L))


if __name__ == "__main__":
    main()
