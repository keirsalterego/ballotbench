from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, exports, judge, organizer, participant, views

urlpatterns = [
    path("", views.gallery),
    path("projects", views.gallery, name="gallery"),
    path("projects/<int:pk>", views.project_page, name="project"),
    path("login", auth_views.LoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    path("signup", views.signup, name="signup"),
    path("me", views.home, name="home"),
    path("events/<slug:slug>/join", participant.join_event, name="event-join"),
    path("events/<slug:slug>/me", participant.event_me, name="event-me"),
    path("events/<slug:slug>/team", participant.create_team, name="team-create"),
    path("events/<slug:slug>/project/new", participant.project_form, name="project-new"),
    path("events/<slug:slug>/project/<int:pk>/edit", participant.project_form, name="project-edit"),
    path("teams/<int:pk>/invites", participant.create_invite, name="invite-create"),
    path("teams/<int:pk>/invites/<int:invite>/revoke", participant.revoke_invite, name="invite-revoke"),
    path("invite/<str:token>", participant.accept_invite, name="invite"),

    path("events/new", organizer.create_event, name="event-new"),
    path("events/<slug:slug>/manage", organizer.manage, name="manage"),
    path("events/<slug:slug>/manage/tracks", organizer.add_track, name="track-add"),
    path("events/<slug:slug>/manage/tracks/<int:pk>/delete", organizer.delete_track, name="track-delete"),
    path("events/<slug:slug>/manage/prizes", organizer.add_prize, name="prize-add"),
    path("events/<slug:slug>/manage/prizes/<int:pk>/delete", organizer.delete_prize, name="prize-delete"),
    path("events/<slug:slug>/manage/rubric", organizer.save_criterion, name="criterion-add"),
    path("events/<slug:slug>/manage/rubric/<int:pk>", organizer.save_criterion, name="criterion-save"),
    path("events/<slug:slug>/manage/rubric/<int:pk>/delete", organizer.delete_criterion, name="criterion-delete"),
    path("events/<slug:slug>/manage/judges/<int:pk>/tracks", organizer.set_judge_tracks, name="judge-tracks"),
    path("events/<slug:slug>/manage/invites", organizer.create_role_invite, name="role-invite"),
    path("events/<slug:slug>/manage/invites/<int:pk>/revoke", organizer.revoke_role_invite, name="role-invite-revoke"),
    path("join/<str:token>", organizer.accept_role_invite, name="role-join"),

    path("judge", judge.queue, name="judge-queue"),
    path("judge/assignments/<int:pk>", judge.assignment_page, name="judge-assignment"),
    path("judge/assignments/<int:pk>/recuse", judge.recuse, name="judge-recuse"),

    path("api/events", api.events, name="api-events"),
    path("api/events/<slug:slug>", api.event_detail, name="api-event"),
    path("api/events/<slug:slug>/projects", api.event_projects, name="api-event-projects"),
    path("api/events/<slug:slug>/export/<slug:kind>.csv", exports.export_csv, name="api-export"),
    path("api/projects/<int:pk>", api.project_detail, name="api-project"),
    path("api/judge/scores", api.judge_scores, name="api-judge-scores"),
    path("api/judge/assignments", judge.api_assignments, name="api-judge-assignments"),
    path("api/judge/assignments/<int:pk>/review", judge.api_review, name="api-judge-review"),
]
