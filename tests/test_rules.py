"""Invariants the database holds on its own, whatever code path writes."""
import pytest
from django.db import DatabaseError, IntegrityError, transaction

from portal.models import (Event, JudgeAssignment, Membership, Project, Review, RubricCriterion, Score, Team,
                           TeamMember, User)

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    return Event.objects.get(slug="demo-open")


def person(email):
    return User.objects.create_user(email, "pw-for-tests-only")


def refused(match=None):
    return pytest.raises((DatabaseError, IntegrityError), match=match)


def test_one_team_per_person_per_event(demo):
    u = person("one@x.org")
    a, b = Team.objects.create(event=demo, name="A"), Team.objects.create(event=demo, name="B")
    TeamMember.objects.create(team=a, event=demo, user=u)
    with refused(), transaction.atomic():
        TeamMember.objects.create(team=b, event=demo, user=u)


def test_team_member_event_follows_the_team(demo):
    fixture = Event.objects.get(slug="sample-hack-2026")
    team = Team.objects.create(event=demo, name="C")
    m = TeamMember.objects.create(team=team, event=fixture, user=person("follow@x.org"))
    m.refresh_from_db()
    assert m.event_id == demo.pk


def test_team_size_cap(demo):
    Event.objects.filter(pk=demo.pk).update(max_team_size=2)
    team = Team.objects.create(event=demo, name="Small")
    TeamMember.objects.create(team=team, event=demo, user=person("s1@x.org"))
    TeamMember.objects.create(team=team, event=demo, user=person("s2@x.org"))
    with refused("team is full"), transaction.atomic():
        TeamMember.objects.create(team=team, event=demo, user=person("s3@x.org"))


def test_judge_cannot_join_a_team_and_member_cannot_judge(demo):
    judge = User.objects.get(email="diego.herrera@example.org")     # judges demo-open in the seed
    team = Team.objects.create(event=demo, name="D")
    with refused("judges this event"), transaction.atomic():
        TeamMember.objects.create(team=team, event=demo, user=judge)
    member = person("member@x.org")
    TeamMember.objects.create(team=team, event=demo, user=member)
    with refused("cannot judge"), transaction.atomic():
        Membership.objects.create(user=member, event=demo, role="judge")


def test_assignment_needs_a_judge_membership_and_same_event(demo):
    fixture_project = Project.objects.filter(event__slug="sample-hack-2026").first()
    with refused("does not judge"), transaction.atomic():
        JudgeAssignment.objects.create(event=fixture_project.event, judge=person("nobody@x.org"), project=fixture_project)
    judge = User.objects.get(email="diego.herrera@example.org")
    with refused("not in event"), transaction.atomic():
        JudgeAssignment.objects.create(event=demo, judge=judge, project=fixture_project)


def test_score_must_be_in_range_and_in_the_event():
    review = Review.objects.filter(assignment__event__slug="sample-hack-2026").first()
    criterion = RubricCriterion.objects.get(event__slug="sample-hack-2026", key="quality")
    with refused("between"), transaction.atomic():
        Score.objects.filter(review=review, criterion=criterion).update(value=6)
    other = RubricCriterion.objects.get(event__slug="demo-open", key="quality")
    with refused("not in the review"), transaction.atomic():
        Score.objects.create(review=review, criterion=other, value=3)


def test_rubric_freezes_once_scored():
    criterion = RubricCriterion.objects.get(event__slug="sample-hack-2026", key="quality")
    with refused("frozen"), transaction.atomic():
        RubricCriterion.objects.filter(pk=criterion.pk).update(weight=5)
    with refused("frozen"), transaction.atomic():
        RubricCriterion.objects.create(event=criterion.event, key="extra", name="Extra")
    RubricCriterion.objects.filter(pk=criterion.pk).update(name="Quality of build")   # a rename is harmless


def test_rubric_is_editable_before_scoring(demo):
    RubricCriterion.objects.filter(event=demo, key="quality").update(weight=3)
    assert RubricCriterion.objects.get(event=demo, key="quality").weight == 3


def test_event_windows_must_be_in_order(demo):
    with refused(), transaction.atomic():
        Event.objects.filter(pk=demo.pk).update(submissions_open=demo.submissions_close)


def test_a_closed_event_is_deleted_only_on_purpose():
    event = Event.objects.get(slug="sample-hack-2026")
    with refused("submissions closed"), transaction.atomic():
        event.delete()
    from django.core.management import call_command
    call_command("delete_event", "sample-hack-2026", "--yes", stdout=open("/dev/null", "w"))
    assert not Event.objects.filter(slug="sample-hack-2026").exists()
    assert not Score.objects.filter(review__assignment__event_id=event.pk).exists()
