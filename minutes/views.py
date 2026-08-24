import json
import ast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth import get_user_model

from meetings.models import Meeting
from .models import Minutes
from meetings.services.ai_meeting_engine.service import run_ai


def _save_minutes_to_knowledge(meeting, summary, payload):
    """
    Store the generated meeting summary + decisions into the RAG knowledge
    base so the WARF Assistant can retrieve it. Upserts one document per meeting.
    """
    from records.models import KnowledgeDocument, KnowledgeChunk

    parts = [
        f"MEETING MINUTES (Meeting: {meeting.title} | ID: {meeting.id})",
        "",
        "SUMMARY:",
        (summary or "").strip() or "-",
        "",
    ]
    payload = payload or {}
    decisions = payload.get("decisions") or []
    if decisions:
        parts.append("DECISIONS:")
        for d in decisions:
            parts.append(f"- {d}")
        parts.append("")
    action_items = payload.get("action_items") or payload.get("tasks") or []
    if action_items:
        parts.append("ACTION ITEMS:")
        for it in action_items:
            if isinstance(it, dict):
                parts.append(
                    f"- {it.get('title','')} | assignee: {it.get('assignee')} "
                    f"| due: {it.get('due_date')} | priority: {it.get('priority')}"
                )
            else:
                parts.append(f"- {it}")
        parts.append("")
    content = "\n".join(parts)

    doc = KnowledgeDocument.objects.filter(
        external_meeting_id=f"minutes-{meeting.id}", doc_type="minutes"
    ).first()
    if doc is None:
        doc = KnowledgeDocument.objects.create(
            title=f"Meeting Minutes - {meeting.title}",
            doc_type="minutes",
            content=content,
            external_meeting_id=f"minutes-{meeting.id}",
            visibility="internal",
        )
    else:
        doc.title = f"Meeting Minutes - {meeting.title}"
        doc.content = content
        doc.save(update_fields=["title", "content"])

    doc.chunks.all().delete()
    KnowledgeChunk.objects.create(document=doc, chunk_index=0, text=content)
    return doc

User = get_user_model()


def _is_admin(user):
    return user.is_staff or user.is_superuser


def _parse_ai_decisions(raw):
    """
    Supports:
    - JSON string
    - Python dict string (legacy: "{'action_items': ...}")
    Returns dict or {}.
    """
    if not raw:
        return {}

    if isinstance(raw, dict):
        return raw

    raw = str(raw).strip()

    # Try JSON
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Try python-literal dict (legacy)
    try:
        data = ast.literal_eval(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}


def _find_user_by_name(name: str):
    if not name:
        return None
    name = str(name).strip()
    if not name:
        return None

    u = User.objects.filter(username__iexact=name).first()
    if u:
        return u

    u = User.objects.filter(first_name__iexact=name).first()
    if u:
        return u

    u = User.objects.filter(last_name__iexact=name).first()
    if u:
        return u

    return None


def _extract_decisions_payload(result: dict) -> dict:
    """
    Normalizes AI engine output to a single dict:
    Expected schema:
    {
      "decisions": [...],
      "action_items": [...],
      "risks": [...],
      "notes": [...]
    }
    Your extractor returns: {"meeting_id": ..., "output": {...}}
    So this function returns the inner "output".
    """
    if not isinstance(result, dict):
        return {}

    # case 1: run_ai returns {"output": {...}}
    if isinstance(result.get("output"), dict):
        return result["output"]

    # case 2: run_ai returns {"decisions": {"output": {...}}}
    dec = result.get("decisions")
    if isinstance(dec, dict) and isinstance(dec.get("output"), dict):
        return dec["output"]

    # case 3: run_ai returns {"decisions": {...}} directly
    if isinstance(dec, dict) and ("action_items" in dec or "decisions" in dec):
        return dec

    return {}


@login_required
def minutes_home(request):
    return render(request, "minutes/minutes_home.html")


@login_required
def minutes_list(request):
    minutes_qs = (
        Minutes.objects
        .select_related("meeting", "created_by", "approved_by")
        .order_by("-updated_at")
    )

    # Employee sees approved only
    if not _is_admin(request.user):
        minutes_qs = minutes_qs.filter(status=Minutes.STATUS_APPROVED)

    return render(request, "minutes/minutes_list.html", {"minutes": minutes_qs})


@login_required
def minutes_for_meeting(request, meeting_id):
    meeting = get_object_or_404(Meeting, pk=meeting_id)

    minutes_obj, _ = Minutes.objects.get_or_create(
        meeting=meeting,
        defaults={"created_by": request.user}
    )

    # Employee cannot access non-approved minutes
    if not _is_admin(request.user) and minutes_obj.status != Minutes.STATUS_APPROVED:
        messages.error(request, "Minutes are not approved yet.")
        return redirect("minutes:list")

    if request.method == "POST":
        action = request.POST.get("action") or ""

        # ✅ Locked policy:
        # Block any POST action when locked EXCEPT generate_tasks
        if minutes_obj.is_locked and action != "generate_tasks":
            messages.error(request, "This minutes record is approved and locked.")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        # ✅ Only capture manual text for actions that actually edit it
        if action in ("save", "generate_ai", "finalize"):
            minutes_obj.discussion_points = request.POST.get("discussion_points", "")

        if action == "save":
            minutes_obj.save(update_fields=["discussion_points", "updated_at"])
            messages.success(request, "Minutes saved.")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        if action == "generate_ai":
            # Use the manual minutes if provided, otherwise fall back to the
            # uploaded meeting transcript so upload-only meetings summarize too.
            source_text = (minutes_obj.discussion_points or "").strip()
            if not source_text:
                source_text = (getattr(meeting, "transcript_text", "") or "").strip()
            result = run_ai(str(meeting.id), source_text)

            minutes_obj.ai_summary = result.get("summary", "")

            payload = _extract_decisions_payload(result)

            # store decisions as JSON string always
            if payload:
                minutes_obj.ai_decisions = json.dumps(payload, ensure_ascii=False)
            else:
                minutes_obj.ai_decisions = str(result.get("decisions", ""))

            minutes_obj.ai_generated_at = timezone.now()
            minutes_obj.summary = minutes_obj.ai_summary  # legacy sync

            minutes_obj.save(update_fields=[
                "discussion_points",
                "ai_summary",
                "summary",
                "ai_decisions",
                "ai_generated_at",
                "updated_at"
            ])

            # also store into the RAG knowledge base for the assistant
            try:
                _save_minutes_to_knowledge(meeting, minutes_obj.ai_summary, payload)
            except Exception:
                pass

            messages.success(request, "AI summary & decisions generated (saved to knowledge base).")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        if action == "finalize":
            # One-click: summarize -> save to RAG -> send for approval.
            source_text = (minutes_obj.discussion_points or "").strip()
            if not source_text:
                source_text = (getattr(meeting, "transcript_text", "") or "").strip()
            if not source_text:
                messages.error(request, "Add minutes text (or upload a transcript) before finalizing.")
                return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

            result = run_ai(str(meeting.id), source_text)
            minutes_obj.ai_summary = result.get("summary", "")
            payload = _extract_decisions_payload(result)
            if payload:
                minutes_obj.ai_decisions = json.dumps(payload, ensure_ascii=False)
            else:
                minutes_obj.ai_decisions = str(result.get("decisions", ""))
            minutes_obj.ai_generated_at = timezone.now()
            minutes_obj.summary = minutes_obj.ai_summary
            minutes_obj.save(update_fields=[
                "ai_summary", "summary", "ai_decisions", "ai_generated_at", "updated_at"
            ])

            try:
                _save_minutes_to_knowledge(meeting, minutes_obj.ai_summary, payload)
            except Exception:
                pass

            minutes_obj.send_to_review()
            messages.success(request, "Summarized, saved to knowledge base, and sent for approval.")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        if action == "send_to_review":
            # (Locked gate already blocks this)
            minutes_obj.send_to_review()
            messages.info(request, "Minutes sent to review.")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        if action == "approve":
            # (Locked gate already blocks this)
            if not _is_admin(request.user):
                messages.error(request, "You are not allowed to approve minutes.")
                return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

            minutes_obj.approve(request.user)
            messages.success(request, "Minutes approved and locked.")
            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        if action == "generate_tasks":
            # ✅ Allowed even when locked
            if not _is_admin(request.user):
                messages.error(request, "You are not allowed to generate tasks.")
                return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

            if minutes_obj.status != Minutes.STATUS_APPROVED:
                messages.error(request, "Approve the minutes first before generating tasks.")
                return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

            data = _parse_ai_decisions(minutes_obj.ai_decisions)

            action_items = data.get("action_items") or data.get("tasks") or data.get("actions") or []
            if not action_items:
                messages.warning(request, "No action items found to convert into tasks.")
                return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

            from tasks.models import Task  # local import

            created_count = 0
            for item in action_items:
                if not isinstance(item, dict):
                    continue

                title = (item.get("title") or "").strip()
                if not title:
                    continue

                assignee_name = item.get("assignee")
                assigned_user = _find_user_by_name(assignee_name)

                priority = (item.get("priority") or "medium").lower()
                if priority not in dict(Task.PRIORITY_CHOICES):
                    priority = "medium"

                _, was_created = Task.objects.get_or_create(
                    meeting=meeting,
                    minutes=minutes_obj,
                    title=title,
                    defaults={
                        "description": "",
                        "assigned_to": assigned_user,
                        "priority": priority,
                        "status": "todo",
                        "due_date": None,
                    }
                )
                if was_created:
                    created_count += 1

            if created_count:
                messages.success(request, f"Generated {created_count} tasks from minutes decisions.")
            else:
                messages.info(request, "No new tasks were created (maybe already generated).")

            return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

        # If action is missing/unknown
        messages.warning(request, "Unknown action.")
        return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

    return render(request, "minutes/meeting_minutes.html", {
        "meeting": meeting,
        "minutes": minutes_obj,
    })
