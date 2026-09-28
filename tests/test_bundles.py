"""Event bundles: an event exported, imported under a new slug and exported
again comes back the same, and calibrates to exactly the same numbers."""
import io
import json

import pytest
from django.core.management import call_command
from django.db import connection

from portal.calibration import fit
from portal.models import AuditLog, Event, JudgeAssignment, Membership, Prize, Project, Review, Score, User
from portal.results import submitted_reviews
from portal.scoring import weighted_score

from .conftest import EMAILS, EVENT, FIXTURES

pytestmark = pytest.mark.django_db
EXPORT = f"/api/events/{EVENT}/export/bundle.json"


def post_bundle(client, bundle, slug):
    body = bundle if isinstance(bundle, str) else json.dumps(bundle)
    return client.post(f"/api/events/import?slug={slug}", body, content_type="application/json")


def comparable(bundle):
    bundle = json.loads(json.dumps(bundle))
    bundle["event"].pop("id")
    return bundle


def qualities(event):
    criteria = list(event.criteria.all())
    obs = [(r.assignment.judge_id, r.assignment.project.external_id, weighted_score(r, criteria))
           for r in submitted_reviews(event)]
    return {p: pf.quality for p, pf in fit([o for o in obs if o[2] is not None]).projects.items()}


def enrich_fixture_event():
    """Give the fixture event what the fixture file lacks, so the round trip
    has something to lose: a prize, tags, a cleared duplicate flag, a draft
    review, a pending and a recused assignment."""
    event = Event.objects.get(slug=EVENT)
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    Prize.objects.create(event=event, name="Best tool", description="For builders", track=event.tracks.first())
    Project.objects.filter(event=event, external_id="prj_02").update(tags=["cli", "rust"], tagline="Small, fast")
    Project.objects.filter(event=event, external_id="prj_41").update(duplicate_of=None)
    judge = User.objects.get(email=EMAILS["judge_a"])
    mine = set(JudgeAssignment.objects.filter(judge=judge).values_list("project", flat=True))
    free = (Project.objects.filter(event=event).exclude(pk__in=mine)
            .exclude(team__members__user=judge).order_by("pk"))[:3]
    pending = JudgeAssignment.objects.create(event=event, judge=judge, project=free[0])
    JudgeAssignment.objects.create(event=event, judge=judge, project=free[1], status="recused")
    draft = JudgeAssignment.objects.create(event=event, judge=judge, project=free[2])
    review = Review.objects.create(assignment=draft, comment="half done")
    Score.objects.create(review=review, criterion=event.criteria.first(), value=3)
    return event, pending


def test_round_trip_keeps_everything(api, web):
    event, _ = enrich_fixture_event()
    first = api("organizer").get(EXPORT)
    assert first.status_code == 200 and first["Content-Type"].startswith("application/json")
    bundle = first.json()
    assert bundle["prizes"][0]["name"] == "Best tool" and {"pending", "recused"} <= {
        a["status"] for a in bundle["assignments"]}
    assert any(s["submitted"] is False for s in bundle["scores"])

    response = post_bundle(web("admin"), bundle, "sample-copy")
    from portal.models import AuditLog
    enrolled = AuditLog.objects.filter(action="event.import_people").latest("seq").after["enrolled"]
    assert "diego.herrera@example.org" in enrolled
    assert response.status_code == 201, response.content
    copy = Event.objects.get(slug="sample-copy")
    assert copy.external_id is None
    assert Membership.objects.filter(event=copy, user__email=EMAILS["admin"], role="organizer").exists()
    second = web("admin").get("/api/events/sample-copy/export/bundle.json").json()
    assert comparable(second) == comparable(bundle)
    # The cleared flag stays cleared: the bundle's word, not a new detection.
    assert Project.objects.get(event=copy, external_id="prj_41").duplicate_of is None

    q = qualities(event)
    assert len(q) >= 30 and qualities(copy) == q          # exactly, not approximately


def test_fixture_file_imports_as_a_new_event(api, web):
    response = post_bundle(web("admin"), FIXTURES.read_text(), "fixture-copy")
    assert response.status_code == 201
    counts = response.json()["counts"]
    assert counts["projects"] == 41 and counts["reviews"] == 126 and counts["duplicates"] == 1


def test_export_is_organizer_only_and_audited(api):
    assert api().get(EXPORT).status_code == 401
    for role in ("participant", "judge_a", "judge_b"):
        assert api(role).get(EXPORT).status_code == 403
    assert api("organizer").get(EXPORT).status_code == 200
    assert AuditLog.objects.filter(action="export.bundle", event__slug=EVENT).exists()


def test_import_is_for_site_admins_only(api, web):
    bundle = api("organizer").get(EXPORT).json()
    assert post_bundle(api(), bundle, "x1").status_code == 401
    assert post_bundle(api("participant"), bundle, "x1").status_code == 403
    assert post_bundle(api("judge_b"), bundle, "x1").status_code == 403
    assert post_bundle(api("organizer"), bundle, "x1").status_code == 403       # an organizer can't enrol people
    assert post_bundle(web("admin"), bundle, EVENT).status_code == 409
    assert post_bundle(web("admin"), bundle, "not a slug").status_code == 422
    assert not Event.objects.filter(slug="x1").exists()


@pytest.mark.parametrize("breakage", [
    lambda b: "[1, 2]",
    lambda b: "{}",
    lambda b: {**b, "event": {}},
    lambda b: {**b, "projects": b["projects"] + [{**b["projects"][0], "id": "p_new", "team": "tm_nope"}]},
    lambda b: {**b, "projects": [{**b["projects"][0], "id": "p_js", "repo_url": "javascript:alert(1)"}]},
    lambda b: {**b, "judges": [{**b["judges"][0], "email": "not an email"}]},
    lambda b: {**b, "scores": [{**b["scores"][0], "status": "bribed"}]},
    lambda b: {**b, "scores": [{**b["scores"][0], "criteria": {"quality": 99}}]},
    lambda b: {**b, "scores": [{**b["scores"][0], "criteria": {"nope": 1}}]},
    lambda b: {**b, "projects": [{**b["projects"][0], "status": "winner"}]},
    lambda b: {**b, "criteria": [{"key": "x", "name": "X", "weight": "heavy"}]},
    lambda b: {**b, "event": {**b["event"], "max_team_size": 0}},
    lambda b: {**b, "tracks": [{"id": "t", "name": "n" * 500}]},
])
def test_a_malformed_bundle_is_422_and_leaves_nothing(api, web, breakage):
    bundle = api("organizer").get(EXPORT).json()
    response = post_bundle(web("admin"), breakage(bundle), "broken")
    assert response.status_code == 422, response.content
    assert not Event.objects.filter(slug="broken").exists()


def test_management_commands_round_trip(tmp_path):
    out = io.StringIO()
    call_command("export_event", EVENT, stdout=out)
    path = tmp_path / "bundle.json"
    path.write_text(out.getvalue())
    out = io.StringIO()
    call_command("import_event", str(path), slug="from-cli", stdout=out)
    assert "imported from-cli" in out.getvalue()
    assert Event.objects.get(slug="from-cli").projects.count() == Event.objects.get(slug=EVENT).projects.count()
