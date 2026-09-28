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


