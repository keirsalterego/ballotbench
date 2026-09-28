"""Regression tests for the QA walkthrough of the organizer and judge pages:
each is the step as it was reported, now doing the right thing."""
from datetime import timedelta

import pytest
from django.utils import timezone

from portal.models import (AuditLog, CalibrationRun, Event, JudgeAssignment, Membership, Prize, Project, Review,
                           RubricCriterion, Track, User)
from portal.organizer import EventForm
from portal.progress import make_plan, podium_line

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db
DEMO = "demo-open"


@pytest.fixture
def event():
    return Event.objects.get(slug=EVENT)


def open_judging(event):
    Event.objects.filter(pk=event.pk).update(judging_close=None)


def submitted(event):
    """judge_a's first submitted review, with judging open again."""
    open_judging(event)
    mine = JudgeAssignment.objects.filter(event=event, judge__email=EMAILS["judge_a"])
    return mine.filter(review__submitted_at__isnull=False).select_related("review").order_by("pk").first()


def scores_for(event, value=3):
    return {c.key: value for c in event.criteria.all()}



def test_deleting_a_scored_criterion_is_refused_not_a_500(web, event):
    criterion = RubricCriterion.objects.filter(event=event, score__isnull=False).first()
    response = web("organizer").post(f"/events/{EVENT}/manage/rubric/{criterion.pk}/delete", follow=True)
    assert response.status_code == 200 and "the rubric is frozen" in response.content.decode()
    assert RubricCriterion.objects.filter(pk=criterion.pk).exists()


def test_a_draft_save_cannot_replace_a_submitted_review(api, web, event):
    a = submitted(event)
    before = {s.criterion_id: s.value for s in a.review.scores.all()}
    response = api("judge_a").post(f"/api/judge/assignments/{a.pk}/review",
                                   {"scores": scores_for(event, 1), "submit": False}, content_type="application/json")
    assert response.status_code == 409 and "resubmit" in response.json()["detail"]
    form = {f"c_{k}": v for k, v in scores_for(event, 1).items()} | {"save": "1"}
    assert web("judge_a").post(f"/judge/assignments/{a.pk}", form).status_code == 409
    assert {s.criterion_id: s.value for s in a.review.scores.all()} == before
    page = web("judge_a").get(f"/judge/assignments/{a.pk}").content.decode()
    assert "Resubmit review" in page and "Save draft" not in page


def test_an_api_review_without_a_comment_keeps_the_comment(api, event):
    a = submitted(event)
    Review.objects.filter(pk=a.review.pk).update(comment="keep me")
    response = api("judge_a").post(f"/api/judge/assignments/{a.pk}/review",
                                   {"scores": scores_for(event), "submit": True}, content_type="application/json")
    assert response.status_code == 200
    assert Review.objects.get(pk=a.review.pk).comment == "keep me"


def test_the_scoresheet_shows_the_whole_project(web, event):
    a = JudgeAssignment.objects.filter(event=event, judge__email=EMAILS["judge_a"]).order_by("pk").first()
    # The fixture has no taglines, and projects are frozen after the deadline.
    later = timezone.now() + timedelta(days=1)
    Event.objects.filter(pk=event.pk).update(submissions_close=later, judging_open=later, judging_close=None)
    Project.objects.filter(pk=a.project_id).update(tagline="The tagline", summary="The summary", tags=["rust", "ml"])
    project = Project.objects.select_related("team", "track").get(pk=a.project_id)
    page = web("judge_a").get(f"/judge/assignments/{a.pk}").content.decode()
    for text in ("The tagline", "The summary", project.team.name, "rust, ml"):
        assert text in page
    if project.track:
        assert project.track.name in page


def test_deleting_a_judges_only_track_is_refused(web, event):
    track = Track.objects.create(event=event, name="Lonely track")
    judge = Membership.objects.get(event=event, user__email=EMAILS["judge_a"], role="judge")
    judge.tracks.set([track])
    response = web("organizer").post(f"/events/{EVENT}/manage/tracks/{track.pk}/delete", follow=True)
    assert "only track of 1 judge" in response.content.decode()
    assert Track.objects.filter(pk=track.pk).exists()


def test_judging_and_voting_cant_open_before_submissions_close():
    form = EventForm({"name": "x", "slug": "x-order", "reviews_per_project": 3, "max_team_size": 4,
                      "submissions_open": "2026-10-01T00:00", "submissions_close": "2026-10-05T00:00",
                      "judging_open": "2026-10-04T00:00", "judging_close": "2026-10-09T00:00",
                      "voting_open": "2026-10-04T00:00", "voting_close": "2026-10-09T00:00",
                      "voting_mode": "account", "vote_credits": 25})
    assert not form.is_valid()
    assert "judging_open" in form.errors and "voting_open" in form.errors


def test_recusal_needs_a_reason(web, event):
    open_judging(event)
    a = JudgeAssignment.objects.filter(event=event, judge__email=EMAILS["judge_a"], status="pending").first()
    if a is None:
        a = JudgeAssignment.objects.create(
            event=event, judge=User.objects.get(email=EMAILS["judge_a"]),
            project=Project.objects.filter(event=event, status="submitted").exclude(
                assignments__judge__email=EMAILS["judge_a"]).first())
    web("judge_a").post(f"/judge/assignments/{a.pk}/recuse", {"reason": "  "})
    assert JudgeAssignment.objects.get(pk=a.pk).status == "pending"
    web("judge_a").post(f"/judge/assignments/{a.pk}/recuse", {"reason": "my cousin's team"})
    assert JudgeAssignment.objects.get(pk=a.pk).status == "recused"


def test_saving_a_track_says_so(web):
    response = web("organizer").post(f"/events/{DEMO}/manage/tracks", {"name": "Saved track"}, follow=True)
    assert "Saved." in response.content.decode()
