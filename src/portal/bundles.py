"""Event bundles: a whole event as one JSON file, to take away and bring back.

A bundle is fixtures.json's shape plus what the fixture leaves out: dates,
the weighted rubric, prizes, judges' tracks, every project field, drafts,
recusals and each review's comment and state. Rows are named by their
external id, or `prj_<pk>` and the like when they have none, so a bundle
imported and exported again names everything the same way. Calibration
results aren't in it: they are recomputed from the scores, which are.

Importing goes through the same importer as the fixture, as a new event."""
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import DataError, IntegrityError
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import exceptions, serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import audit
from .access import Conflict, Invalid, guarded, organizer_event_or_deny
from .importer import import_event
from .models import Event, JudgeAssignment, Membership, Review

FORMAT = "ballotbench-bundle/1"
MALFORMED = (KeyError, TypeError, ValueError, AttributeError, ValidationError, IntegrityError, DataError)


def _ids(rows, prefix):
    """pk -> the row's external id, or prefix_<pk>; never two the same."""
    taken = {r.external_id for r in rows if r.external_id}
    out = {}
    for r in rows:
        ident = r.external_id
        if not ident:
            ident, n = f"{prefix}_{r.pk}", 0
            while ident in taken:
                n += 1
                ident = f"{prefix}_{r.pk}_{n}"
            taken.add(ident)
        out[r.pk] = ident
    return out


def _iso(value):
    return value.isoformat() if value else None


def export_bundle(event):
    tracks = list(event.tracks.order_by("pk"))
    track_ids = _ids(tracks, "trk")
    judges = list(Membership.objects.filter(event=event, role=Membership.Role.JUDGE)
                  .select_related("user").prefetch_related("tracks").order_by("pk"))
    judge_ids = _ids(judges, "jdg")
    judge_of = {m.user_id: judge_ids[m.pk] for m in judges}
    teams = list(event.teams.prefetch_related("members__user").order_by("pk"))
    team_ids = _ids(teams, "tm")
    projects = list(event.projects.order_by("pk"))
    project_ids = _ids(projects, "prj")
    # Only judges who still judge the event: an assignment of someone whose
    # role was taken away couldn't be imported (the database refuses it).
    reviews = (Review.objects.filter(assignment__event=event, assignment__judge_id__in=judge_of)
               .select_related("assignment").prefetch_related("scores__criterion").order_by("pk"))
    unreviewed = JudgeAssignment.objects.filter(event=event, review__isnull=True,
                                                judge_id__in=judge_of).order_by("pk")
    return {
        "format": FORMAT,
        "event": {"id": event.external_id or f"evt_{event.pk}", "name": event.name,
                  "description": event.description, "submissions_open": _iso(event.submissions_open),
                  "submissions_close": _iso(event.submissions_close), "judging_open": _iso(event.judging_open),
                  "judging_close": _iso(event.judging_close), "voting_open": _iso(event.voting_open),
                  "voting_close": _iso(event.voting_close), "reviews_per_project": event.reviews_per_project,
                  "max_team_size": event.max_team_size},
        "tracks": [{"id": track_ids[t.pk], "name": t.name} for t in tracks],
        "prizes": [{"name": p.name, "description": p.description, "track": track_ids.get(p.track_id)}
                   for p in event.prizes.order_by("pk")],
        "criteria": [{"key": c.key, "name": c.name, "weight": str(c.weight), "min_value": c.min_value,
                      "max_value": c.max_value, "position": c.position} for c in event.criteria.all()],
        "judges": [{"id": judge_ids[m.pk], "name": m.user.name, "email": m.user.email,
                    "tracks": [track_ids[t.pk] for t in m.tracks.all()]} for m in judges],
        "teams": [{"id": team_ids[t.pk], "name": t.name,
                   "members": sorted(m.user.email for m in t.members.all())} for t in teams],
        "projects": [{"id": project_ids[p.pk], "team": team_ids[p.team_id], "track": track_ids.get(p.track_id),
                      "title": p.title, "tagline": p.tagline, "summary": p.summary, "description": p.description,
                      "repo_url": p.repo_url, "demo_url": p.demo_url, "tags": p.tags, "status": p.status,
                      "submitted_at": _iso(p.submitted_at), "duplicate_of": project_ids.get(p.duplicate_of_id)}
                     for p in projects],
        "scores": [{"judge": judge_of[r.assignment.judge_id], "project": project_ids[r.assignment.project_id],
                    "criteria": {s.criterion.key: s.value for s in r.scores.all()}, "comment": r.comment,
                    "submitted": r.submitted_at is not None, "submitted_at": _iso(r.submitted_at),
                    "status": r.assignment.status, "source": r.assignment.source} for r in reviews],
        "assignments": [{"judge": judge_of[a.judge_id], "project": project_ids[a.project_id], "status": a.status,
                         "source": a.source} for a in unreviewed],
    }


@extend_schema(responses={(200, "application/json"): OpenApiTypes.OBJECT},
               description="The whole event as one JSON bundle, fixtures.json's shape extended with dates, "
                           "rubric weights, prizes, judges' tracks, drafts and every review. Organizers of the "
                           "event only; audited.")
@api_view(["GET"])
def export_bundle_view(request, slug):
    event = organizer_event_or_deny(request.user, slug)
    bundle = export_bundle(event)
    audit.record("export.bundle", request=request, event=event, obj=event,
                 after={"projects": len(bundle["projects"]), "reviews": len(bundle["scores"])})
    response = JsonResponse(bundle, json_dumps_params={"ensure_ascii": False, "indent": 1})
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-bundle.json"'
    return response


def bundle_import(data, slug, actor=None, name=""):
    """Import `data` as the new event `slug`, called `name` or else the
    bundle's name with " (imported)", so the copy can be told from the
    original: 409 if the slug is taken, 422 for anything malformed, and in
    either case nothing is kept."""
    if not isinstance(data, dict):
        raise Invalid("the body must be a bundle: a JSON object")
    try:
        validate_slug(slug)
    except ValidationError:
        raise Invalid("slug: letters, digits, hyphens and underscores") from None
    name = name.strip()
    if len(name) > 200:
        raise Invalid("name: at most 200 characters")
    if Event.objects.filter(slug=slug).exists():
        raise Conflict(f"there is already an event at {slug}")
    try:
        with guarded():
            event, counts = import_event(data, slug=slug, actor=actor, new=True)
            event.name = name or f"{event.name[:189]} (imported)"   # names are at most 200 characters
            event.save(update_fields=["name"])
            return event, counts
    except MALFORMED as exc:
        raise Invalid(f"not a valid bundle: {type(exc).__name__}: {exc}") from exc


@extend_schema(
    request={"application/json": OpenApiTypes.OBJECT},
    parameters=[OpenApiParameter("slug", str, required=True, description="The new event's slug"),
                OpenApiParameter("name", str, description="The new event's name. Default: the bundle's name "
                                                          "followed by \" (imported)\".")],
    responses={201: inline_serializer("Imported", {"event": serializers.CharField(),
                                                   "counts": serializers.DictField(child=serializers.IntegerField())})},
    description="Create a new event from a bundle (see the export). Site admins only: a bundle names people, and "
                "importing it enrols them, so it is the operator's call. You become an organizer of the new event. 409 if the slug is taken, 422 if the bundle is malformed. "
                "One transaction: a bad bundle leaves nothing behind.")
@api_view(["POST"])
def import_bundle_view(request):
    # Site admins only. A bundle names people by email, and importing one
    # enrols them: existing accounts become judges or team members of the new
    # event, and can then be issued signed records about it. An organizer of
    # some other event shouldn't be able to do that to anyone on the portal.
    if not request.user.is_staff:
        raise exceptions.PermissionDenied("importing an event is for site admins")
    event, counts = bundle_import(request.data, request.query_params.get("slug", ""), actor=request.user,
                                  name=request.query_params.get("name", ""))
    people = sorted(set(Membership.objects.filter(event=event).values_list("user__email", flat=True)))
    audit.record("event.import_people", request=request, event=event, obj=event, after={"enrolled": people})
    membership, _ = Membership.objects.get_or_create(user=request.user, event=event, role=Membership.Role.ORGANIZER)
    audit.record("role.grant", request=request, event=event, obj=membership,
                 after={"role": "organizer", "why": "imported the event"})
    return Response({"event": event.slug, "counts": counts}, status=status.HTTP_201_CREATED)
