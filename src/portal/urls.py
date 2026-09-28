from django.contrib.auth import views as auth_views
from django.urls import path

from . import api, exports, views

urlpatterns = [
    path("", views.gallery),
    path("projects", views.gallery, name="gallery"),
    path("projects/<int:pk>", views.project_page, name="project"),
    path("login", auth_views.LoginView.as_view(), name="login"),
    path("logout", auth_views.LogoutView.as_view(), name="logout"),
    path("signup", views.signup, name="signup"),
    path("me", views.home, name="home"),

    path("api/events", api.events, name="api-events"),
    path("api/events/<slug:slug>", api.event_detail, name="api-event"),
    path("api/events/<slug:slug>/projects", api.event_projects, name="api-event-projects"),
    path("api/events/<slug:slug>/export/<slug:kind>.csv", exports.export_csv, name="api-export"),
    path("api/projects/<int:pk>", api.project_detail, name="api-project"),
    path("api/judge/scores", api.judge_scores, name="api-judge-scores"),
]
