import json
from . import config
from .bedrock_client import chat

JSON_SCHEMA_HINT = """
Return ONLY valid JSON (no markdown, no code fences).
Schema:
{
  "decisions": [string],
  "action_items": [
    { "title": string, "assignee": string|null, "due_date": string|null, "priority": "low"|"medium"|"high" }
  ],
  "risks": [string],
  "notes": [string]
}
Rules:
- If assignee not mentioned, use null.
- If due date not mentioned, use null.
- Keep items short and actionable.
"""


def extract_decisions(meeting_id: str, transcript: str) -> dict:
    prompt = f"""{JSON_SCHEMA_HINT}

Meeting ID: {meeting_id}

Transcript:
{transcript}
"""

    content = chat(
        system_prompt="You extract structured decisions and action items from meeting transcripts.",
        user_text=prompt,
        temperature=config.TEMPERATURE_DECISIONS,
        max_tokens=config.MAX_TOKENS_DECISIONS,
    ).strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {
            "decisions": [],
            "action_items": [],
            "risks": [],
            "notes": [],
            "_raw": content
        }

    return {"meeting_id": meeting_id, "output": data}
