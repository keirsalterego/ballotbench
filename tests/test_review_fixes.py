"""Regression tests for findings from the security review: each one is the
attack as it was reported, now refused."""
from datetime import timedelta

import pytest
from django.utils import timezone

from portal.models import Event, JudgeAssignment, Project, Track

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db


@pytest.fixture
def published(web):
    web("organizer").post(f"/events/{EVENT}/manage/calibration")
    web("organizer").post(f"/events/{EVENT}/manage/publish")
    return Event.objects.get(slug=EVENT)


def test_explain_page_is_hidden_while_a_reopened_vote_runs(web, published):
    project = Project.objects.get(event=published, external_id="prj_01")
    url = f"/events/{EVENT}/results/{project.pk}"
    assert web("participant").get(url).status_code == 200
    now = timezone.now()
    Event.objects.filter(pk=published.pk).update(voting_mode="account", voting_open=now - timedelta(hours=1),
                                                  voting_close=now + timedelta(days=1))
    assert web("participant").get(url).status_code == 404
    assert web("organizer").get(url).status_code == 200


def test_judges_cannot_rescore_once_results_are_published(api, published):
    mine = JudgeAssignment.objects.filter(judge__email=EMAILS["judge_a"], event=published).first()
    response = api("judge_a").post(f"/api/judge/assignments/{mine.pk}/review",
                                   {"scores": {"functionality": 5, "quality": 5, "innovation": 5}, "submit": True},
                                   content_type="application/json")
    assert response.status_code == 409 and "published" in response.json()["detail"]


def test_public_results_carry_no_raw_means(api, published):
    public = api().get(f"/api/events/{EVENT}/results").json()["projects"]
    assert public and all("raw_mean" not in row for row in public)
    assert all("raw_mean" in row for row in api("organizer").get(f"/api/events/{EVENT}/results").json()["projects"])


def test_deleting_a_track_after_the_deadline_is_refused_not_a_500(web):
    track = Track.objects.filter(event__slug=EVENT, project__isnull=False).first()
    response = web("organizer").post(f"/events/{EVENT}/manage/tracks/{track.pk}/delete", follow=True)
    assert response.status_code == 200 and "be deleted now" in response.content.decode()
    assert Track.objects.filter(pk=track.pk).exists()


def test_explain_rounds_each_judges_usual_score(published):
    from portal.explain import explanation
    project = Project.objects.get(event=published, external_id="prj_01")
    for line in explanation(published.published_run, project)["lines"]:
        assert abs(line["usual"] * 20 - round(line["usual"] * 20)) < 1e-9
