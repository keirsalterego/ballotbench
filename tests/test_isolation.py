"""Who can see what: the checks run.py makes, and the many it doesn't."""
import pytest

from portal.models import JudgeAssignment, Project, User

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db


def assignment_of(role):
    return JudgeAssignment.objects.filter(judge__email=EMAILS[role], event__slug=EVENT).order_by("pk").first()


def test_judge_sees_only_their_own_scores(api):
    rows = api("judge_a").get("/api/judge/scores").json()
    assert rows and {r["judge"] for r in rows} == {EMAILS["judge_a"]}


@pytest.mark.parametrize("named", ["jdg_24", EMAILS["judge_a"], "jdg_07", "999999"])
def test_judge_naming_another_judge_is_refused(api, named):
    assert api("judge_b").get(f"/api/judge/scores?judge={named}").status_code == 403


def test_judge_may_name_themselves(api):
    assert api("judge_a").get("/api/judge/scores?judge=jdg_24").status_code == 200


def test_judge_user_id_of_peer_is_refused(api):
    peer = User.objects.get(email=EMAILS["judge_a"]).pk
    assert api("judge_b").get(f"/api/judge/scores?judge={peer}").status_code == 403


def test_participant_and_anonymous_are_refused(api):
    assert api("participant").get("/api/judge/scores").status_code == 403
    assert api().get("/api/judge/scores").status_code == 401
    assert api("participant").get("/api/judge/assignments").status_code == 403


def test_organizer_may_read_a_judges_scores(api):
    rows = api("organizer").get("/api/judge/scores?judge=jdg_24").json()
    assert rows and {r["judge"] for r in rows} == {EMAILS["judge_a"]}


def test_bad_token_is_401(api):
    from django.test import Client
    assert Client(HTTP_AUTHORIZATION="Bearer nope").get("/api/judge/scores").status_code == 401


def test_peer_assignment_is_invisible_on_pages_and_api(api, web):
    theirs = assignment_of("judge_a")
    assert web("judge_b").get(f"/judge/assignments/{theirs.pk}").status_code == 404
    response = api("judge_b").post(f"/api/judge/assignments/{theirs.pk}/review", {"scores": {"quality": 5}},
                                   content_type="application/json")
    assert response.status_code == 404
    assert web("participant").get(f"/judge/assignments/{theirs.pk}").status_code == 404


def test_recuse_someone_elses_assignment_is_404(web):
    theirs = assignment_of("judge_a")
    assert web("judge_b").post(f"/judge/assignments/{theirs.pk}/recuse").status_code == 404


@pytest.mark.parametrize("kind", ["scores", "results", "judges", "audit", "registrations", "teams", "projects",
                                  "assignments"])
def test_exports_are_organizer_only(api, kind):
    url = f"/api/events/{EVENT}/export/{kind}.csv"
    assert api().get(url).status_code == 401
    for role in ("participant", "judge_a", "judge_b"):
        assert api(role).get(url).status_code == 403
    response = api("organizer").get(url)
    assert response.status_code == 200 and "," in response.content.decode().splitlines()[0]


def test_unknown_export_is_404(api):
    assert api("organizer").get(f"/api/events/{EVENT}/export/secrets.csv").status_code == 404


@pytest.mark.parametrize("page", ["manage", "manage/assign", "manage/progress", "manage/calibration", "manage/audit",
                                  "manage/duplicates", "manage/exports"])
def test_organizer_pages_refuse_other_roles(web, page):
    url = f"/events/{EVENT}/{page}"
    assert web().get(url).status_code == 302          # to the login page
    for role in ("participant", "judge_a"):
        assert web(role).get(url).status_code == 403
    assert web("organizer").get(url).status_code == 200


def test_organizer_writes_refuse_other_roles(web):
    for url in (f"/events/{EVENT}/manage/tracks", f"/events/{EVENT}/manage/publish",
                f"/events/{EVENT}/manage/assign", f"/events/{EVENT}/manage/invites"):
        assert web("judge_a").post(url, {"name": "x", "role": "judge"}).status_code == 403


def test_results_hidden_until_published(api, web):
    web("organizer").post(f"/events/{EVENT}/manage/calibration")
    for role in (None, "participant", "judge_a"):
        assert api(role).get(f"/api/events/{EVENT}/results").status_code == 404
        assert web(role).get(f"/events/{EVENT}/results").status_code == 404
    assert api("organizer").get(f"/api/events/{EVENT}/results").status_code == 200


def test_a_draft_is_hidden_from_strangers(api, web):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    project = Project.objects.filter(event__slug=EVENT).order_by("pk").first()
    Project.objects.filter(pk=project.pk).update(status="draft", submitted_at=None)
    for role in (None, "judge_b"):
        assert web(role).get(f"/projects/{project.pk}").status_code == 404
        assert api(role).get(f"/api/projects/{project.pk}").status_code == 404
    assert web("organizer").get(f"/projects/{project.pk}").status_code == 200
    member = project.team.members.first().user
    client = web()
    client.force_login(member)
    assert client.get(f"/projects/{project.pk}").status_code == 200
    assert project.title not in web().get("/projects").content.decode()


def test_editing_another_teams_project_is_refused(api):
    other = Project.objects.filter(event__slug=EVENT).exclude(team__members__user__email=EMAILS["participant"]).first()
    response = api("participant").patch(f"/api/projects/{other.pk}", {"title": "mine now"},
                                        content_type="application/json")
    assert response.status_code == 403


def test_pages_accept_tokens_with_the_same_role_checks(api):
    assert api("judge_a").get(f"/events/{EVENT}/manage").status_code == 403
    assert api("organizer").get(f"/events/{EVENT}/manage").status_code == 200
    from django.test import Client
    assert Client(HTTP_AUTHORIZATION="Bearer nope").get("/projects").status_code == 401
