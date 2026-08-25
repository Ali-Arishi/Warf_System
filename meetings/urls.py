from django.urls import path
from . import views

app_name = "meetings"

urlpatterns = [
    path("", views.meetings_list, name="list"),
    path("new/", views.meeting_create, name="create"),

    path("<int:pk>/", views.meeting_detail, name="detail"),
    path("<int:pk>/join/", views.join_meeting, name="join"),
    path("<int:pk>/verify-face/", views.verify_face_api, name="verify_face"),

    # Upload transcript (text) for upload-only / both
    path("<int:pk>/upload/", views.upload_transcript, name="upload_transcript"),

    # Live transcription segments from the in-meeting recorder
    path("<int:pk>/transcript-segment/", views.add_transcript_segment, name="transcript_segment"),
]
