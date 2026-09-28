from django.urls import path

from . import api, exports

urlpatterns = [
    path("api/events", api.events, name="api-events"),
    path("api/events/<slug:slug>", api.event_detail, name="api-event"),
    path("api/events/<slug:slug>/projects", api.event_projects, name="api-event-projects"),
    path("api/events/<slug:slug>/export/<slug:kind>.csv", exports.export_csv, name="api-export"),
    path("api/projects/<int:pk>", api.project_detail, name="api-project"),
    path("api/judge/scores", api.judge_scores, name="api-judge-scores"),
]
