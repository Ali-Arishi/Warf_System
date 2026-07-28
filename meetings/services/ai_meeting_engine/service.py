from typing import Dict, Any
import re


def _local_summary(text: str) -> str:
    """Simple extractive summary used when the LLM is unavailable."""
    text = (text or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(s.strip() for s in sentences[:6] if s.strip())
    return summary[:900]


def _local_decisions(text: str) -> dict:
    """Naive decision/action extraction used when the LLM is unavailable."""
    lines = [l.strip(" -*\t") for l in (text or "").splitlines() if l.strip()]
    dec_kw = re.compile(r"\b(decid|agree|approv|conclud|resolv)\w*", re.I)
    act_kw = re.compile(r"\b(will|must|should|assign|responsible|action|task|deadline|due)\w*", re.I)
    decisions = [l for l in lines if dec_kw.search(l)][:10]
    actions = [l for l in lines if act_kw.search(l) and l not in decisions][:10]
    action_items = [{"title": a, "assignee": None, "due_date": None, "priority": "medium"} for a in actions]
    return {"decisions": decisions, "action_items": action_items, "risks": [], "notes": []}


def run_ai(meeting_id: str, minutes_text: str) -> Dict[str, Any]:
    """
    WARF unified AI interface.

    Tries the Bedrock pipeline first; if the LLM is unreachable (missing keys,
    model not enabled, etc.) it falls back to a local extractive summary so the
    feature keeps working.
    """
    minutes_text = (minutes_text or "").strip()
    if not minutes_text:
        return {"summary": "", "decisions": {}}

    try:
        from .pipeline import run_pipeline
        result = run_pipeline(meeting_id=str(meeting_id), transcript=minutes_text)
        return {
            "summary": result.get("summary", ""),
            "decisions": result.get("decisions", {}),
        }
    except Exception:
        # LLM unavailable — local fallback so summarization still works
        return {
            "summary": _local_summary(minutes_text),
            "decisions": _local_decisions(minutes_text),
        }
