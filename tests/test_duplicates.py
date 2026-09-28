"""Duplicate detection: the later submission is the copy, a submit only ever
flags the project being submitted, and an organizer's call sticks."""
from datetime import timedelta

import pytest
from django.test import Client

from portal import voting
from portal.access import db_now
from portal.duplicates import find_duplicates
from portal.models import Event, Project, Team, TeamMember, User, Voter
from portal.progress import rankable_projects

from .conftest import EMAILS

pytestmark = pytest.mark.django_db
DEMO = "demo-open"


def team_client(event, name, user):
    TeamMember.objects.create(team=Team.objects.create(event=event, name=name), event=event, user=user)
    client = Client()
    client.force_login(user)
    return client


def test_copying_a_public_project_flags_the_copy_not_the_original(api, web):
    """A participant keeps an early draft (the lowest pk), then copies another
    team's public title and repo into it and submits. The copy is the
    duplicate; the original stays in judging and on the ballot."""
    event = Event.objects.get(slug=DEMO)
    TeamMember.objects.create(team=Team.objects.create(event=event, name="Copycats"), event=event,
                              user=User.objects.get(email=EMAILS["participant"]))
    copier = api("participant")
    draft = copier.post(f"/api/events/{DEMO}/projects", {"title": "placeholder"}, content_type="application/json")
    assert draft.status_code == 201, draft.content
    copy_pk = draft.json()["id"]

    victim_client = team_client(event, "Originals", User.objects.create_user("original@example.org", "pw-for-tests"))
    victim_client.post(f"/events/{DEMO}/project/new",
                       {"title": "Real Thing", "repo_url": "https://github.com/victim/real", "submit": "1"})
    original = Project.objects.get(team__name="Originals", event=event)
    # A test is one transaction, with one now(); really the copy comes later.
    Project.objects.filter(pk=original.pk).update(submitted_at=original.submitted_at - timedelta(hours=1))
    assert copy_pk < original.pk and not find_duplicates(event), "a draft is nobody's original"

    r = copier.patch(f"/api/projects/{copy_pk}", {"title": "Real Thing", "repo_url": "https://github.com/victim/real",
                                                  "submit": True}, content_type="application/json")
    assert r.status_code == 200, r.content
    original.refresh_from_db()
    assert original.duplicate_of_id is None and original in rankable_projects(event)
    voter = Voter.objects.create(event=event, user=User.objects.create_user("v@example.org", "x"), confirmed_at=db_now())
    assert original.pk in {p.pk for p in voting.ballot_projects(event, voter)}
    assert Project.objects.get(pk=copy_pk).duplicate_of_id == original.pk


def test_a_project_cleared_as_distinct_is_never_flagged_again(web):
    event = Event.objects.get(slug=DEMO)
    first = team_client(event, "First", User.objects.create_user("first@example.org", "pw-for-tests"))
    second = team_client(event, "Second", User.objects.create_user("second@example.org", "pw-for-tests"))
    fields = {"title": "Same Name", "repo_url": "https://github.com/shared/monorepo", "submit": "1"}
    first.post(f"/events/{DEMO}/project/new", fields)
    second.post(f"/events/{DEMO}/project/new", fields)
    later = Project.objects.get(team__name="Second", event=event)
    assert later.duplicate_of_id is not None

    web("organizer").post(f"/events/{DEMO}/manage/duplicates/{later.pk}", {"decision": "distinct"})
    later.refresh_from_db()
    assert later.duplicate_of_id is None and later.duplicate_cleared
    assert later.pk not in {dup.pk for dup, _ in find_duplicates(event)}

    team_client(event, "Bystanders", User.objects.create_user("bystander@example.org", "pw-for-tests")).post(
        f"/events/{DEMO}/project/new", {"title": "Unrelated", "submit": "1"})
    later.refresh_from_db()
    assert later.duplicate_of_id is None


def test_submitted_at_is_the_servers(api):
    """The order is by submitted_at, so nobody may choose their own."""
    event = Event.objects.get(slug=DEMO)
    TeamMember.objects.create(team=Team.objects.create(event=event, name="Early birds"), event=event,
                              user=User.objects.get(email=EMAILS["participant"]))
    r = api("participant").post(f"/api/events/{DEMO}/projects",
                                {"title": "Backdated", "submitted_at": "2000-01-01T00:00:00Z", "submit": True},
                                content_type="application/json")
    assert r.status_code == 201, r.content
    assert Project.objects.get(pk=r.json()["id"]).submitted_at.year != 2000
