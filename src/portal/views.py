"""Public pages and the signed-in home page."""
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from . import audit
from .access import visible_projects
from .models import Event, Membership, Project, TeamMember, Track, User


def gallery(request):
    """Public: submitted projects only, searchable and filterable. Page one
    is ordered by id, so the fixture's first projects lead."""
    projects = Project.objects.filter(status=Project.Status.SUBMITTED).select_related("event", "team", "track")
    get = request.GET
    q = get.get("q", "").strip().replace("\x00", "")
    if q:
        projects = projects.filter(Q(title__icontains=q) | Q(tagline__icontains=q) | Q(summary__icontains=q)
                                   | Q(team__name__icontains=q) | Q(tags__icontains=q))
    event = Event.objects.filter(slug=get.get("event", "")).first()
    if event:
        projects = projects.filter(event=event)
    track = get.get("track", "")
    if track:
        projects = projects.filter(track_id=track) if track.isascii() and track.isdigit() else projects.none()
    if tag := get.get("tag", "").replace("\x00", ""):
        projects = projects.filter(tags__contains=[tag])
    page = Paginator(projects.order_by("pk"), 60).get_page(get.get("page"))
    query = get.copy()
    query.pop("page", None)
    return render(request, "portal/gallery.html", {
        "page": page, "q": q, "event": event, "track": track, "query": query.urlencode(),
        "events": Event.objects.order_by("-submissions_close"),
        "tracks": Track.objects.filter(event=event) if event else Track.objects.none(),
    })


def project_page(request, pk):
    project = get_object_or_404(visible_projects(request.user).select_related("event", "team", "track"), pk=pk)
    members = project.team.members.select_related("user")
    return render(request, "portal/project.html", {"project": project, "members": members})


class SignupForm(forms.Form):
    name = forms.CharField(max_length=200)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=10)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("an account with this email already exists")
        return email


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["password"],
                                        name=form.cleaned_data["name"])
        audit.record("user.signup", request=request, actor=user, obj=user, after={"email": user.email})
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome. Join an event below, or open an invite link from your team.")
        target = request.GET.get("next", "")
        # Only follow `next` to a page on this site, never to another host.
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
            target = "home"
        return redirect(target)
    return render(request, "registration/signup.html", {"form": form})


@login_required
def home(request):
    memberships = (Membership.objects.filter(user=request.user).select_related("event")
                   .order_by("-event__submissions_close", "role"))
    teams = {m.event_id: m.team for m in TeamMember.objects.filter(user=request.user).select_related("team")}
    joinable = Event.objects.exclude(memberships__user=request.user).order_by("-submissions_close")
    return render(request, "portal/home.html", {
        "memberships": memberships, "teams": teams, "joinable": joinable,
        "can_create": request.user.is_staff or memberships.filter(role=Membership.Role.ORGANIZER).exists(),
    })
