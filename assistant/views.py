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


# Stopwords to drop — English + Arabic (so questions in either language work)
STOPWORDS = {
    "what", "is", "the", "a", "an", "please", "tell", "me", "about",
    "give", "show", "for", "of", "to", "in", "on", "and", "with", "?",
    "who", "when", "where", "why", "how", "are", "was", "were", "do", "does",
    "ما", "هو", "هي", "من", "عن", "في", "على", "الى", "إلى", "مع", "و",
    "ايش", "أيش", "وش", "كيف", "متى", "وين", "أين", "ليش", "لماذا", "هل",
    "اعطني", "أعطني", "اعرض", "وضح", "قل", "لي", "بخصوص", "ماهو", "ماهي",
}


def retrieve_chunks(query: str, k: int = 5):
    qtext = (query or "").lower()
    # supports Arabic letters (؀-ۿ) plus English and digits
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

    if "decision" in keywords or "قرار" in keywords or "قرارات" in keywords:
        q_obj |= Q(text__icontains="DECISION:") | Q(document__content__icontains="DECISION:")

    # Order by recency: newest first
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
            "_context": ch.text[:900],  # longer text for the LLM only (not shown)
        })
    return results


def _build_context(sources):
    """Build the numbered source block passed to the model."""
    lines = []
    for i, s in enumerate(sources, start=1):
        meta = f"[{i}] {s['title']} — ({s['doc_type']})"
        if s.get("date"):
            meta += f" | date: {s['date']}"
        if s.get("meeting_id"):
            meta += f" | meeting: {s['meeting_id']}"
        lines.append(meta + "\n" + s["_context"])
    return "\n\n".join(lines)


SYSTEM_PROMPT = (
    "You are the WARF assistant. Answer ONLY using the sources provided below.\n"
    "- Do not invent any information that is not in the sources. If the sources "
    "are insufficient, say so clearly.\n"
    "- Answer in English (unless the user clearly asks in another language, then "
    "match their language).\n"
    "- Cite the source number in brackets like [1] after each fact you use.\n"
    "- The sources are ordered newest to oldest. If information conflicts, prefer "
    "the most recent source and mention its date.\n"
    "- Be concise and direct."
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
            "answer": "I couldn't find a clear match in the current knowledge base. "
                      "Try different keywords (e.g., decision, problem, tasks) or "
                      "rephrase your question.",
            "sources": [],
        })

    context = _build_context(sources)
    user_prompt = f"Sources:\n\n{context}\n\n---\nUser question: {question}"

    try:
        answer = bedrock_chat(
            system_prompt=SYSTEM_PROMPT,
            user_text=user_prompt,
            temperature=0.2,
            max_tokens=700,
        ).strip()
    except Exception as e:
        # Bedrock unreachable — log the real reason, then show the actual
        # content (PROBLEM / DECISION / TASKS ...) from each source so the
        # answer is still readable without the LLM.
        logger.exception("Bedrock call failed: %s", e)
        answer_lines = ["Here is the relevant information from the WARF Knowledge Base:\n"]
        for i, s in enumerate(sources, start=1):
            date = f"  ({s['date']})" if s.get("date") else ""
            answer_lines.append(f"— Source [{i}]{date} —")
            answer_lines.append(s.get("snippet", "").strip())
            answer_lines.append("")
        answer = "\n".join(answer_lines).strip()

    # Do not return _context to the frontend
    public_sources = [
        {kk: vv for kk, vv in s.items() if kk != "_context"}
        for s in sources
    ]

    return JsonResponse({
        "ok": True,
        "answer": answer,
        "sources": public_sources,
    })
