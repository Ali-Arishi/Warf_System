from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.utils import timezone
import re
import logging

from records.models import KnowledgeChunk

logger = logging.getLogger(__name__)

# Unified Bedrock client (same one used by the meeting engine)
from meetings.services.ai_meeting_engine.bedrock_client import chat as bedrock_chat


@login_required
def chat_view(request):
    return render(request, "assistant/chat.html")


# ------------------------------------------------------------------ retrieval

STOPWORDS = {
    "what", "is", "the", "a", "an", "please", "tell", "me", "about",
    "give", "show", "for", "of", "to", "in", "on", "and", "with", "?",
    "who", "when", "where", "why", "how", "are", "was", "were", "do", "does",
    "can", "you", "create", "my", "last", "an", "professional",
    "ما", "هو", "هي", "من", "عن", "في", "على", "الى", "إلى", "مع", "و",
    "ايش", "أيش", "وش", "كيف", "متى", "وين", "أين", "ليش", "لماذا", "هل",
    "اعطني", "أعطني", "اعرض", "وضح", "قل", "لي", "بخصوص", "ماهو", "ماهي",
}


def retrieve_chunks(query: str, k: int = 5):
    qtext = (query or "").lower()
    words = re.findall(r"[\w؀-ۿ]+", qtext, flags=re.UNICODE)
    keywords = [w for w in words if w not in STOPWORDS]
    if not keywords:
        keywords = [qtext.strip()] if qtext.strip() else []

    q_obj = Q()
    for kw in keywords[:8]:
        q_obj |= (
            Q(text__icontains=kw)
            | Q(document__title__icontains=kw)
            | Q(document__content__icontains=kw)
        )
    # broad fallbacks so generic requests (MOM, email, summary) still match
    if not q_obj:
        q_obj = Q(text__icontains="DECISION")
    q_obj |= Q(text__icontains="DECISION:")

    qs = (
        KnowledgeChunk.objects.select_related("document")
        .filter(q_obj)
        .order_by("-document__created_at", "-created_at")[:k]
    )

    results = []
    for ch in qs:
        doc = ch.document
        created = doc.created_at
        results.append({
            "title": doc.title,
            "doc_type": doc.doc_type,
            "meeting_id": doc.external_meeting_id,
            "date": created.strftime("%Y-%m-%d") if created else None,
            "days_ago": (timezone.now() - created).days if created else None,
            "snippet": ch.text[:650],
            "full": ch.text,            # untruncated, used by parser + LLM
        })
    return results


# ------------------------------------------------------- parse structured data

_SECTION_RE = re.compile(
    r"PROBLEM:\s*(?P<problem>.*?)\s*"
    r"OPTIONS:\s*(?P<options>.*?)\s*"
    r"DECISION:\s*(?P<decision>.*?)\s*"
    r"JUSTIFICATION:\s*(?P<justification>.*?)\s*"
    r"CONFIDENCE:\s*(?P<confidence>.*?)\s*"
    r"TASKS:\s*(?P<tasks>.*)",
    re.DOTALL,
)


def _parse_seed(text):
    m = _SECTION_RE.search(text or "")
    if not m:
        return None
    d = {kk: (vv or "").strip() for kk, vv in m.groupdict().items()}
    return d


def _clean(v):
    v = (v or "").strip()
    return v if v and v.lower() != "(empty)" else "-"


def _fmt_tasks(tasks):
    t = (tasks or "").strip()
    return t if t and t != "- (none)" else "- (none recorded)"


# ------------------------------------------------------- compose (no-LLM path)

def _compose_local(question, sources):
    q = (question or "").lower()
    parsed = []
    for s in sources:
        p = _parse_seed(s.get("full", ""))
        if p:
            p["_date"] = s.get("date")
            p["_id"] = s.get("meeting_id")
            p["_i"] = len(parsed) + 1
            parsed.append(p)

    # Not structured seed knowledge — just show readable snippets
    if not parsed:
        out = ["Here is the relevant information from the knowledge base:\n"]
        for i, s in enumerate(sources, start=1):
            out.append(f"— Source [{i}] —")
            out.append(s.get("snippet", "").strip())
            out.append("")
        return "\n".join(out).strip()

    # ----- Minutes of Meeting
    if any(w in q for w in ["mom", "minute", "محضر"]):
        p = parsed[0]
        return (
            "**Minutes of Meeting (MOM)**\n"
            f"Date: {p.get('_date') or '-'}\n"
            f"Meeting ID: {p.get('_id') or '-'}\n\n"
            "**1. Problem / Context**\n" + _clean(p.get("problem")) + "\n\n"
            "**2. Options Considered**\n" + _clean(p.get("options")) + "\n\n"
            "**3. Decision**\n" + _clean(p.get("decision")) + "\n\n"
            "**4. Justification**\n" + _clean(p.get("justification")) + "\n\n"
            "**5. Action Items**\n" + _fmt_tasks(p.get("tasks"))
        )

    # ----- Professional email
    if any(w in q for w in ["email", "mail", "ايميل", "إيميل", "بريد", "رسالة"]):
        p = parsed[0]
        return (
            "Subject: Follow-up on Recent Meeting Decision\n\n"
            "Dear Team,\n\n"
            "Following our recent meeting, here is a summary of the outcome and "
            "the next steps.\n\n"
            "Context: " + _clean(p.get("problem")) + "\n\n"
            "Decision: " + _clean(p.get("decision")) + "\n\n"
            "Rationale: " + _clean(p.get("justification")) + "\n\n"
            "Action items:\n" + _fmt_tasks(p.get("tasks")) + "\n\n"
            "Please review and share any blockers.\n\n"
            "Best regards,\nWARF"
        )

    # ----- Tasks / action items
    if any(w in q for w in ["task", "action", "مهام", "مهمة", "اكشن"]):
        out = ["**Action items across the matched meetings:**\n"]
        found = False
        for p in parsed:
            t = (p.get("tasks") or "").strip()
            if t and t != "- (none)":
                found = True
                out.append(f"From meeting {p.get('_date') or ''} [source {p['_i']}]:")
                out.append(t)
                out.append("")
        if not found:
            out.append("No action items were recorded in the matched meetings.")
        return "\n".join(out).strip()

    # ----- Default: summary of decisions
    out = ["**Summary of key decisions across the matched meetings:**\n"]
    n = 0
    for p in parsed:
        dec = _clean(p.get("decision"))
        if dec == "-":
            continue
        n += 1
        date = f" ({p['_date']})" if p.get("_date") else ""
        out.append(f"{n}. {dec}{date}  [source {p['_i']}]")
    if n == 0:
        out.append("No explicit decisions were recorded in the matched meetings.")
        for p in parsed:
            out.append(f"- Problem [source {p['_i']}]: {_clean(p.get('problem'))[:200]}")
    return "\n".join(out).strip()


# ------------------------------------------------------------------- LLM setup

def _build_context(sources):
    lines = []
    for i, s in enumerate(sources, start=1):
        meta = f"[{i}] {s['title']} — ({s['doc_type']})"
        if s.get("date"):
            meta += f" | date: {s['date']}"
        lines.append(meta + "\n" + s["full"])
    return "\n\n".join(lines)


SYSTEM_PROMPT = (
    "You are the WARF assistant for meeting knowledge. Use ONLY the sources "
    "provided. If asked to write a MOM (minutes of meeting), an email, a summary, "
    "or a task list, produce a clean, well-structured, professional document based "
    "on the sources.\n"
    "- Do not invent facts not in the sources. If insufficient, say so.\n"
    "- Answer in the language of the user's question (English or Arabic).\n"
    "- Cite the source number like [1] where relevant.\n"
    "- Prefer the most recent source when information conflicts."
)


@login_required
@require_POST
def ask_api(request):
    question = (request.POST.get("question") or "").strip()
    if not question:
        return JsonResponse({"ok": False, "error": "Empty question"}, status=400)

    sources = retrieve_chunks(question, k=5)

    if not sources:
        return JsonResponse({
            "ok": True,
            "answer": "I couldn't find a clear match in the current knowledge "
                      "base. Try different keywords (e.g., decision, problem, "
                      "tasks) or rephrase your question.",
            "sources": [],
        })

    context = _build_context(sources)
    user_prompt = f"Sources:\n\n{context}\n\n---\nUser request: {question}"

    try:
        answer = bedrock_chat(
            system_prompt=SYSTEM_PROMPT,
            user_text=user_prompt,
            temperature=0.3,
            max_tokens=900,
        ).strip()
    except Exception as e:
        # Bedrock unavailable — log the real reason, then compose locally so the
        # user still gets a clean, structured answer.
        logger.exception("Bedrock call failed: %s", e)
        answer = _compose_local(question, sources)

    public_sources = [
        {kk: vv for kk, vv in s.items() if kk not in ("full",)}
        for s in sources
    ]

    return JsonResponse({
        "ok": True,
        "answer": answer,
        "sources": public_sources,
    })
