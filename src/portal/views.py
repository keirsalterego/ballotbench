"""Public pages and the signed-in home page."""
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import audit, ratelimit
from .access import db_now, is_organizer, visible_projects
from .comments import visible_comments
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
    return render(request, "portal/project.html", {
        "project": project, "members": members, "comments": visible_comments(request.user, project),
        "can_moderate": is_organizer(request.user, project.event),
    })


class SignupForm(forms.Form):
    name = forms.CharField(max_length=200)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=10)

    def clean_password(self):
        # The same rules as the rest of Django: length and the common-password list.
        password = self.cleaned_data["password"]
        validate_password(password, User(email=self.data.get("email", ""), name=self.data.get("name", "")))
        return password

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("an account with this email already exists")
        return email


LOGIN_LIMIT = (20, 600)       # attempts per address per 10 minutes
SIGNUP_LIMIT = (10, 3600)     # new accounts per address per hour


def login_page(request):
    """Django's login page, with a cap on attempts per address so a password
    can't be guessed at network speed."""
    if request.method == "POST" and not ratelimit.allow(ratelimit.ip_key(request, "login"), *LOGIN_LIMIT):
        return ratelimit.refused(request)
    return auth_views.LoginView.as_view()(request)


RESET_LIMIT = (5, 3600)     # reset emails per address per hour
CONFIRM_LIMIT = (5, 3600)   # confirmation emails per account per hour
CONFIRM_MAX_AGE = 7 * 24 * 3600


def confirmation_token(user):
    # Signed, not stored: it names the account and the address it was sent
    # to, so changing the address makes old links stop working.
    return signing.TimestampSigner(salt="email-confirm").sign(f"{user.pk}:{user.email}")


def send_confirmation(request, user):
    link = request.build_absolute_uri(f"/confirm-email/{confirmation_token(user)}")
    send_mail("Confirm your ballotbench address",
              f"Open this link to confirm that {user.email} is yours:\n\n{link}\n\n"
              "If you didn't sign up, ignore this message.", None, [user.email])


@login_required
@require_POST
def resend_confirmation(request):
    if not ratelimit.allow(f"confirm:{request.user.pk}", *CONFIRM_LIMIT):
        return ratelimit.refused(request)
    send_confirmation(request, request.user)
    messages.success(request, f"A confirmation link is on its way to {request.user.email}.")
    return redirect(request.POST.get("next") if url_has_allowed_host_and_scheme(
        request.POST.get("next", ""), allowed_hosts={request.get_host()}) else "home")


def confirm_email(request, token):
    """GET shows a button and POST confirms, so a mail scanner that opens
    every link doesn't confirm on the person's behalf."""
    try:
        pk, email = signing.TimestampSigner(salt="email-confirm").unsign(token, max_age=CONFIRM_MAX_AGE).split(":", 1)
        user = User.objects.get(pk=pk, email=email)
    except (signing.BadSignature, ValueError, User.DoesNotExist):
        return render(request, "registration/confirm_email.html", {"invalid": True}, status=404)
    if request.method == "POST" and user.email_confirmed_at is None:
        user.email_confirmed_at = db_now()
        user.save(update_fields=["email_confirmed_at"])
        audit.record("user.email_confirmed", request=request, actor=user, obj=user, after={"email": user.email})
        messages.success(request, "Address confirmed.")
        return redirect("home")
    return render(request, "registration/confirm_email.html", {"email": user.email,
                                                                "done": user.email_confirmed_at is not None})


def password_reset(request):
    """Django's reset flow, rate limited, with mail going wherever mail goes
    (the outbox offline). It's also the way back for someone whose address was
    signed up by somebody else: the link goes to the inbox, not the squatter."""
    if request.method == "POST" and not ratelimit.allow(ratelimit.ip_key(request, "reset"), *RESET_LIMIT):
        return ratelimit.refused(request)
    return auth_views.PasswordResetView.as_view()(request)


class ResetConfirm(auth_views.PasswordResetConfirmView):
    def form_valid(self, form):
        response = super().form_valid(form)
        if form.user.email_confirmed_at is None:        # the link came to this inbox
            form.user.email_confirmed_at = db_now()
            form.user.save(update_fields=["email_confirmed_at"])
        audit.record("user.password_reset", request=self.request, actor=form.user, obj=form.user)
        return response


def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == "POST" and not ratelimit.allow(ratelimit.ip_key(request, "signup"), *SIGNUP_LIMIT):
        return ratelimit.refused(request)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["password"],
                                        name=form.cleaned_data["name"])
        audit.record("user.signup", request=request, actor=user, obj=user, after={"email": user.email})
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        send_confirmation(request, user)
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
    now = db_now()
    # Only events still taking (or yet to take) submissions: joining a closed one leads nowhere.
    joinable = (Event.objects.exclude(memberships__user=request.user).filter(submissions_close__gt=now)
                .order_by("submissions_close"))
    return render(request, "portal/home.html", {
        "memberships": memberships, "teams": teams, "joinable": joinable, "now": now,
        "can_create": request.user.is_staff or memberships.filter(role=Membership.Role.ORGANIZER).exists(),
    })
