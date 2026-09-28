"""CSV exports, organizer only, one per stage of an event. Every export is
audited. Cells that a spreadsheet would read as a formula are prefixed with
a quote, since project titles and comments come from participants."""
import csv
import io

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions
from rest_framework.decorators import api_view

from . import audit
from .access import organizer_event_or_deny
from .models import JudgeAssignment, Membership, Project, Review, TeamMember
from .scoring import weighted_score

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(value):
    if value is None:
        return ""
    text = str(value)
    if text.startswith(FORMULA_START) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def registrations(event):
    yield ["user_email", "name", "role", "tracks"]
    for m in (Membership.objects.filter(event=event).select_related("user")
              .prefetch_related("tracks").order_by("role", "user__email")):
        yield [m.user.email, m.user.name, m.role, ";".join(t.name for t in m.tracks.all())]


def teams(event):
    yield ["team_id", "external_id", "team", "member_email", "joined_at"]
    for m in TeamMember.objects.filter(event=event).select_related("team", "user").order_by("team_id", "user__email"):
        yield [m.team.pk, m.team.external_id, m.team.name, m.user.email, m.joined_at.isoformat()]


def projects(event):
    yield ["project_id", "external_id", "title", "team", "track", "status", "submitted_at", "repo_url",
           "demo_url", "tags", "duplicate_of"]
    for p in Project.objects.filter(event=event).select_related("team", "track").order_by("pk"):
        yield [p.pk, p.external_id, p.title, p.team.name, p.track.name if p.track else "", p.status,
               p.submitted_at.isoformat() if p.submitted_at else "", p.repo_url, p.demo_url, ";".join(p.tags),
               p.duplicate_of_id or ""]


def assignments(event):
    yield ["assignment_id", "judge_email", "project_id", "project", "status", "source", "created_at"]
    for a in JudgeAssignment.objects.filter(event=event).select_related("judge", "project").order_by("pk"):
        yield [a.pk, a.judge.email, a.project_id, a.project.title, a.status, a.source, a.created_at.isoformat()]


def scores(event):
    """One row per review: every criterion, the weighted score, the comment."""
    criteria = list(event.criteria.all())
    yield (["review_id", "judge_email", "project_id", "project", "submitted_at"]
           + [c.key for c in criteria] + ["weighted_0_1", "comment"])
    reviews = (Review.objects.filter(assignment__event=event)
               .select_related("assignment__judge", "assignment__project")
               .prefetch_related("scores").order_by("assignment__project_id", "assignment__judge__email"))
    for r in reviews:
        values = {s.criterion_id: s.value for s in r.scores.all()}
        total = weighted_score(r, criteria)
        yield ([r.pk, r.assignment.judge.email, r.assignment.project_id, r.assignment.project.title,
                r.submitted_at.isoformat() if r.submitted_at else ""]
               + [values.get(c.pk, "") for c in criteria]
               + ["" if total is None else f"{total:.4f}", r.comment])


EXPORTS = {
    "registrations": registrations,
    "teams": teams,
    "projects": projects,
    "assignments": assignments,
    "scores": scores,
}


def render_csv(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return buffer.getvalue()


@extend_schema(responses={(200, "text/csv"): str},
               description="kinds: " + ", ".join(EXPORTS) + ". Organizers of the event only.")
@api_view(["GET"])
def export_csv(request, slug, kind):
    event = organizer_event_or_deny(request.user, slug)
    if kind not in EXPORTS:
        raise exceptions.NotFound(f"no export called {kind}")
    body = render_csv(EXPORTS[kind](event))
    audit.record("export.csv", request=request, event=event, obj=event, after={"kind": kind})
    response = HttpResponse(body, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
    return response
