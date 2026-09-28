"""The seed runs on every boot, so it must be safe to run again, and it has to
load the fixture's awkward cases the way the docs say."""
import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from portal.models import (AuditLog, Event, JudgeAssignment, Membership, Project, Review, Score, Team, TeamMember,
                           User)

from .conftest import FIXTURES

pytestmark = pytest.mark.django_db
DATA = json.loads(Path(FIXTURES).read_text())


def counts():
    return [m.objects.count() for m in (Event, Team, TeamMember, Project, JudgeAssignment, Review, Score, Membership,
                                        User)]


def test_fixture_loaded_whole():
    event = Event.objects.get(slug="sample-hack-2026")
    assert event.projects.count() == len(DATA["projects"])
    assert Review.objects.filter(assignment__event=event).count() == len(DATA["scores"])
    assert event.submissions_close.isoformat() == "2026-03-01T18:00:00+00:00"


def test_running_it_again_changes_nothing():
    before = counts()
    call_command("seed", fixtures=str(FIXTURES), stdout=io.StringIO())
    assert counts() == before


def test_seed_never_undoes_later_changes():
    project = Project.objects.get(external_id="prj_41")
    Project.objects.filter(pk=project.pk).update(duplicate_of=None)    # an organizer cleared the flag
    call_command("seed", fixtures=str(FIXTURES), stdout=io.StringIO())
    assert Project.objects.get(pk=project.pk).duplicate_of is None


def test_duplicate_submission_is_flagged():
    copy, original = Project.objects.get(external_id="prj_41"), Project.objects.get(external_id="prj_07")
    assert copy.duplicate_of == original
    assert AuditLog.objects.filter(action="project.flag_duplicate", object_id=str(copy.pk)).exists()


def test_demo_judges_have_scores_and_share_no_project():
    a = set(JudgeAssignment.objects.filter(judge__email="diego.herrera@example.org",
                                           event__slug="sample-hack-2026").values_list("project", flat=True))
    b = set(JudgeAssignment.objects.filter(judge__email="ines.rocha@example.org",
                                           event__slug="sample-hack-2026").values_list("project", flat=True))
    assert len(a) >= 3 and len(b) >= 3 and not a & b


def test_boot_prints_the_logins_in_the_spec_format():
    out = io.StringIO()
    call_command("seed", fixtures=str(FIXTURES), stdout=out)
    lines = out.getvalue().splitlines()
    start = lines.index("seeded. test logins:")
    assert [line.split()[0] for line in lines[start + 1:start + 5]] == ["organizer", "judge_a", "judge_b", "participant"]
    assert all("Authorization: Bearer bb_demo_" in line for line in lines[start + 1:start + 5])


def test_tokens_are_stored_hashed():
    from portal.models import ApiToken
    assert not ApiToken.objects.filter(token_hash__startswith="bb_").exists()
