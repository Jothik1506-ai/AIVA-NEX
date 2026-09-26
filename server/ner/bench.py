"""
CPU latency benchmark:  cd server && python -m ner.bench

Uses synthetic, invented text only. Prints model load time and the average
time to detect NAME/ADDRESS spans in a 2 KB text and in a typical screen graph.
"""

import statistics
import time

from . import model
from .detector import detect_spans
from .tokenizer import sanitize_payload

_BASE = (
    "Ravi Kumar, H.No 12-3-45, Gandhi Nagar, Hyderabad 500080. Contact Priya Nair for details. "
    "Buy Samsung Galaxy M14 5G at best price. Showing results for flights to Delhi. "
    "Please deliver to Smt. Lakshmi Devi, Flat 4B, Sai Residency, Ameerpet Road, Telangana 500016. "
)


def _time(fn, n):
    fn()  # warm
    runs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(runs), max(runs)


def main():
    t0 = time.perf_counter()
    model.get_nlp()
    load_ms = (time.perf_counter() - t0) * 1000
    text = (_BASE * 20)[:2048]
    graph = {
        "pageTitle": "Checkout",
        "headings": ["Delivery address", "Order summary"],
        "textSnippets": [s.strip() + "." for s in _BASE.split(".") if s.strip()] * 4,
        "buttons": [{"ref": f"button-{i}", "text": t} for i, t in enumerate(["Submit", "Sign in", "Add to cart"])],
    }
    med, worst = _time(lambda: detect_spans(text), 30)
    gmed, gworst = _time(lambda: sanitize_payload(graph), 30)
    print(f"engine            : {model.engine_name()}")
    print(f"model load (cold) : {load_ms:.0f} ms")
    print(f"2 KB text         : median {med:.1f} ms, max {worst:.1f} ms")
    print(f"screen graph ({len(graph['textSnippets'])} snippets): median {gmed:.1f} ms, max {gworst:.1f} ms")


if __name__ == "__main__":
    main()
