"""Signed participation records, the certificates that carry them, and the
public page that checks them.

A record says what someone did in an event (judged N projects, or was on a
team that submitted a project) and never how anyone scored: judges' scores
stay private. It is signed with the portal's Ed25519 key (signing.py), so a
person can show it anywhere and anyone can check it without trusting them,
or even reaching this portal. Every issuance is audited."""
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Min, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema, inline_serializer
from rest_framework import exceptions, permissions, serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from . import audit, signing
from .access import db_now, has_role, is_organizer
from .api import resolve_judge
from .models import Event, JudgeAssignment, Membership, Project, Review, TeamMember, User

Role = Membership.Role


def _plain(value):
    """Datetimes as UTC ISO strings to the second, recursively."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def judge_facts(user, event):
    mine = JudgeAssignment.objects.filter(event=event, judge=user).exclude(status=JudgeAssignment.Status.RECUSED)
    span = Review.objects.filter(assignment__in=mine, submitted_at__isnull=False).aggregate(
        n=Count("pk"), first=Min("submitted_at"), last=Max("submitted_at"))
    return {"role": Role.JUDGE.value, "projects_assigned": mine.count(), "reviews_submitted": span["n"],
            "first_review_at": span["first"], "last_review_at": span["last"]}


def participant_facts(user, event):
    member = TeamMember.objects.filter(event=event, user=user).select_related("team").first()
    projects = (member.team.projects.filter(status=Project.Status.SUBMITTED).order_by("submitted_at", "pk")
                if member else [])
    return {"role": Role.PARTICIPANT.value, "team": member.team.name if member else None,
            "projects": [{"title": p.title, "submitted_at": p.submitted_at} for p in projects]}


FACTS = {Role.JUDGE: judge_facts, Role.PARTICIPANT: participant_facts}


def role_in(user, event):
    """The role a record speaks for: judge wins over participant (a judge
    can hold a participant registration without a team)."""
    roles = set(Membership.objects.filter(user=user, event=event, role__in=list(FACTS)).values_list("role", flat=True))
    return next((r for r in FACTS if r in roles), None)


def issue(request, user, event, role):
    """Returns (signed document, the record's facts with real datetimes)."""
    facts = {"issuer": "ballotbench", "version": 1, "event": {"slug": event.slug, "name": event.name},
             "person": {"name": user.name or user.email, "email": user.email},
             **FACTS[role](user, event), "issued_at": db_now(),
             "verify_at": request.build_absolute_uri(reverse("verify"))}
    doc = signing.sign(_plain(facts))
    audit.record("record.issue", request=request, event=event, obj=user,
                 after={"role": role, "subject": user.email, "key_id": doc["record"]["key_id"],
                        "issued_at": doc["record"]["issued_at"]})
    return doc, facts


def _event_param(request):
    slug = request.query_params.get("event")
    if not slug:
        raise exceptions.ValidationError({"event": "the event's slug is required"})
    return get_object_or_404(Event, slug=slug)


SignedRecord = inline_serializer("SignedRecord", {
    "record": serializers.DictField(help_text="the signed facts; canonical JSON of this object is what is signed"),
    "signature": serializers.CharField(help_text="Ed25519 signature, base64url without padding")})


@extend_schema(
    parameters=[OpenApiParameter("event", str, required=True, description="Event slug"),
                OpenApiParameter("judge", str, description="Another judge of the event (fixture id, email or user "
                                 "id). Organizers of the event only; a judge naming anyone else gets 403.")],
    responses=SignedRecord,
    description="A signed record of your judging in one event: projects assigned, reviews submitted, between "
                "which dates. No scores. 403 unless you judge the event (or organize it and name a judge).")
@api_view(["GET"])
def api_judge_record(request):
    event = _event_param(request)
    target = request.user
    named = request.query_params.get("judge")
    if named and resolve_judge(named) != request.user:
        if not is_organizer(request.user, event):
            raise exceptions.PermissionDenied("judges can only fetch their own record")
        q = Q(external_id=named) | Q(user__email=named.lower())
        if named.isascii() and named.isdigit():
            q |= Q(user_id=int(named))
        membership = Membership.objects.filter(q, event=event, role=Role.JUDGE).select_related("user").first()
        if membership is None:
            raise exceptions.NotFound("no such judge in this event")
        target = membership.user
    elif not has_role(request.user, event, Role.JUDGE):
        raise exceptions.PermissionDenied("judges of this event only")
    return Response(issue(request, target, event, Role.JUDGE)[0])


@extend_schema(parameters=[OpenApiParameter("event", str, required=True, description="Event slug")],
               responses=SignedRecord,
               description="A signed record of your participation in one event: your team, and its submitted "
                           "projects with their submission times. 403 unless you're registered in the event.")
@api_view(["GET"])
def api_participant_record(request):
    event = _event_param(request)
    if not has_role(request.user, event, Role.PARTICIPANT):
        raise exceptions.PermissionDenied("participants of this event only")
    return Response(issue(request, request.user, event, Role.PARTICIPANT)[0])


@extend_schema(auth=[], responses=inline_serializer("SigningKey", {
    "algorithm": serializers.CharField(), "key_id": serializers.CharField(),
    "public_key": serializers.CharField(help_text="raw 32-byte key, base64url"),
    "pem": serializers.CharField()}),
    description="The public key that checks this portal's records. Public.")
@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def signing_key(request):
    return Response(signing.public_key_info())


@extend_schema(auth=[], request={"application/json": OpenApiTypes.OBJECT},
               responses=inline_serializer("Verification", {
                   "valid": serializers.BooleanField(), "reason": serializers.CharField(),
                   "record": serializers.DictField(allow_null=True)}),
               description="Check a record: send it exactly as issued, {\"record\": ..., \"signature\": ...}. "
                           "Always 200; `valid` says whether this portal's key signed exactly this record, "
                           "`reason` says why not. Public, and it only uses the public key.")
@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def api_verify(request):
    valid, reason, record = signing.verify(request.body.decode("utf-8", "replace"))
    return Response({"valid": valid, "reason": reason, "record": record})


def verify_page(request):
    text = (request.POST if request.method == "POST" else request.GET).get("record", "")
    valid, reason, record = signing.verify(text) if text.strip() else (None, "", None)
    return render(request, "portal/verify.html", {"text": text, "valid": valid, "reason": reason,
                                                  "record": record, "key": signing.public_key_info()})


@login_required
def certificate(request, slug):
    """Your own certificate, or, for the event's organizers, anyone's with
    ?person=<email>. Everyone else gets 404: we don't confirm who took part."""
    event = get_object_or_404(Event, slug=slug)
    person = request.user
    named = request.GET.get("person", "").strip().lower()
    if named and named != person.email:
        if not is_organizer(request.user, event):
            raise Http404
        person = get_object_or_404(User, email=named)
    role = role_in(person, event)
    if role is None:
        raise Http404
    doc, facts = issue(request, person, event, role)
    compact = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return render(request, "portal/certificate.html", {
        "event": event, "facts": facts, "doc": doc, "compact": compact,
        "verify_url": facts["verify_at"] + "?" + urlencode({"record": compact}),
    })
