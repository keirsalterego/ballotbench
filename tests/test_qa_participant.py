"""Regression tests for what a stranger's walkthrough found on the
participant side: each one is the step as it was reported."""
import io
import json
from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.core.management import call_command

from portal.access import db_now
from portal.models import AuditLog, Comment, Event, Membership, Project, Team, TeamInvite, TeamMember, User
from portal.participant import _hash

from .conftest import EMAILS, EVENT
from .test_voting import DEMO, ballot  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db


def flashed(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


@pytest.fixture
def me(web):
    """A participant of an event that takes submissions now, on a team."""
    now = db_now()
    event = Event.objects.create(slug="qa-open", name="QA open", submissions_open=now - timedelta(hours=1),
                                 submissions_close=now + timedelta(days=1))
    user = User.objects.create_user("qa-person@example.org", "pw-for-tests-only", name="QA person")
    Membership.objects.create(user=user, event=event, role=Membership.Role.PARTICIPANT)
    client = web()
    client.force_login(user)
    return client, event, user


def on_team(event, user, name="QA team"):
    team = Team.objects.create(event=event, name=name)
    TeamMember.objects.create(team=team, event=event, user=user)
    return team


def test_an_over_long_team_name_says_so(me):
    client, event, _ = me
    response = client.post(f"/events/{event.slug}/team", {"name": "x" * 300})
    assert flashed(response) == ["A team name can be at most 200 characters (this one has 300)."]
    assert not event.teams.exists()


def test_a_team_name_already_taken_in_the_event_is_refused(me):
    client, event, _ = me
    Team.objects.create(event=event, name="Night Owls")
    response = client.post(f"/events/{event.slug}/team", {"name": "night owls"})
    assert "already a team called night owls" in flashed(response)[0]
    assert event.teams.count() == 1


def test_a_submitted_project_saves_changes_not_a_draft(me):
    client, event, user = me
    team = on_team(event, user)
    project = Project.objects.create(event=event, team=team, title="Lamp", summary="s", status="submitted",
                                     submitted_at=db_now())
    url = f"/events/{event.slug}/project/{project.pk}/edit"
    page = client.get(url).content.decode()
    assert "Save changes" in page and "Save draft" not in page
    response = client.post(url, {"title": "Lamp 2", "summary": "s", "save": ""})
    assert flashed(response) == ["Saved. It stays submitted and public."]
    assert Project.objects.get(pk=project.pk).status == "submitted"


def test_a_team_deletes_its_own_draft_but_not_a_submitted_project(me):
    client, event, user = me
    team = on_team(event, user)
    draft = Project.objects.create(event=event, team=team, title="Draft", summary="s")
    kept = Project.objects.create(event=event, team=team, title="Kept", summary="s", status="submitted",
                                  submitted_at=db_now())
    assert "Delete this draft" in client.get(f"/events/{event.slug}/project/{draft.pk}/edit").content.decode()
    assert client.post(f"/events/{event.slug}/project/{draft.pk}/delete").status_code == 302
    assert not Project.objects.filter(pk=draft.pk).exists()
    assert AuditLog.objects.filter(action="project.delete", object_id=str(draft.pk)).exists()
    client.post(f"/events/{event.slug}/project/{kept.pk}/delete")
    assert Project.objects.filter(pk=kept.pk).exists()


def test_deleting_a_draft_after_the_deadline_is_409(me):
    client, event, user = me
    draft = Project.objects.create(event=event, team=on_team(event, user), title="Draft", summary="s")
    now = db_now()
    Event.objects.filter(pk=event.pk).update(submissions_open=now - timedelta(days=2),
                                             submissions_close=now - timedelta(minutes=1))
    assert client.post(f"/events/{event.slug}/project/{draft.pk}/delete").status_code == 409
    assert Project.objects.filter(pk=draft.pk).exists()


def test_someone_elses_draft_cant_be_deleted(me):
    client, event, user = me
    other = Team.objects.create(event=event, name="Other")
    on_team(event, user)
    draft = Project.objects.create(event=event, team=other, title="Theirs", summary="s")
    assert client.post(f"/events/{event.slug}/project/{draft.pk}/delete").status_code == 404
    assert Project.objects.filter(pk=draft.pk).exists()


def test_a_double_posted_new_project_makes_one(me):
    client, event, user = me
    team = on_team(event, user)
    form = {"title": "Twice", "summary": "s", "save": ""}
    client.post(f"/events/{event.slug}/project/new", form)
    response = client.post(f"/events/{event.slug}/project/new", form)
    only = team.projects.get()
    assert response.url == f"/events/{event.slug}/project/{only.pk}/edit"
    assert any("already has a project called Twice" in m for m in flashed(response))


def test_the_page_disables_buttons_once_a_form_is_sent(web):
    page = web().get("/projects").content.decode()
    assert "e.submitter" in page and "b.disabled = true" in page


def test_after_the_deadline_the_project_page_is_read_only(web):
    own = Project.objects.filter(event__slug=EVENT, team__members__user__email=EMAILS["participant"]).first()
    response = web("participant").get(f"/events/{EVENT}/project/{own.pk}/edit")
    page = response.content.decode()
    assert response.status_code == 200 and "closed" in page and own.title in page
    assert "<button" not in page.split("<main>")[1].split("</main>")[0]


def test_a_full_team_offers_no_invite_and_its_links_no_join_button(me, web):
    client, event, user = me
    Event.objects.filter(pk=event.pk).update(max_team_size=1)
    team = on_team(event, user)
    page = client.get(f"/events/{event.slug}/me").content.decode()
    assert "Your team is full" in page and "Make an invite link" not in page
    TeamInvite.objects.create(team=team, token_hash=_hash("qa-token"), created_by=user,
                              expires_at=db_now() + timedelta(hours=1))
    stranger = web()
    stranger.force_login(User.objects.create_user("qa-stranger@example.org", "pw-for-tests-only"))
    page = stranger.get("/invite/qa-token").content.decode()
    assert "This team is full." in page and "Join the team" not in page


def test_project_form_names_its_urls_plainly(me):
    client, event, user = me
    on_team(event, user)
    page = client.get(f"/events/{event.slug}/project/new").content.decode()
    assert "Code repository URL" in page and "Demo URL" in page and "Repo url" not in page
