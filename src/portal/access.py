"""Who may see what. Every scoped query in the portal starts here, so the
rules live in one place and the views can't forget them.

Status codes, the same on HTML pages and the API:
- 400: the request itself is malformed (a missing field, a wrong type).
- 401: no credentials on a route that needs them.
- 403: you lack the role, or you named another principal (`?judge=`).
- 404: the object exists but is outside your scope, so we don't confirm it.
- 409: the window for this action is closed, or a conflict of interest.
- 422: well-formed values the rules refuse (a score out of range).
"""
from contextlib import contextmanager

from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.db.models.functions import Now
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, status

from .models import Event, JudgeAssignment, Membership, Project

Role = Membership.Role


def has_role(user, event, role):
    if not user.is_authenticated:
        return False
    return Membership.objects.filter(user=user, event=event, role=role).exists()


def is_organizer(user, event):
    return user.is_authenticated and (user.is_staff or has_role(user, event, Role.ORGANIZER))


def organizer_events(user):
    if not user.is_authenticated:
        return Event.objects.none()
    if user.is_staff:
        return Event.objects.all()
    return Event.objects.filter(memberships__user=user, memberships__role=Role.ORGANIZER)


def judged_events(user):
    if not user.is_authenticated:
        return Event.objects.none()
    return Event.objects.filter(memberships__user=user, memberships__role=Role.JUDGE)


def judge_assignments(user):
    """A judge's own assignments, in events they still judge. Every judge
    query starts from this queryset; nothing else hands out assignments."""
    return JudgeAssignment.objects.filter(judge=user, event__in=judged_events(user))


def visible_projects(user):
    """Submitted projects are public. Drafts are visible to their own team and
    to the event's organizers."""
    q = Q(status=Project.Status.SUBMITTED)
    if user.is_authenticated:
        q |= Q(team__members__user=user) | Q(event__in=organizer_events(user))
    return Project.objects.filter(q).distinct()


def organizer_event_or_deny(user, slug):
    """The event, if `user` organizes it. 404 if the event doesn't exist,
    403 if it does and they don't organize it."""
    event = get_object_or_404(Event, slug=slug)
    if not is_organizer(user, event):
        raise exceptions.PermissionDenied("organizers of this event only")
    return event


def submissions_closed_reason(event):
    """None while `event` takes submissions, else why not. Read on the
    database's clock, the same clock the deadline trigger uses."""
    state = Event.objects.filter(pk=event.pk).values(
        early=Q(submissions_open__gt=Now()), late=Q(submissions_close__lte=Now())).get()
    if state["late"]:
        return f"Submissions for {event.name} closed at {event.submissions_close:%Y-%m-%d %H:%M} UTC."
    if state["early"]:
        return f"Submissions for {event.name} open at {event.submissions_open:%Y-%m-%d %H:%M} UTC."
    return None


def db_now():
    """The database's clock: the one every deadline is judged by."""
    with connection.cursor() as cur:
        cur.execute("SELECT now()")
        return cur.fetchone()[0]


class Conflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "not allowed at this stage"
    default_code = "conflict"


class Invalid(exceptions.APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "invalid"
    default_code = "invalid"


# SQLSTATEs our triggers raise, and what they mean to a client.
TRIGGER_ERRORS = {
    "BB409": Conflict,
    "BB410": Conflict,
    "BB423": Conflict,
    "BB422": Invalid,
    "BB403": exceptions.PermissionDenied,
}


def trigger_error(exc):
    """The API exception for a refusal raised by one of our triggers, or None."""
    cause = exc.__cause__
    state = getattr(getattr(cause, "diag", None), "sqlstate", None) or getattr(cause, "sqlstate", None)
    if state in TRIGGER_ERRORS:
        message = getattr(getattr(cause, "diag", None), "message_primary", None) or str(cause)
        return TRIGGER_ERRORS[state](message)
    return None


@contextmanager
def guarded():
    """Run writes in a savepoint and turn a trigger's refusal into a 4xx.
    The savepoint matters: the request's own transaction stays usable, so
    the error response can still be rendered and committed cleanly."""
    try:
        with transaction.atomic():
            yield
    except DatabaseError as exc:
        if (api_exc := trigger_error(exc)) is not None:
            raise api_exc from exc
        raise
