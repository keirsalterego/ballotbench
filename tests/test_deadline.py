"""The deadline holds on every path: the API, the pages, the admin, and the
database itself when something slips past all three."""
import pytest
from django.db import DatabaseError, connection, transaction

from portal.models import AuditLog, Event, Project, Team, TeamMember, User

from .conftest import EVENT

pytestmark = pytest.mark.django_db
PROBE = {"title": "dogfood-late-submission-probe", "summary": "probe"}


def test_api_refuses_late_submission_with_409_and_says_why(api):
    response = api("participant").post(f"/api/events/{EVENT}/projects", PROBE, content_type="application/json")
    assert response.status_code == 409
    assert "closed" in response.json()["detail"]
    assert not Project.objects.filter(title=PROBE["title"]).exists()


def test_api_refuses_late_edit(api):
    own = Project.objects.filter(event__slug=EVENT, team__members__user__email="priya1@example.org").first()
    response = api("participant").patch(f"/api/projects/{own.pk}", {"title": "late"}, content_type="application/json")
    assert response.status_code == 409


def test_pages_refuse_late_writes(web):
    client = web("participant")
    own = Project.objects.filter(event__slug=EVENT, team__members__user__email="priya1@example.org").first()
    assert client.post(f"/events/{EVENT}/project/{own.pk}/edit", {"title": "late", "summary": "x"}).status_code == 409
    assert client.post(f"/events/{EVENT}/project/new", {"title": "late", "summary": "x"}).status_code == 409


def test_admin_refuses_late_edit(web):
    project = Project.objects.filter(event__slug=EVENT).order_by("pk").first()
    data = {"event": project.event_id, "team": project.team_id, "track": project.track_id or "", "title": "late",
            "tagline": "", "summary": project.summary, "description": "", "repo_url": project.repo_url,
            "demo_url": "", "tags": "", "status": project.status, "submitted_at_0": "2026-02-27",
            "submitted_at_1": "04:08:00", "external_id": project.external_id, "duplicate_of": ""}
    response = web("admin").post(f"/admin/portal/project/{project.pk}/change/", data)
    assert response.status_code == 200 and "closed" in response.content.decode()
    assert Project.objects.get(pk=project.pk).title != "late"


def test_trigger_refuses_what_slips_past_the_app():
    project = Project.objects.filter(event__slug=EVENT).first()
    with pytest.raises(DatabaseError, match="submissions closed"), transaction.atomic():
        Project.objects.filter(pk=project.pk).update(title="sneaky")
    with pytest.raises(DatabaseError, match="submissions closed"), transaction.atomic():
        Project.objects.create(event=project.event, team=project.team, title="sneaky new")
    with pytest.raises(DatabaseError, match="submissions closed"), transaction.atomic():
        project.delete()


def test_trigger_allows_organizer_bookkeeping_after_close():
    project = Project.objects.filter(event__slug=EVENT, duplicate_of__isnull=True).first()
    other = Project.objects.filter(event__slug=EVENT).exclude(pk=project.pk).first()
    Project.objects.filter(pk=project.pk).update(duplicate_of=other)
    assert Project.objects.get(pk=project.pk).duplicate_of == other


def test_import_flag_is_the_only_bypass():
    project = Project.objects.filter(event__slug=EVENT).first()
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    Project.objects.filter(pk=project.pk).update(title="imported")
    assert Project.objects.get(pk=project.pk).title == "imported"


def test_open_event_accepts_and_reopening_is_immediate(api):
    response = api("participant").post(f"/api/events/{EVENT}/projects", PROBE, content_type="application/json")
    assert response.status_code == 409
    Event.objects.filter(slug=EVENT).update(submissions_close="2099-01-01T00:00:00Z")
    response = api("participant").patch(
        f"/api/projects/{Project.objects.filter(event__slug=EVENT, team__members__user__email='priya1@example.org').first().pk}",
        {"tagline": "extended"}, content_type="application/json")
    assert response.status_code == 200


def test_not_yet_open_is_409_too(api):
    Event.objects.filter(slug=EVENT).update(submissions_open="2098-01-01T00:00:00Z", submissions_close="2099-01-01T00:00:00Z")
    response = api("participant").post(f"/api/events/{EVENT}/projects", PROBE, content_type="application/json")
    assert response.status_code == 409 and "open at" in response.json()["detail"]


def test_submit_in_open_event_is_audited(api):
    demo = Event.objects.get(slug="demo-open")
    team = Team.objects.create(event=demo, name="Probe Team")
    TeamMember.objects.create(team=team, event=demo, user=User.objects.get(email="priya1@example.org"))
    response = api("participant").post("/api/events/demo-open/projects", {"title": "On time", "summary": "s",
                                                                          "submit": True},
                                       content_type="application/json")
    assert response.status_code == 201 and response.json()["status"] == "submitted"
    assert AuditLog.objects.filter(action="project.submit", object_id=str(response.json()["id"])).exists()


def test_api_caps_and_cleans_tags(api):
    demo = Event.objects.get(slug="demo-open")
    team = Team.objects.create(event=demo, name="Tag Team")
    TeamMember.objects.create(team=team, event=demo, user=User.objects.get(email="priya1@example.org"))
    url = "/api/events/demo-open/projects"
    too_many = api("participant").post(url, {"title": "t", "tags": [f"t{i}" for i in range(11)]},
                                       content_type="application/json")
    assert too_many.status_code == 400
    ok = api("participant").post(url, {"title": "t", "tags": ["AI", "ai ", "web"]}, content_type="application/json")
    assert ok.json()["tags"] == ["ai", "web"]
