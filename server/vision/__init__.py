"""On-device visual perception for Aiva Nex Agent (SIH26171).

Public API used by main.py:
    build_router(pii_patterns)          -> APIRouter with POST /perceive
    attach_visual_target(action, graph) -> ties /analyze actions to visual elements
    visual_refs(graph)                  -> visual ids an LLM may target
"""

from .decide import attach_visual_target, decide_visual_action, visual_refs
from .pipeline import perceive
from .routes import build_router

__all__ = ["build_router", "attach_visual_target", "decide_visual_action", "visual_refs", "perceive"]
