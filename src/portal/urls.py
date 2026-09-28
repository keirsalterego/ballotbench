from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, exports, participant, views

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

    path("api/events", api.events, name="api-events"),
    path("api/events/<slug:slug>", api.event_detail, name="api-event"),
    path("api/events/<slug:slug>/projects", api.event_projects, name="api-event-projects"),
    path("api/events/<slug:slug>/export/<slug:kind>.csv", exports.export_csv, name="api-export"),
    path("api/projects/<int:pk>", api.project_detail, name="api-project"),
    path("api/judge/scores", api.judge_scores, name="api-judge-scores"),
]
