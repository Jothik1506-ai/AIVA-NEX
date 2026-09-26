"""
On-device NER for personal NAMES and ADDRESSES (Aiva Nex Agent, SIH26171).

    from ner import detect_names_addresses, tokenize_text, enforce_ner_policy

Model: spaCy en_core_web_sm (CPU) + Indian name/address rule layer.
Everything runs locally; no text is logged or sent anywhere.
"""

from .api import enforce_ner_policy, ner_policy, router, sanitize_texts
from .detector import detect_batch, detect_names_addresses, detect_spans
from .model import engine_name, get_nlp, warm_up_async
from .tokenizer import Tokenizer, sanitize_payload, tokenize_text
from .types import Span

__all__ = [
    "Span",
    "Tokenizer",
    "detect_batch",
    "detect_names_addresses",
    "detect_spans",
    "engine_name",
    "enforce_ner_policy",
    "get_nlp",
    "ner_policy",
    "router",
    "sanitize_payload",
    "sanitize_texts",
    "tokenize_text",
    "warm_up_async",
]
