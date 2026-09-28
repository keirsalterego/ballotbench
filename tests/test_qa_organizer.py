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


def test_manual_assignment_respects_the_judges_tracks(web, event):
    judge = Membership.objects.get(event=event, user__email=EMAILS["judge_b"], role="judge")
    project = Project.objects.filter(event=event, status="submitted", duplicate_of__isnull=True,
                                     track__isnull=False).exclude(assignments__judge=judge.user).first()
    other = Track.objects.create(event=event, name="Elsewhere")
    judge.tracks.set([other])
    url = f"/events/{EVENT}/manage/assign/manual"
    response = web("organizer").post(url, {"judge": judge.user_id, "project": project.pk}, follow=True)
    assert "isn&#x27;t one of" in response.content.decode()
    assert not JudgeAssignment.objects.filter(judge=judge.user, project=project).exists()
    web("organizer").post(url, {"judge": judge.user_id, "project": project.pk, "outside_tracks": "1"})
    a = JudgeAssignment.objects.get(judge=judge.user, project=project)
    assert AuditLog.objects.get(action="assignment.create", object_id=str(a.pk)).after["outside_tracks"] is True


def test_the_podium_line_counts_overall_prizes_only(event):
    Prize.objects.filter(event=event).delete()
    Prize.objects.create(event=event, name="Best in track", track=event.tracks.first())
    assert podium_line(event) == 3
    Prize.objects.create(event=event, name="First")
    Prize.objects.create(event=event, name="Second")
    assert podium_line(event) == 2


def test_the_results_page_lists_the_prizes(web, event):
    Prize.objects.create(event=event, name="Grand prize")
    web("organizer").post(f"/events/{EVENT}/manage/calibration")
    assert "Grand prize" in web("organizer").get(f"/events/{EVENT}/results").content.decode()


def test_calibration_with_nothing_rankable_offers_no_publish(web):
    client = web("organizer")
    client.post(f"/events/{DEMO}/manage/calibration")
    page = client.get(f"/events/{DEMO}/manage/calibration").content.decode()
    assert "Nothing to publish" in page and "Publish results" not in page
    response = client.post(f"/events/{DEMO}/manage/publish")
    assert response.status_code == 409 and f"/events/{DEMO}/manage/calibration" in response.content.decode()
    assert Event.objects.get(slug=DEMO).published_run_id is None


def test_calibration_explains_why_publish_waits_for_the_vote(web, event):
    client = web("organizer")
    client.post(f"/events/{EVENT}/manage/calibration")
    now = timezone.now()
    Event.objects.filter(pk=event.pk).update(voting_mode="account", voting_open=now - timedelta(hours=1),
                                              voting_close=now + timedelta(days=1))
    page = client.get(f"/events/{EVENT}/manage/calibration").content.decode()
    assert "community vote is open" in page and "Publish results" not in page
    refused = client.post(f"/events/{EVENT}/manage/publish").content.decode()
    assert f"/events/{EVENT}/manage/calibration" in refused and "server&#x27;s clock" not in refused


def test_runs_are_numbered_per_event(web, api, event):
    client = web("organizer")
    client.post(f"/events/{DEMO}/manage/calibration")      # another event's run takes a pk first
    client.post(f"/events/{EVENT}/manage/calibration")
    n = CalibrationRun.objects.filter(event=event).count()
    assert "<h2>Run 1</h2>" in client.get(f"/events/{DEMO}/manage/calibration").content.decode()
    body = api("organizer").get(f"/api/events/{EVENT}/results").json()
    assert body["run_number"] == n and body["run"] == CalibrationRun.objects.filter(event=event).latest("pk").pk


def test_progress_offers_only_what_the_planner_can_fill(web, event):
    Event.objects.filter(pk=event.pk).update(reviews_per_project=10)
    event.refresh_from_db()
    fillable = len(make_plan(event, 10).new)
    page = web("organizer").get(f"/events/{EVENT}/manage/progress").content.decode()
    assert "can't be handed out" in page
    if fillable:
        assert f"Hand out {fillable} more" in page
    else:
        assert "Hand out" not in page


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


def test_api_docs_render_code_use_this_host_and_describe_every_route(web):
    page = web().get("/api/docs", SERVER_NAME="localhost", SERVER_PORT="8123").content.decode()
    assert "`" not in page and "<code>Authorization: Bearer &lt;token&gt;</code>" in page
    assert "http://localhost:8123/api/judge/scores" in page and "localhost:8080" not in page
    assert "<td></td>" not in page


def test_the_certificate_links_the_key_in_full(web):
    page = web("judge_a").get(f"/events/{EVENT}/certificate").content.decode()
    assert 'href="http://testserver/.well-known/ballotbench-signing-key"' in page


def test_calibration_heading_matches_the_nav_and_the_audit_log_names_things(web, event):
    client = web("organizer")
    assert "<h1>Calibration and results</h1>" in client.get(f"/events/{EVENT}/manage/calibration").content.decode()
    page = client.get(f"/events/{EVENT}/manage/audit").content.decode()
    assert f"event {EVENT}" in page
