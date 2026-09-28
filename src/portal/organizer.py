"""The organizer console: create an event, set its dates, tracks, prizes and
rubric, and invite judges and co-organizers. Every view checks the role on
the server; every change is audited."""
import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from . import audit
from .access import db_now, guarded, is_organizer, organizer_events
from .models import Event, Membership, Prize, RoleInvite, RubricCriterion, Track

ROLE_INVITE_LIFETIME = timedelta(days=7)
# Words that appear where an event slug would in a URL (/api/events/import).
RESERVED_SLUGS = {"import", "new", "export"}
EVENT_FIELDS = ["name", "description", "submissions_open", "submissions_close", "judging_open", "judging_close",
                "voting_open", "voting_close", "voting_mode", "vote_credits", "reviews_per_project", "max_team_size"]


def organizer_required(view):
    """Loads the event named by `slug`: 404 if there is none, 403 unless the
    caller organizes it (or is an admin)."""
    @login_required
    @wraps(view)
    def wrapper(request, slug, *args, **kwargs):
        event = get_object_or_404(Event, slug=slug)
        if not is_organizer(request.user, event):
            raise PermissionDenied
        return view(request, event, *args, **kwargs)
    return wrapper


def can_create_events(user):
    return user.is_staff or organizer_events(user).exists()


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ["name", "slug", "description", "submissions_open", "submissions_close", "judging_open",
                  "judging_close", "voting_open", "voting_close", "voting_mode", "vote_credits", "reviews_per_project",
                  "max_team_size"]
        widgets = {f: forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
                   for f in ["submissions_open", "submissions_close", "judging_open", "judging_close",
                             "voting_open", "voting_close"]}
        widgets["description"] = forms.Textarea(attrs={"rows": 3})
        help_texts = {"reviews_per_project": "k: how many judges review each project",
                      "voting_mode": "who may vote in the community vote: nobody, anyone signed in, or anyone "
                                     "who confirms an email address",
                      "vote_credits": "each voter's budget: n votes for one project cost n² credits",
                      "slug": "used in URLs; can't be changed later"}

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if slug in RESERVED_SLUGS:
            raise forms.ValidationError("that name is used by the portal itself; pick another")
        return slug

    def clean(self):
        data = super().clean()
        for start, end in (("submissions_open", "submissions_close"), ("judging_open", "judging_close"),
                           ("voting_open", "voting_close")):
            if data.get(start) and data.get(end) and data[start] >= data[end]:
                self.add_error(end, "must be after the opening time")
        return data


@login_required
def create_event(request):
    if not can_create_events(request.user):
        raise PermissionDenied
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save()
        Membership.objects.create(user=request.user, event=event, role=Membership.Role.ORGANIZER)
        for position, (key, name) in enumerate([("functionality", "Functionality"), ("quality", "Quality"),
                                                 ("innovation", "Innovation")]):
            RubricCriterion.objects.create(event=event, key=key, name=name, position=position)
        audit.record("event.create", request=request, event=event, obj=event, after=audit.snapshot(event, EVENT_FIELDS))
        messages.success(request, "Event created with a default three-criterion rubric. Adjust it below.")
        return redirect("manage", slug=event.slug)
    return render(request, "portal/organizer/event_form.html", {"form": form})


@organizer_required
def manage(request, event):
    form = EventForm(request.POST or None, instance=event)
    form.fields["slug"].disabled = True
    if request.method == "POST" and form.is_valid():
        before = audit.snapshot(Event.objects.get(pk=event.pk), EVENT_FIELDS)
        event = form.save()
        audit.record("event.update", request=request, event=event, obj=event, before=before,
                     after=audit.snapshot(event, EVENT_FIELDS))
        messages.success(request, "Saved.")
        return redirect("manage", slug=event.slug)
    return render(request, "portal/organizer/manage.html", {
        "event": event, "form": form,
        "tracks": event.tracks.all(), "prizes": event.prizes.select_related("track"),
        "criteria": event.criteria.all(),
        "frozen": RubricCriterion.objects.filter(event=event, score__isnull=False).exists(),
        "judges": (Membership.objects.filter(event=event, role=Membership.Role.JUDGE)
                   .select_related("user").prefetch_related("tracks").order_by("user__email")),
        "organizers": Membership.objects.filter(event=event, role=Membership.Role.ORGANIZER).select_related("user"),
        "invites": event.role_invites.filter(used_at__isnull=True, expires_at__gt=db_now()),
        "new_link": request.session.pop("new_role_link", None),
    })


@organizer_required
@require_POST
def add_track(request, event):
    name = request.POST.get("name", "").strip()[:200]
    if name:
        try:
            with guarded():
                track = Track.objects.create(event=event, name=name)
            audit.record("track.create", request=request, event=event, obj=track, after={"name": name})
        except IntegrityError:
            messages.error(request, f"There is already a track called {name}.")
    return redirect("manage", slug=event.slug)


@organizer_required
@require_POST
def delete_track(request, event, pk):
    track = get_object_or_404(event.tracks, pk=pk)
    audit.record("track.delete", request=request, event=event, obj=track, before={"name": track.name})
    track.delete()
    return redirect("manage", slug=event.slug)


@organizer_required
@require_POST
def add_prize(request, event):
    name = request.POST.get("name", "").strip()[:200]
    track = event.tracks.filter(pk=request.POST.get("track") or 0).first()
    if name:
        try:
            with guarded():
                prize = Prize.objects.create(event=event, name=name, track=track,
                                             description=request.POST.get("description", "")[:2000])
            audit.record("prize.create", request=request, event=event, obj=prize, after={"name": name})
        except IntegrityError:
            messages.error(request, f"There is already a prize called {name}.")
    return redirect("manage", slug=event.slug)


@organizer_required
@require_POST
def delete_prize(request, event, pk):
    prize = get_object_or_404(event.prizes, pk=pk)
    audit.record("prize.delete", request=request, event=event, obj=prize, before={"name": prize.name})
    prize.delete()
    return redirect("manage", slug=event.slug)


class CriterionForm(forms.ModelForm):
    class Meta:
        model = RubricCriterion
        fields = ["key", "name", "weight", "min_value", "max_value", "position"]


@organizer_required
@require_POST
def save_criterion(request, event, pk=None):
    """Add or change a rubric criterion. Refused (by a trigger) once the
    event has any score, so weights can't be tuned after reading results."""
    criterion = get_object_or_404(event.criteria, pk=pk) if pk else RubricCriterion(event=event)
    before = None if pk is None else audit.snapshot(criterion, ["key", "name", "weight", "min_value", "max_value"])
    form = CriterionForm(request.POST, instance=criterion)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
        return redirect("manage", slug=event.slug)
    try:
        with guarded():
            criterion = form.save()
    except APIException as exc:
        messages.error(request, str(exc.detail))
        return redirect("manage", slug=event.slug)
    except IntegrityError:
        messages.error(request, "That key is already used, or the range is invalid.")
        return redirect("manage", slug=event.slug)
    audit.record("rubric.update" if pk else "rubric.create", request=request, event=event, obj=criterion,
                 before=before, after=audit.snapshot(criterion, ["key", "name", "weight", "min_value", "max_value"]))
    return redirect("manage", slug=event.slug)


@organizer_required
@require_POST
def delete_criterion(request, event, pk):
    criterion = get_object_or_404(event.criteria, pk=pk)
    try:
        with guarded():
            criterion.delete()
    except APIException as exc:
        messages.error(request, str(exc.detail))
        return redirect("manage", slug=event.slug)
    audit.record("rubric.delete", request=request, event=event, obj=criterion, before={"key": criterion.key})
    return redirect("manage", slug=event.slug)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@organizer_required
@require_POST
def create_role_invite(request, event):
    role = request.POST.get("role")
    if role not in (Membership.Role.JUDGE, Membership.Role.ORGANIZER):
        messages.error(request, "Pick judge or organizer.")
        return redirect("manage", slug=event.slug)
    token = secrets.token_urlsafe(24)
    invite = RoleInvite.objects.create(event=event, role=role, note=request.POST.get("note", "")[:200],
                                       token_hash=_hash(token), created_by=request.user,
                                       expires_at=db_now() + ROLE_INVITE_LIFETIME)
    invite.tracks.set(event.tracks.filter(pk__in=request.POST.getlist("tracks")))
    audit.record("role.invite", request=request, event=event, obj=invite, after={"role": role, "note": invite.note})
    request.session["new_role_link"] = request.build_absolute_uri(f"/join/{token}")
    return redirect("manage", slug=event.slug)


@organizer_required
@require_POST
def revoke_role_invite(request, event, pk):
    invite = get_object_or_404(event.role_invites, pk=pk, used_at__isnull=True)
    invite.expires_at = db_now()
    invite.save(update_fields=["expires_at"])
    audit.record("role.invite_revoke", request=request, event=event, obj=invite)
    return redirect("manage", slug=event.slug)


def accept_role_invite(request, token):
    invite = (RoleInvite.objects.select_related("event")
              .filter(token_hash=_hash(token), used_at__isnull=True, expires_at__gt=db_now()).first())
    if invite is None:
        return render(request, "portal/organizer/role_invite.html", {"invalid": True}, status=404)
    if not request.user.is_authenticated or request.method != "POST":
        return render(request, "portal/organizer/role_invite.html", {"invite": invite})
    try:
        with guarded():
            invite = RoleInvite.objects.select_for_update().get(pk=invite.pk, used_at__isnull=True)
            membership, _ = Membership.objects.get_or_create(user=request.user, event=invite.event, role=invite.role)
            if invite.role == Membership.Role.JUDGE:
                membership.tracks.add(*invite.tracks.all())
            invite.used_by, invite.used_at = request.user, db_now()
            invite.save(update_fields=["used_by", "used_at"])
    except APIException:
        # The only refusal here is the conflict-of-interest trigger.
        error = "You're on a team in this event, so you can't also judge it."
        return render(request, "portal/organizer/role_invite.html", {"invite": invite, "error": error}, status=409)
    audit.record("role.accept", request=request, event=invite.event, obj=membership, after={"role": invite.role})
    messages.success(request, f"You are now a {invite.role} of {invite.event.name}.")
    return redirect("home")


@organizer_required
@require_POST
def set_judge_tracks(request, event, pk):
    membership = get_object_or_404(Membership, event=event, role=Membership.Role.JUDGE, pk=pk)
    tracks = event.tracks.filter(pk__in=request.POST.getlist("tracks"))
    before = sorted(membership.tracks.values_list("name", flat=True))
    membership.tracks.set(tracks)
    audit.record("judge.tracks", request=request, event=event, obj=membership, before={"tracks": before},
                 after={"tracks": sorted(t.name for t in tracks)})
    return redirect("manage", slug=event.slug)
