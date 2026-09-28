"""Load a fixtures.json-shaped event into the portal.

Idempotent: every imported row is keyed by its fixture id (external_id), so
running it again inserts only what is missing and never overwrites what
people changed afterwards. It runs with ballotbench.import set, the one
sanctioned bypass of the deadline trigger (fixture projects were submitted
before a close date that has already passed), and it says so in the audit
log.

It also reads event bundles (bundles.py): the same shape with the fields the
fixture lacks, all optional here, so the fixture itself still loads as
before. A bundle may come from anyone who organizes an event, so links and
emails are validated as the forms would, and every enum is checked."""
from datetime import datetime

from django.core.validators import URLValidator, validate_email
from django.db import connection, transaction
from django.utils.text import slugify

from . import audit
from .duplicates import find_duplicates
from .models import (Event, JudgeAssignment, Membership, Prize, Project, Review, RubricCriterion, Score, Team,
                     TeamMember, Track, User)

CRITERIA = [("functionality", "Functionality"), ("quality", "Quality"), ("innovation", "Innovation")]


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_dt(data, key, default=None):
    """A date the fixture may leave out (then `default`) or set to null."""
    if key not in data:
        return default
    return _dt(data[key]) if data[key] else None


def _url(value):
    if value:
        URLValidator(schemes=["http", "https"])(value)
    return value or ""


def _choice(value, choices, default):
    value = value or default
    if value not in choices.values:
        raise ValueError(f"{value!r} isn't one of {', '.join(choices.values)}")
    return value


def _user(email, name=""):
    validate_email(email)
    user, created = User.objects.get_or_create(email=email.lower(), defaults={"name": name})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


@transaction.atomic
def import_event(data, *, slug=None, actor=None, new=False):
    """Returns (event, counts of rows created). With new=True, `data` becomes
    a new event called `slug`, with no external id, so one bundle can be
    imported any number of times under different slugs. ValueError, KeyError
    or a ValidationError mean the data was malformed; nothing is kept."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    counts = dict.fromkeys(["tracks", "prizes", "judges", "teams", "members", "projects", "reviews", "assignments",
                            "duplicates"], 0)
    ev = data["event"]
    close = _dt(ev["submissions_close"])
    submitted = [_dt(p["submitted_at"]) for p in data["projects"] if p.get("submitted_at")]
    # The fixture gives only the close. We open submissions on the day of the
    # earliest submission, and open judging at the close.
    opens = min(submitted + [close]).replace(hour=0, minute=0, second=0, microsecond=0)
    fields = dict(slug=slug or slugify(ev["name"]), name=ev["name"], description=ev.get("description", ""),
                  submissions_open=_optional_dt(ev, "submissions_open", opens), submissions_close=close,
                  judging_open=_optional_dt(ev, "judging_open", close), judging_close=_optional_dt(ev, "judging_close"),
                  voting_open=_optional_dt(ev, "voting_open"), voting_close=_optional_dt(ev, "voting_close"),
                  **{k: int(ev[k]) for k in ("reviews_per_project", "max_team_size") if k in ev})
    if new:
        if not slug or Event.objects.filter(slug=slug).exists():
            raise ValueError(f"the slug {slug!r} is taken or empty")
        event, created = Event.objects.create(**fields), True
    else:
        event, created = Event.objects.get_or_create(external_id=ev["id"], defaults=fields)
    if created:
        rubric = data.get("criteria") or [{"key": k, "name": n} for k, n in CRITERIA]
        for position, c in enumerate(rubric):
            RubricCriterion.objects.create(event=event, key=c["key"], name=c["name"], weight=c.get("weight", 1),
                                           min_value=int(c.get("min_value", 1)), max_value=int(c.get("max_value", 5)),
                                           position=int(c.get("position", position)))
    criteria = {c.key: c for c in event.criteria.all()}

    tracks = {}
    for t in data["tracks"]:
        tracks[t["id"]], made = Track.objects.get_or_create(event=event, external_id=t["id"], defaults={"name": t["name"]})
        counts["tracks"] += made

    for p in data.get("prizes", []):
        _, made = Prize.objects.get_or_create(event=event, name=p["name"], defaults={
            "description": p.get("description", ""), "track": tracks.get(p.get("track"))})
        counts["prizes"] += made

    judges = {}
    for j in data["judges"]:
        user = _user(j["email"], j.get("name", ""))
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

    projects, created_projects = {}, set()
    for p in data["projects"]:
        at = p.get("submitted_at")
        project, made = Project.objects.get_or_create(
            event=event, external_id=p["id"],
            defaults=dict(team=teams[p["team"]], track=tracks.get(p.get("track")), title=p["title"],
                          tagline=p.get("tagline", ""), summary=p.get("summary", ""),
                          description=p.get("description", ""), repo_url=_url(p.get("repo_url")),
                          demo_url=_url(p.get("demo_url")), tags=[str(t) for t in p.get("tags", [])],
                          status=p.get("status") or (Project.Status.SUBMITTED if at else Project.Status.DRAFT),
                          submitted_at=_dt(at) if at else None))
        counts["projects"] += made
        projects[p["id"]] = project
        if made:
            created_projects.add(p["id"])

    for s in data["scores"]:
        done = s.get("submitted", True)
        status = _choice(s.get("status"), JudgeAssignment.Status,
                         JudgeAssignment.Status.DONE if done else JudgeAssignment.Status.PENDING)
        assignment, made = JudgeAssignment.objects.get_or_create(
            judge=judges[s["judge"]], project=projects[s["project"]],
            defaults=dict(event=event, status=status,
                          source=_choice(s.get("source"), JudgeAssignment.Source, JudgeAssignment.Source.SEED)))
        if not made:
            continue
        at = s.get("submitted_at")
        review = Review.objects.create(assignment=assignment, comment=s.get("comment", ""),
                                       submitted_at=(_dt(at) if at else close) if done else None)
        Score.objects.bulk_create(Score(review=review, criterion=criteria[k], value=int(v))
                                  for k, v in s["criteria"].items())
        counts["reviews"] += 1

    for a in data.get("assignments", []):
        _, made = JudgeAssignment.objects.get_or_create(
            judge=judges[a["judge"]], project=projects[a["project"]],
            defaults=dict(event=event,
                          status=_choice(a.get("status"), JudgeAssignment.Status, JudgeAssignment.Status.PENDING),
                          source=_choice(a.get("source"), JudgeAssignment.Source, JudgeAssignment.Source.SEED)))
        counts["assignments"] += made

    if any("duplicate_of" in p for p in data["projects"]):
        # A bundle says which projects are duplicates, including flags an
        # organizer cleared, so we take its word rather than detect again.
        pairs = [(projects[p["id"]], projects[p["duplicate_of"]]) for p in data["projects"]
                 if p.get("duplicate_of") and p["id"] in created_projects]
        reason = "from the bundle"
    else:
        pairs = [(dup, original) for dup, original in find_duplicates(event) if counts["projects"]]
        reason = "same repo and title"
    for dup, original in pairs:
        if dup.duplicate_of_id is None:
            dup.duplicate_of = original
            dup.save(update_fields=["duplicate_of", "updated_at"])
            counts["duplicates"] += 1
            audit.record("project.flag_duplicate", actor=actor, event=event, obj=dup,
                         after={"duplicate_of": original.pk, "reason": reason})

    if created or any(counts.values()):
        audit.record("event.import", actor=actor, event=event, obj=event,
                     after={**counts, "deadline_bypass": True, "source": ev["id"]})
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'off'")    # the bypass ends with the import, not the request
    return event, counts
