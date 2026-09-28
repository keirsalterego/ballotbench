"""Load a fixtures.json-shaped event into the portal.

Idempotent: every imported row is keyed by its fixture id (external_id), so
running it again inserts only what is missing and never overwrites what
people changed afterwards. It runs with ballotbench.import set, the one
sanctioned bypass of the deadline trigger (fixture projects were submitted
before a close date that has already passed), and it says so in the audit
log."""
from datetime import datetime

from django.db import connection, transaction
from django.utils.text import slugify

from . import audit
from .duplicates import find_duplicates
from .models import (Event, JudgeAssignment, Membership, Project, Review, RubricCriterion, Score, Team,
                     TeamMember, Track, User)

CRITERIA = [("functionality", "Functionality"), ("quality", "Quality"), ("innovation", "Innovation")]


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _user(email, name=""):
    user, created = User.objects.get_or_create(email=email.lower(), defaults={"name": name})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


@transaction.atomic
def import_event(data, *, slug=None, actor=None):
    """Returns (event, counts of rows created)."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    counts = dict.fromkeys(["tracks", "judges", "teams", "members", "projects", "reviews", "duplicates"], 0)
    ev = data["event"]
    close = _dt(ev["submissions_close"])
    submitted = [_dt(p["submitted_at"]) for p in data["projects"] if p.get("submitted_at")]
    # The fixture gives only the close. We open submissions on the day of the
    # earliest submission, and open judging at the close.
    opens = min(submitted + [close]).replace(hour=0, minute=0, second=0, microsecond=0)
    event, created = Event.objects.get_or_create(
        external_id=ev["id"],
        defaults=dict(slug=slug or slugify(ev["name"]), name=ev["name"], submissions_open=opens,
                      submissions_close=close, judging_open=close))
    if created:
        for position, (key, name) in enumerate(CRITERIA):
            RubricCriterion.objects.create(event=event, key=key, name=name, position=position)
    criteria = {c.key: c for c in event.criteria.all()}

    tracks = {}
    for t in data["tracks"]:
        tracks[t["id"]], made = Track.objects.get_or_create(event=event, external_id=t["id"], defaults={"name": t["name"]})
        counts["tracks"] += made

    judges = {}
    for j in data["judges"]:
        user = _user(j["email"], j["name"])
        m, made = Membership.objects.get_or_create(user=user, event=event, role=Membership.Role.JUDGE,
                                                   defaults={"external_id": j["id"]})
        if made:
            m.tracks.set([tracks[t] for t in j.get("tracks", []) if t in tracks])
        counts["judges"] += made
        judges[j["id"]] = user

    teams = {}
    for t in data["teams"]:
        team, made = Team.objects.get_or_create(event=event, external_id=t["id"], defaults={"name": t["name"]})
        counts["teams"] += made
        teams[t["id"]] = team
        for email in t["members"]:
            user = _user(email)
            _, joined = TeamMember.objects.get_or_create(event=event, user=user, defaults={"team": team})
            counts["members"] += joined
            Membership.objects.get_or_create(user=user, event=event, role=Membership.Role.PARTICIPANT)

    projects = {}
    for p in data["projects"]:
        at = p.get("submitted_at")
        project, made = Project.objects.get_or_create(
            event=event, external_id=p["id"],
            defaults=dict(team=teams[p["team"]], track=tracks.get(p.get("track")), title=p["title"],
                          summary=p.get("summary", ""), repo_url=p.get("repo_url", ""),
                          status=Project.Status.SUBMITTED if at else Project.Status.DRAFT,
                          submitted_at=_dt(at) if at else None))
        counts["projects"] += made
        projects[p["id"]] = project

    for s in data["scores"]:
        assignment, made = JudgeAssignment.objects.get_or_create(
            judge=judges[s["judge"]], project=projects[s["project"]],
            defaults=dict(event=event, status=JudgeAssignment.Status.DONE, source=JudgeAssignment.Source.SEED))
        if not made:
            continue
        review = Review.objects.create(assignment=assignment, comment=s.get("comment", ""), submitted_at=close)
        Score.objects.bulk_create(Score(review=review, criterion=criteria[k], value=v)
                                  for k, v in s["criteria"].items())
        counts["reviews"] += 1

    for dup, original in find_duplicates(event):
        if dup.duplicate_of_id is None and counts["projects"]:
            dup.duplicate_of = original
            dup.save(update_fields=["duplicate_of", "updated_at"])
            counts["duplicates"] += 1
            audit.record("project.flag_duplicate", actor=actor, event=event, obj=dup,
                         after={"duplicate_of": original.pk, "reason": "same repo and title"})

    if any(counts.values()):
        audit.record("event.import", actor=actor, event=event, obj=event,
                     after={**counts, "deadline_bypass": True, "source": ev["id"]})
    return event, counts
