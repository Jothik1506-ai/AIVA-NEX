"""Let /analyze act on perceived visual elements.

``attach_visual_target(action, graph)`` runs after main.decide_action():
  * the LLM/rule engine picked a DOM ref  -> attach the matching visual
    element's id + bbox so the extension can draw a highlight;
  * the LLM picked a visual id ("v7")      -> translate to its DOM ref if
    fused, otherwise keep targetVisualId + bbox (the extension clicks the
    bbox centre, for canvas/image-only controls with no DOM node);
  * the rule engine found nothing (summarize) -> try a visual-only rule.
"""

from typing import Any, Dict, List, Optional

PRIMARY_WORDS = ("submit", "continue", "next", "login", "log in", "sign in", "pay", "proceed", "apply", "place order")


def _visual_target_fields(el: Dict[str, Any]) -> Dict[str, Any]:
    out = {"targetVisualId": el["id"], "bbox": el.get("bbox_css") or el["bbox"], "visualText": el.get("text", "")}
    if el.get("bbox_css") is None:
        out["bboxSpace"] = "image"
    return out


def decide_visual_action(elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for el in elements:
        txt = (el.get("text") or "").lower()
        if el.get("type_guess") == "button" and any(w in txt for w in PRIMARY_WORDS):
            return {
                "action": "click",
                "reason": f"Visual perception found a primary button ('{el.get('text')}').",
                **_visual_target_fields(el),
            }
    for el in elements:
        if el.get("type_guess") == "input":
            label = el.get("label") or el.get("text") or "input"
            return {
                "action": "focus",
                "reason": f"Visual perception found an input field ('{label}').",
                **_visual_target_fields(el),
            }
    return None


def attach_visual_target(action: Dict[str, Any], graph: Dict[str, Any]) -> Dict[str, Any]:
    elements = graph.get("visualElements") or []
    if not elements or not isinstance(action, dict):
        return action
    by_id = {e.get("id"): e for e in elements if isinstance(e, dict)}
    by_ref = {e.get("domRef"): e for e in elements if isinstance(e, dict) and e.get("domRef")}

    target = action.get("targetRef")
    if target and target in by_id:
        el = by_id[target]
        action = {**action, **_visual_target_fields(el)}
        if el.get("domRef"):
            action["targetRef"] = el["domRef"]
        else:
            action.pop("targetRef", None)
        return action
    if target and target in by_ref:
        return {**action, **_visual_target_fields(by_ref[target])}

    if action.get("action") == "summarize":
        visual = decide_visual_action(elements)
        if visual:
            el = by_id.get(visual["targetVisualId"], {})
            if el.get("domRef"):
                visual["targetRef"] = el["domRef"]
            by = action.get("decidedBy", "rule-engine")
            return {**visual, "decidedBy": f"{by}+vision"}
        if graph.get("visualSummary"):
            return {**action, "summary": f"{action.get('summary', '')} Visual: {graph['visualSummary']}".strip()}
    return action


def visual_refs(graph: Dict[str, Any]) -> set:
    """Visual ids the LLM may legally target (merged into main._known_refs)."""
    return {e.get("id") for e in graph.get("visualElements") or [] if isinstance(e, dict) and e.get("id")}


__all__ = ["attach_visual_target", "decide_visual_action", "visual_refs"]
