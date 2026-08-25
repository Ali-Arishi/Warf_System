import os, uuid, base64
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Q
from .forms import MeetingCreateForm
from accounts.services.faceREC.face import verify_face, FACE_DB, face_recognition_available
from .models import Meeting, Attendee
from django.contrib import messages


# -------------------------
# Helpers
# -------------------------
def _is_allowed_user(meeting: Meeting, user) -> bool:
    if user.is_superuser:
        return True
    if meeting.organizer_id == user.id:
        return True
    return Attendee.objects.filter(meeting=meeting, user=user).exists()


def _has_face_session(meeting_id: int, request) -> bool:
    return bool(request.session.get(f"face_verified_meeting_{meeting_id}", False))


# -------------------------
# Admin: Create Meeting (UI)
# -------------------------
@login_required
def meeting_create(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Admin only")

    if request.method == "POST":
        form = MeetingCreateForm(request.POST)
        if form.is_valid():
            meeting = form.save(commit=False)
            meeting.organizer = request.user

            # legacy consistency
            meeting.scheduled_at = meeting.starts_at

            meeting.save()

            # Add organizer as Host
            Attendee.objects.get_or_create(
                meeting=meeting,
                user=request.user,
                defaults={"role": "host"},
            )

            # Add selected attendees
            users = form.cleaned_data.get("attendees")
            if users:
                for u in users:
                    if u == request.user:
                        continue
                    Attendee.objects.get_or_create(
                        meeting=meeting,
                        user=u,
                        defaults={"role": "member"},
                    )

            return redirect("meetings:list")
    else:
        form = MeetingCreateForm()

    return render(request, "meetings/meeting_create.html", {"form": form})


# -------------------------
# Meetings List
# - Admin: sees all meetings
# - Employee: sees only invited/organized meetings
# -------------------------
@login_required
def meetings_list(request):
    user = request.user

    if user.is_superuser:
        meetings = Meeting.objects.all()
    else:
        meetings = (
            Meeting.objects
            .filter(
                Q(attendees__user=user) | Q(organizer=user)
            )
            .distinct()
        )

    meetings = meetings.order_by("-starts_at", "-scheduled_at")
    return render(request, "meetings/meetings_list.html", {"meetings": meetings})

# -------------------------
# Meeting Detail
# (Will become: Online vs Upload sections)
# -------------------------
@login_required
def meeting_detail(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)

    # mode permissions for UI
    can_join_online = meeting.mode in ("online", "both")
    can_upload = meeting.mode in ("upload", "both")

    is_open_now = meeting.is_open_now()
    open_message = meeting.open_status_message()

    # attendee state (for face badge in UI)
    face_verified = False
    attendee = Attendee.objects.filter(meeting=meeting, user=request.user).first()
    if attendee:
        face_verified = attendee.face_verified

    is_host = request.user.is_superuser or meeting.organizer_id == request.user.id

    return render(request, "meetings/meeting_detail.html", {
        "meeting": meeting,
        "can_join_online": can_join_online,
        "can_upload": can_upload,
        "is_open_now": is_open_now,
        "open_message": open_message,
        "require_face": meeting.require_face_verification,
        "face_verified": face_verified,
        "is_host": is_host,
    })



# -------------------------
# Join Meeting
# - Invitation gate
# - Time gate
# - Face gate (if required)
# -------------------------
@login_required
def join_meeting(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)

    # 1) Invitation gate
    if not _is_allowed_user(meeting, request.user):
        return render(request, "meetings/not_invited.html", {"meeting": meeting}, status=403)

    # 2) Mode gate (only if online is allowed)
    if meeting.mode not in ["online", "both"]:
        return render(request, "meetings/join_blocked.html", {
            "meeting": meeting,
            "reason": "This meeting is not configured as an online meeting."
        }, status=403)

    # 3) Time gate (host / admin can open the meeting anytime)
    is_host = request.user.is_superuser or meeting.organizer_id == request.user.id
    if not meeting.is_open_now() and not is_host:
        return render(request, "meetings/meeting_closed.html", {
            "meeting": meeting,
            "message": meeting.open_status_message(),
        }, status=403)

    # 4) Face gate (session-based). If the face service is unavailable
    # (deepface not installed on this host) skip it so users can still join.
    effective_require_face = meeting.require_face_verification and face_recognition_available()
    face_verified = _has_face_session(meeting.id, request) or not effective_require_face

    jitsi_domain = (meeting.jitsi_domain or "").strip() or "meet.jit.si"

    return render(request, "meetings/join_meeting.html", {
        "meeting": meeting,
        "jitsi_domain": jitsi_domain,
        "room_name": meeting.jitsi_room,
        "require_face": effective_require_face,
        "face_verified": face_verified,
    })



# -------------------------
# Face Verification API (Camera base64)
# -------------------------
@require_POST
@login_required
def verify_face_api(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)

    if not _is_allowed_user(meeting, request.user):
        return JsonResponse({"approved": False, "message": "Forbidden"}, status=403)

    if not meeting.require_face_verification:
        return JsonResponse({"approved": True, "message": "Face verification not required."})

    if not face_recognition_available():
        request.session[f"face_verified_meeting_{meeting.id}"] = True
        return JsonResponse({"approved": True, "message": "Face verification skipped (service unavailable)."})

    # Enforce time gate here too (extra security)
    if not meeting.is_open_now():
        return JsonResponse({"approved": False, "message": meeting.open_status_message()}, status=403)

    image_data = request.POST.get("image_data", "")
    if not image_data.startswith("data:image"):
        return JsonResponse({"approved": False, "message": "No image received."}, status=400)

    # decode base64
    try:
        _, encoded = image_data.split(",", 1)
        img_bytes = base64.b64decode(encoded)
    except Exception:
        return JsonResponse({"approved": False, "message": "Invalid image format."}, status=400)

    # save temp file
    temp_dir = os.path.join(settings.MEDIA_ROOT, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(temp_dir, filename)

    with open(temp_path, "wb") as f:
        f.write(img_bytes)

    authorized = list(FACE_DB.keys())
    result = verify_face(temp_path, authorized) or {}

    try:
        os.remove(temp_path)
    except Exception:
        pass

    approved = bool(result.get("approved"))

    if approved:
        attendee, _ = Attendee.objects.get_or_create(
            meeting=meeting,
            user=request.user,
            defaults={"role": "member"}
        )
        attendee.face_verified = True
        attendee.face_verified_at = timezone.now()

        conf = result.get("confidence")
        if conf is not None:
            try:
                attendee.confidence = float(conf)
            except Exception:
                pass

        attendee.save(update_fields=["face_verified", "face_verified_at", "confidence"])

        request.session[f"face_verified_meeting_{meeting.id}"] = True

    return JsonResponse({
        "approved": approved,
        "message": result.get("message", "Verified" if approved else "Verification failed"),
        "details": result
    })
@login_required
def upload_transcript(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)

    # Permission gate
    if not _is_allowed_user(meeting, request.user):
        return HttpResponseForbidden("Not allowed")

    # Mode gate
    if meeting.mode not in ("upload", "both"):
        return HttpResponseForbidden("This meeting is not configured for uploads.")

    if request.method == "POST":
        text = (request.POST.get("transcript_text") or "").strip()
        if not text:
            messages.error(request, "Please paste the transcript text.")
            return redirect("meetings:upload_transcript", pk=meeting.id)

        meeting.transcript_text = text
        meeting.transcript_uploaded_at = timezone.now()
        meeting.save(update_fields=["transcript_text", "transcript_uploaded_at"])

        messages.success(request, "Transcript uploaded successfully.")
        return redirect("minutes:meeting_minutes", meeting_id=meeting.id)

    return render(request, "meetings/upload_transcript.html", {"meeting": meeting})

# -------------------------
# Live transcript segment (speech-to-text from the meeting page)
# Each participant's browser transcribes their own mic and posts segments
# here; appended in arrival order => "who said what, in order".
# -------------------------
@require_POST
@login_required
def add_transcript_segment(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)

    if not _is_allowed_user(meeting, request.user):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    text = (request.POST.get("text") or "").strip()
    if not text or len(text) > 2000:
        return JsonResponse({"ok": False, "error": "Empty or too long"}, status=400)

    speaker = request.user.get_full_name() or request.user.username
    stamp = timezone.localtime().strftime("%H:%M:%S")
    line = f"[{stamp}] {speaker}: {text}"

    # append in order
    meeting.transcript_text = (meeting.transcript_text or "")
    if meeting.transcript_text and not meeting.transcript_text.endswith("\n"):
        meeting.transcript_text += "\n"
    meeting.transcript_text += line + "\n"
    meeting.transcript_uploaded_at = timezone.now()
    meeting.save(update_fields=["transcript_text", "transcript_uploaded_at"])

    return JsonResponse({"ok": True, "line": line})
