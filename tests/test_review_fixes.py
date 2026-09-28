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


def test_a_token_reads_pages_but_cannot_write_through_them(api):
    client = api("judge_a")
    assert client.get("/judge").status_code == 200
    assert client.post("/me/tokens", {"label": "minted"}).status_code == 403
    assert client.get("/me/tokens").status_code == 403
    assert client.post(f"/events/{EVENT}/manage/tracks", {"name": "x"}).status_code == 403
    from portal.models import ApiToken
    assert not ApiToken.objects.filter(label="minted").exists()


def test_a_staff_token_does_not_open_the_admin(db):
    from django.test import Client
    from portal.auth import issue_token
    from portal.models import User
    token = issue_token(User.objects.get(email=EMAILS["admin"]), "admin script")
    assert Client(HTTP_AUTHORIZATION=f"Bearer {token}").get("/admin/").status_code == 403


def test_signup_applies_the_password_validators(web):
    response = web().post("/signup", {"name": "N", "email": "weak@example.org", "password": "1234567890"})
    assert response.status_code == 200 and "too common" in response.content.decode()


def test_admin_inline_rows_are_audited(web):
    from portal.models import AuditLog
    event = Event.objects.get(slug="demo-open")
    tracks = list(event.tracks.all())
    data = {"slug": event.slug, "name": event.name, "description": event.description,
            "submissions_open_0": event.submissions_open.strftime("%Y-%m-%d"),
            "submissions_open_1": event.submissions_open.strftime("%H:%M:%S"),
            "submissions_close_0": event.submissions_close.strftime("%Y-%m-%d"),
            "submissions_close_1": event.submissions_close.strftime("%H:%M:%S"),
            "voting_mode": event.voting_mode, "vote_credits": event.vote_credits,
            "reviews_per_project": 3, "max_team_size": 4,
            "tracks-TOTAL_FORMS": len(tracks) + 1, "tracks-INITIAL_FORMS": len(tracks),
            "prizes-TOTAL_FORMS": 0, "prizes-INITIAL_FORMS": 0}
    for i, t in enumerate(tracks):
        data.update({f"tracks-{i}-id": t.pk, f"tracks-{i}-event": event.pk, f"tracks-{i}-name": t.name})
    data[f"tracks-0-DELETE"] = "on"
    data.update({f"tracks-{len(tracks)}-name": "Sneaky track", f"tracks-{len(tracks)}-event": event.pk})
    web("admin").post(f"/admin/portal/event/{event.pk}/change/", data)
    added = AuditLog.objects.filter(action="admin.add", object_type="track").exists()
    deleted = AuditLog.objects.filter(action="admin.delete", object_type="track").exists()
    assert added and deleted


def test_verify_refuses_a_record_with_a_repeated_field():
    import json
    from portal import signing
    signed = signing.sign({"kind": "test", "team": "real"})
    text = json.dumps(signed).replace('"team": "real"', '"team": "First Place", "team": "real"')
    valid, reason, _ = signing.verify(text)
    assert not valid and "repeats a field" in reason
    assert signing.verify(json.dumps(signed))[0]
