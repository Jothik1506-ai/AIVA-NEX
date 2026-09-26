"""FastAPI routes for visual perception: POST /perceive, GET /perceive/health."""

from typing import Any, Callable, Dict, List, Optional, Pattern

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .ocr import ocr_status, warmup_async
from .pii import set_pii_patterns, set_text_sanitizer
from .pipeline import PerceptionError, perceive


class PerceiveRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: str  # base64 JPEG/PNG (optionally a data: URL), ALREADY masked in the extension
    domElements: Optional[List[Dict[str, Any]]] = None  # sanitized {ref, kind, text, x, y, width, height} in CSS px
    viewport: Optional[Dict[str, Any]] = None  # {width, height} in CSS px, maps image px -> CSS px
    masked: Optional[bool] = None  # extension asserts it applied on-device masking


def build_router(
    pii_patterns: Optional[Dict[str, Pattern]] = None,
    warmup: bool = True,
    text_sanitizer: Optional[Callable] = None,
) -> APIRouter:
    set_pii_patterns(pii_patterns)
    set_text_sanitizer(text_sanitizer)
    if warmup:
        warmup_async()
    router = APIRouter(tags=["vision"])

    @router.get("/perceive/health")
    def perceive_health():
        return ocr_status()

    @router.post("/perceive")
    def perceive_route(req: PerceiveRequest):
        try:
            return perceive(req.image, req.domElements, req.viewport)
        except PerceptionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
