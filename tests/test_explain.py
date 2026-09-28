"""Explain my rank: who may see it, and that its weights add up."""
import pytest

from portal.explain import describe, explanation
from portal.models import CalibrationRun, Project

from .conftest import EVENT

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(web):
    web("organizer").post(f"/events/{EVENT}/manage/calibration")
    return CalibrationRun.objects.filter(event__slug=EVENT).latest("pk")


def own_project():
    return Project.objects.get(event__slug=EVENT, external_id="prj_01")      # priya's team


def test_hidden_until_published_except_for_organizers(web, run):
    url = f"/events/{EVENT}/results/{own_project().pk}"
    assert web("participant").get(url).status_code == 404
    assert web("organizer").get(url).status_code == 200


def test_after_publishing_only_the_team_and_organizers_see_it(web, run):
    web("organizer").post(f"/events/{EVENT}/manage/publish")
    url = f"/events/{EVENT}/results/{own_project().pk}"
    assert web("participant").get(url).status_code == 200
    assert web().get(url).status_code == 404
    assert web("judge_a").get(url).status_code == 404
    other = Project.objects.get(event__slug=EVENT, external_id="prj_02")
    assert web("participant").get(f"/events/{EVENT}/results/{other.pk}").status_code == 404


def test_shares_add_up_and_ignored_judges_weigh_nothing(run):
    for project in Project.objects.filter(event__slug=EVENT, duplicate_of__isnull=True)[:15]:
        info = explanation(run, project)
        assert abs(sum(line["share"] for line in info["lines"]) + info["prior_share"] - 1) < 1e-9
        for line in info["lines"]:
            if line["flag"] != "ok":
                assert line["share"] == 0


def test_no_judge_identity_or_comment_on_the_page(web, run):
    web("organizer").post(f"/events/{EVENT}/manage/publish")
    body = web("participant").get(f"/events/{EVENT}/results/{own_project().pk}").content.decode()
    assert "@example.org" not in body.replace("priya1@example.org", "")
    assert "jdg_" not in body
    assert "Runs clean." not in body         # prj_01's first review comment


def test_describe():
    assert describe("constant", 0, None) == "gave every project the same score"
    assert describe("ok", 0.1, 2.0) == "a generous judge who separates projects sharply"
    assert describe("ok", -0.1, 1.0) == "a harsh judge"
