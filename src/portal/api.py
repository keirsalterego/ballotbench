"""The JSON API. Every route authenticates with a bearer token or a session,
and every object lookup goes through the scoped querysets in access.py."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import exceptions, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from . import audit
from .access import (Conflict, db_now, guarded, judge_assignments, organizer_events, submissions_closed_reason,
                     visible_projects)
from .duplicates import find_duplicates
from .models import Event, Membership, Project, Review, TeamMember, User
from .serializers import EventSerializer, ProjectSerializer, ReviewScoresSerializer

PROJECT_FIELDS = ["title", "tagline", "summary", "description", "repo_url", "demo_url", "tags", "track_id",
                  "status", "submitted_at"]


def require_submissions_open(event):
    if reason := submissions_closed_reason(event):
        raise Conflict(reason)


def flag_duplicates(request, event):
    for dup, original in find_duplicates(event):
        if dup.duplicate_of_id is None:
            dup.duplicate_of = original
            dup.save(update_fields=["duplicate_of", "updated_at"])
            audit.record("project.flag_duplicate", request=request, event=event, obj=dup,
                         after={"duplicate_of": original.pk})


def save_project(request, event, project, data, *, created):
    serializer = ProjectSerializer(project, data=data, partial=not created, context={"event": event})
    serializer.is_valid(raise_exception=True)
    before = None if created else audit.snapshot(project, PROJECT_FIELDS)
    submit = serializer.validated_data.pop("submit", False)
    with guarded():
        project = serializer.save()
        if submit and project.status != Project.Status.SUBMITTED:
            project.status = Project.Status.SUBMITTED
            project.submitted_at = db_now()
            project.save(update_fields=["status", "submitted_at", "updated_at"])
    action = "project.create" if created else "project.update"
    if submit:
        action = "project.submit"
    audit.record(action, request=request, event=event, obj=project, before=before,
                 after=audit.snapshot(project, PROJECT_FIELDS))
    if submit:
        flag_duplicates(request, event)
    return ProjectSerializer(project, context={"event": event}).data


@extend_schema(responses=EventSerializer(many=True))
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def events(request):
    return Response(EventSerializer(Event.objects.order_by("-submissions_close"), many=True).data)


@extend_schema(responses=EventSerializer)
@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def event_detail(request, slug):
    return Response(EventSerializer(get_object_or_404(Event, slug=slug)).data)


@extend_schema(methods=["GET"], responses=ProjectSerializer(many=True))
@extend_schema(methods=["POST"], request=ProjectSerializer, responses={201: ProjectSerializer},
               description="Create a project for your team in this event. 403 without a team in the event, "
                           "409 outside the submission window.")
@api_view(["GET", "POST"])
@permission_classes([permissions.AllowAny])
def event_projects(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if request.method == "GET":
        projects = visible_projects(request.user).filter(event=event).select_related("event", "team").order_by("pk")
        return Response(ProjectSerializer(projects, many=True, context={"event": event}).data)
    if not request.user.is_authenticated:
        raise exceptions.NotAuthenticated()
    member = TeamMember.objects.filter(event=event, user=request.user).select_related("team").first()
    if member is None:
        raise exceptions.PermissionDenied("you need a team in this event to submit a project")
    require_submissions_open(event)
    data = save_project(request, event, Project(event=event, team=member.team), request.data, created=True)
    return Response(data, status=201)


@extend_schema(methods=["GET"], responses=ProjectSerializer)
@extend_schema(methods=["PATCH"], request=ProjectSerializer, responses=ProjectSerializer,
               description="Edit your team's project. 404 if you can't see it, 403 if it isn't your team's, "
                           "409 after the deadline.")
@api_view(["GET", "PATCH"])
@permission_classes([permissions.AllowAny])
def project_detail(request, pk):
    project = get_object_or_404(visible_projects(request.user).select_related("event", "team"), pk=pk)
    event = project.event
    if request.method == "GET":
        return Response(ProjectSerializer(project, context={"event": event}).data)
    if not request.user.is_authenticated:
        raise exceptions.NotAuthenticated()
    if not project.team.members.filter(user=request.user).exists():
        raise exceptions.PermissionDenied("only the project's team can edit it")
    require_submissions_open(event)
    return Response(save_project(request, event, project, request.data, created=False))


def resolve_judge(value):
    """A judge named by fixture id (jdg_24), email, or user id."""
    q = Q(email=value.lower()) | Q(memberships__role=Membership.Role.JUDGE, memberships__external_id=value)
    if value.isascii() and value.isdigit():
        q |= Q(pk=int(value))
    return User.objects.filter(q).distinct().first()


@extend_schema(
    parameters=[OpenApiParameter("judge", str, description="Another judge (fixture id, email or user id). "
                                 "Judges may only name themselves (403 otherwise); organizers may name any "
                                 "judge of events they organize."),
                OpenApiParameter("event", str, description="Event slug")],
    responses=ReviewScoresSerializer(many=True))
@api_view(["GET"])
def judge_scores(request):
    """Your own reviews and scores. A judge gets exactly their own; naming
    another judge is refused with 403, never filtered to an empty list, so
    the refusal can't be mistaken for "no scores"."""
    user = request.user
    named = request.query_params.get("judge")
    target = user
    if named:
        target = resolve_judge(named)
        if target != user:
            if not organizer_events(user).exists():
                raise exceptions.PermissionDenied("judges can only read their own scores")
            if target is None:
                raise exceptions.NotFound("no such judge")
    if target == user:
        assignments = judge_assignments(user)
        if not assignments.exists() and not Membership.objects.filter(user=user, role=Membership.Role.JUDGE).exists():
            raise exceptions.PermissionDenied("judges only")
    else:
        assignments = target.assignments.filter(event__in=organizer_events(user))
    if slug := request.query_params.get("event"):
        assignments = assignments.filter(event__slug=slug)
    reviews = (Review.objects.filter(assignment__in=assignments)
               .select_related("assignment__event", "assignment__project", "assignment__judge")
               .prefetch_related("scores__criterion", "assignment__event__criteria")
               .order_by("assignment__event_id", "assignment__project_id"))
    return Response(ReviewScoresSerializer(reviews, many=True).data)
