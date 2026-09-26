"""Shared span type for the NER package."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    type: str  # "NAME" | "ADDRESS"
    score: float
    source: str = ""  # which rule / model produced it (debug only, never the text)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 3)
        return d
