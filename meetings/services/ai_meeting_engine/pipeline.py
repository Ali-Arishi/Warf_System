import json
import os
from .summarizer import summarize_meeting
from .decision_extractor import extract_decisions

OUTPUT_DIR = "outputs"


def run_pipeline(meeting_id: str, transcript: str):
    summary = summarize_meeting(meeting_id, transcript)
    decisions = extract_decisions(meeting_id, transcript)

    output = {
        "meeting_id": meeting_id,
        "summary": summary["summary"],
        "decisions": decisions["output"],
        "created_at": summary["created_at"],
    }

    # Best-effort local dump; never fail the request if the FS is read-only.
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(f"{OUTPUT_DIR}/{meeting_id}.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return output
