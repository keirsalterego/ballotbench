"""Participants: join an event, form a team by invite link, and draft, edit
and submit a project until the deadline."""
import hashlib
import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from rest_framework.exceptions import APIException

from . import audit
from .access import db_now, guarded, submissions_closed_reason
from .duplicates import find_duplicates
from .models import Event, Membership, Project, Team, TeamInvite, TeamMember

INVITE_LIFETIME = timedelta(hours=72)
PROJECT_FIELDS = ["title", "tagline", "summary", "description", "repo_url", "demo_url", "tags", "track_id",
                  "status", "submitted_at"]


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def closed(request, event, template="portal/closed.html"):
    """The page for a write refused because the window is shut: 409."""
    return render(request, template, {"event": event, "reason": submissions_closed_reason(event)}, status=409)


@login_required
@require_POST
def join_event(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if submissions_closed_reason(event):
        return closed(request, event)
    if Membership.objects.filter(user=request.user, event=event, role=Membership.Role.JUDGE).exists():
        messages.error(request, "You judge this event, so you can't also compete in it.")
        return redirect("home")
    _, created = Membership.objects.get_or_create(user=request.user, event=event, role=Membership.Role.PARTICIPANT)
    if created:
        audit.record("event.join", request=request, event=event, obj=event)
    return redirect("event-me", slug=slug)


@login_required
def event_me(request, slug):
    """A participant's page for one event: their team, invites, project."""
    event = get_object_or_404(Event, slug=slug)
    if not Membership.objects.filter(user=request.user, event=event, role=Membership.Role.PARTICIPANT).exists():
        raise Http404
    member = TeamMember.objects.filter(event=event, user=request.user).select_related("team").first()
    team = member.team if member else None
    return render(request, "portal/participant/event.html", {
        "event": event, "team": team,
        "members": team.members.select_related("user") if team else [],
        "projects": team.projects.all() if team else [],
        "invites": team.invites.filter(used_at__isnull=True, expires_at__gt=db_now()) if team else [],
        "closed": submissions_closed_reason(event),
        "new_link": request.session.pop("new_invite_link", None),
    })


class TeamForm(forms.Form):
    name = forms.CharField(max_length=200)


@login_required
@require_POST
def create_team(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not Membership.objects.filter(user=request.user, event=event, role=Membership.Role.PARTICIPANT).exists():
        raise Http404
    if submissions_closed_reason(event):
        return closed(request, event)
    form = TeamForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A team needs a name.")
        return redirect("event-me", slug=slug)
    try:
        with guarded():
            team = Team.objects.create(event=event, name=form.cleaned_data["name"])
            TeamMember.objects.create(team=team, event=event, user=request.user)
    except APIException as exc:
        messages.error(request, str(exc.detail))
        return redirect("event-me", slug=slug)
    except IntegrityError:
        messages.error(request, "You're already on a team in this event.")
        return redirect("event-me", slug=slug)
    audit.record("team.create", request=request, event=event, obj=team, after={"name": team.name})
    return redirect("event-me", slug=slug)


def _my_team(request, pk):
    member = TeamMember.objects.filter(team_id=pk, user=request.user).select_related("team__event").first()
    if member is None:
        raise Http404
    return member.team


@login_required
@require_POST
def create_invite(request, pk):
    """A single-use link, valid for 72 hours or until submissions close.
    Shown once to its creator; only its hash is stored."""
    team = _my_team(request, pk)
    if submissions_closed_reason(team.event):
        return closed(request, team.event)
    token = secrets.token_urlsafe(24)
    expires = min(db_now() + INVITE_LIFETIME, team.event.submissions_close)
    invite = TeamInvite.objects.create(team=team, token_hash=_hash(token), created_by=request.user, expires_at=expires)
    audit.record("team.invite", request=request, event=team.event, obj=invite, after={"team": team.pk, "expires_at": expires})
    request.session["new_invite_link"] = request.build_absolute_uri(f"/invite/{token}")
    return redirect("event-me", slug=team.event.slug)


@login_required
@require_POST
def revoke_invite(request, pk, invite):
    team = _my_team(request, pk)
    invite = get_object_or_404(team.invites, pk=invite, used_at__isnull=True)
    invite.expires_at = db_now()
    invite.save(update_fields=["expires_at"])
    audit.record("team.invite_revoke", request=request, event=team.event, obj=invite)
    return redirect("event-me", slug=team.event.slug)


def accept_invite(request, token):
    invite = (TeamInvite.objects.select_related("team__event")
              .filter(token_hash=_hash(token), used_at__isnull=True, expires_at__gt=db_now()).first())
    if invite is None:
        return render(request, "portal/participant/invite.html", {"invalid": True}, status=404)
    team, event = invite.team, invite.team.event
    if not request.user.is_authenticated:
        return render(request, "portal/participant/invite.html", {"team": team, "event": event, "anonymous": True})
    if request.method != "POST":
        return render(request, "portal/participant/invite.html", {"team": team, "event": event})
    if submissions_closed_reason(event):
        return closed(request, event)
    try:
        with guarded():
            # Lock the invite so two people can't both use it.
            invite = TeamInvite.objects.select_for_update().get(pk=invite.pk, used_at__isnull=True)
            Membership.objects.get_or_create(user=request.user, event=event, role=Membership.Role.PARTICIPANT)
            TeamMember.objects.create(team=team, event=event, user=request.user)
            invite.used_by, invite.used_at = request.user, db_now()
            invite.save(update_fields=["used_by", "used_at"])
    except TeamInvite.DoesNotExist:
        raise Http404
    except APIException as exc:  # a trigger refused: the team is full, or you judge this event
        return render(request, "portal/participant/invite.html",
                      {"team": team, "event": event, "error": exc.detail}, status=409)
    except IntegrityError:
        return render(request, "portal/participant/invite.html",
                      {"team": team, "event": event, "error": "You're already on a team in this event."}, status=409)
    audit.record("team.join", request=request, event=event, obj=team, after={"invite": invite.pk})
    messages.success(request, f"You joined {team.name}.")
    return redirect("event-me", slug=event.slug)


class ProjectForm(forms.ModelForm):
    tags = forms.CharField(required=False, help_text="comma separated")
    # A URL typed without a scheme means https, as it will by default in Django 6.
    repo_url = forms.URLField(max_length=500, required=False, assume_scheme="https")
    demo_url = forms.URLField(max_length=500, required=False, assume_scheme="https")

    class Meta:
        model = Project
        fields = ["title", "tagline", "track", "summary", "description", "repo_url", "demo_url"]
        widgets = {"summary": forms.Textarea(attrs={"rows": 3}), "description": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["track"].queryset = event.tracks.all()
        self.initial.setdefault("tags", ", ".join(self.instance.tags))

    def clean_tags(self):
        tags = [t.strip().lower()[:40] for t in self.cleaned_data["tags"].split(",") if t.strip()]
        return list(dict.fromkeys(tags))[:10]


@login_required
def project_form(request, slug, pk=None):
    """Create or edit your team's project. Every save re-checks the deadline;
    after it, the answer is 409 and the trigger refuses it anyway."""
    event = get_object_or_404(Event, slug=slug)
    member = TeamMember.objects.filter(event=event, user=request.user).select_related("team").first()
    if member is None:
        raise Http404
    project = get_object_or_404(member.team.projects, pk=pk) if pk else Project(event=event, team=member.team)
    form = ProjectForm(request.POST or None, instance=project, event=event)
    if request.method == "POST":
        if submissions_closed_reason(event):
            return closed(request, event)
        if form.is_valid():
            before = None if project.pk is None else audit.snapshot(Project.objects.get(pk=project.pk), PROJECT_FIELDS)
            submit = "submit" in request.POST
            try:
                with guarded():
                    project = form.save(commit=False)
                    project.tags = form.cleaned_data["tags"]
                    if submit and project.status == Project.Status.DRAFT:
                        project.status, project.submitted_at = Project.Status.SUBMITTED, db_now()
                    project.save()
            except APIException as exc:
                if exc.status_code == 409:
                    return closed(request, event)
                form.add_error(None, str(exc.detail))
                return render(request, "portal/participant/project_form.html",
                              {"event": event, "form": form, "project": project}, status=422)
            action = "project.submit" if submit else ("project.update" if before else "project.create")
            audit.record(action, request=request, event=event, obj=project, before=before,
                         after=audit.snapshot(project, PROJECT_FIELDS))
            if submit:
                for dup, original in find_duplicates(event):
                    if dup.duplicate_of_id is None:
                        dup.duplicate_of = original
                        dup.save(update_fields=["duplicate_of", "updated_at"])
                        audit.record("project.flag_duplicate", request=request, event=event, obj=dup,
                                     after={"duplicate_of": original.pk})
            messages.success(request, "Submitted." if submit else "Saved as a draft.")
            return redirect("event-me", slug=slug)
    return render(request, "portal/participant/project_form.html", {
        "event": event, "form": form, "project": project, "closed": submissions_closed_reason(event),
    })
