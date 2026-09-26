"""
Statistical NER model: spaCy `en_core_web_sm` (CPU, ~12 MB), loaded lazily
exactly once per process and cached.

Only the `tok2vec` + `ner` pipes run (tagger/parser/lemmatizer disabled), which
keeps a 2 KB text at tens of milliseconds on a laptop CPU.

If spaCy or the model package is missing, or AIVA_NER_MODEL=none, the package
degrades to rules-only detection instead of failing the server.
"""

import logging
import os
import threading
from typing import List, Optional, Sequence, Tuple

log = logging.getLogger("aiva.ner")

MODEL_NAME = os.getenv("AIVA_NER_MODEL", "en_core_web_sm")
_DISABLE = ["tagger", "parser", "attribute_ruler", "lemmatizer", "senter"]

_lock = threading.Lock()
_nlp = None
_loaded = False

# (start, end, label) - labels are spaCy's: PERSON, GPE, LOC, FAC, ...
Ent = Tuple[int, int, str]


def get_nlp():
    """Return the cached spaCy pipeline, or None if unavailable."""
    global _nlp, _loaded
    if _loaded:
        return _nlp
    with _lock:
        if _loaded:
            return _nlp
        if MODEL_NAME.lower() in ("", "none", "off", "rules"):
            _nlp = None
        else:
            try:
                import spacy  # noqa: WPS433 (import inside function: optional dependency)

                _nlp = spacy.load(MODEL_NAME, disable=_DISABLE)
                log.info("NER model loaded: %s", MODEL_NAME)
            except Exception as exc:  # ImportError, OSError (model not downloaded) ...
                log.warning("NER model %s unavailable (%s); using rules only", MODEL_NAME, type(exc).__name__)
                _nlp = None
        _loaded = True
        return _nlp


def engine_name() -> str:
    return f"spacy:{MODEL_NAME}+rules" if get_nlp() is not None else "rules-only"


def model_entities(texts: Sequence[str]) -> List[List[Ent]]:
    """Run the model over a batch of texts; returns per-text entity lists."""
    nlp = get_nlp()
    if nlp is None:
        return [[] for _ in texts]
    out: List[List[Ent]] = []
    for doc in nlp.pipe(texts, batch_size=32):
        out.append([(e.start_char, e.end_char, e.label_) for e in doc.ents])
    return out


def warm_up_async() -> Optional[threading.Thread]:
    """Load the model in a background thread so the first request is fast."""
    if os.getenv("AIVA_NER_WARM", "1") == "0":
        return None

    def _warm():
        nlp = get_nlp()
        if nlp is not None:
            nlp("Warm up.")

    t = threading.Thread(target=_warm, name="ner-warmup", daemon=True)
    t.start()
    return t
