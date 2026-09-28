"""Community voting: a quadratic ballot for the public, its projects in a
random order that is stable for each voter, and tallies that stay hidden
until the results are published.

Who may vote is Event.voting_mode: `account` is any signed-in account,
`email` is anyone who confirms an address through a mailed link (one ballot
per inbox, after normalize_email()), `off` is nobody. The rules that matter
(the window, the budget, own team, which projects are eligible) are checked
here first so a refusal can say why, and again by the vote_rules trigger
(migration 0012) so that no code path can skip them."""
import hashlib
import hmac
import json
import math
import random
import secrets
from collections import defaultdict
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.mail import send_mail
from django.db.models import Count, Min, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import exceptions, serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import audit, ratelimit
from .access import Conflict, Invalid, db_now, guarded
from .audit import client_ip
from .models import AuditLog, Event, Project, TeamMember, Vote, Voter
from .organizer import organizer_required

Mode = Event.VotingMode
BALLOT_IP_LIMIT = (30, 3600)
BALLOT_VOTER_LIMIT = (20, 600)
LINK_IP_LIMIT = (5, 3600)
LINK_EMAIL_LIMIT = (3, 3600)
# How many voters on one network, or with one identical ballot, before the
# organizer is asked to look. Flags are for a person to read; nothing is
# voided automatically.
CLUSTER_SIZE = 3
NEW_ACCOUNT = timedelta(hours=1)
GMAIL = {"gmail.com", "googlemail.com"}


def normalize_email(address):
    """One spelling per inbox, so that one inbox gets one ballot. Lowercase,
    and drop a +tag, which nearly every provider delivers to the plain
    address; Gmail also ignores dots and answers to googlemail.com too.
    It never raises: it also runs over team members' account emails, and one
    odd address mustn't break every ballot. Something it can't take apart,
    like +x@example.com, stays as it is, lowercased."""
    lowered = address.strip().lower()
    local, _, domain = lowered.rpartition("@")
    local = local.split("+", 1)[0]
    if domain in GMAIL:
        local, domain = local.replace(".", ""), "gmail.com"
    if not local or not domain:
        return lowered
    return f"{local}@{domain}"


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def voting_closed_reason(event):
    """None while `event` takes votes, else why not, on the database's clock
    (the one the trigger uses)."""
    if event.voting_mode == Mode.OFF:
        return f"{event.name} has no community vote."
    if event.voting_open is None or event.voting_close is None:
        return f"Voting for {event.name} has no dates yet."
    now = db_now()
    if now < event.voting_open:
        return f"Voting for {event.name} opens at {event.voting_open:%Y-%m-%d %H:%M} UTC."
    if now >= event.voting_close:
        return f"Voting for {event.name} closed at {event.voting_close:%Y-%m-%d %H:%M} UTC."
    return None


def voting_is_open(event):
    return voting_closed_reason(event) is None


def ballot_projects(event, voter):
    """The projects on `voter`'s ballot, shuffled with a seed fixed for this
    voter and different for every other: no project is always first, and a
    reload doesn't reshuffle the list someone is halfway through."""
    projects = list(Project.objects.filter(event=event, status=Project.Status.SUBMITTED, duplicate_of__isnull=True)
                    .select_related("team", "track").order_by("pk"))
    seed = int.from_bytes(hashlib.sha256(f"{event.pk}:{voter.pk}".encode()).digest()[:8], "big")
    random.Random(seed).shuffle(projects)
    return projects


def own_projects(event, voter):
    """Ids of the projects `voter` may not vote for: their own team's. For an
    account that is team membership, which the trigger checks too. For an
    email voter it is a team member whose address reaches the same inbox,
    which only the app can see."""
    members = TeamMember.objects.filter(event=event).select_related("user")
    if voter.user_id:
        teams = [m.team_id for m in members if m.user_id == voter.user_id]
    else:
        teams = [m.team_id for m in members if normalize_email(m.user.email) == voter.email_normalized]
    return set(Project.objects.filter(event=event, team_id__in=teams).values_list("pk", flat=True))


def ballot_of(voter):
    return {v.project_id: v.votes for v in voter.votes.all()}


def cost(votes):
    return sum(n * n for n in votes.values())


def ballot_hash(votes):
    """A fingerprint of a whole ballot, equal for equal ballots, so the abuse
    panel can see many voters casting the same one. It goes in the audit log
    instead of the choices themselves: every organizer reads that log, and a
    ballot is between the voter and the tally. It is keyed with the server's
    secret because a plain SHA-256 of something as small as a ballot can be
    reversed by trying every ballot."""
    canon = json.dumps(sorted((int(p), int(n)) for p, n in votes.items() if n), separators=(",", ":"))
    return hmac.new(settings.SECRET_KEY.encode(), canon.encode(), hashlib.sha256).hexdigest()


def cast(request, event, voter, votes):
    """Replace `voter`'s ballot with `votes` ({project id: n}). Raises
    Conflict (409) or Invalid (422) with a reason a voter can act on.
    Returns False if the ballot was already exactly this."""
    if reason := voting_closed_reason(event):
        raise Conflict(reason)
    if voter.voided_at:
        raise Conflict("this ballot was voided by the organizers")
    if voter.confirmed_at is None:
        raise Conflict("confirm your email address before voting")
    if any(n < 0 for n in votes.values()):
        raise Invalid("votes can't be negative")
    votes = {p: n for p, n in votes.items() if n}
    eligible = {p.pk for p in ballot_projects(event, voter)}
    if unknown := set(votes) - eligible:
        raise Invalid(f"project {min(unknown)} is not on this ballot")
    if set(votes) & own_projects(event, voter):
        raise Conflict("you can't vote for your own team's project")
    spent = cost(votes)
    if spent > event.vote_credits:
        raise Conflict(f"that ballot costs {spent} credits and you have {event.vote_credits}")
    before = ballot_of(voter)
    if before == votes:
        return False
    with guarded():
        voter.votes.exclude(project_id__in=votes).delete()
        # Lower votes before raising any, so the trigger's running total
        # never passes the budget partway through a legal change.
        for pid, n in sorted(votes.items(), key=lambda item: item[1] - before.get(item[0], 0)):
            Vote.objects.update_or_create(voter=voter, project_id=pid, defaults={"votes": n})
    audit.record("vote.cast", request=request, event=event, obj=voter,
                 after={"voter": voter.pk, "credits": spent, "projects": len(votes), "ballot": ballot_hash(votes)})
    return True


def new_voter(request, event, **fields):
    voter = Voter.objects.create(event=event, ip=client_ip(request),
                                 user_agent=request.META.get("HTTP_USER_AGENT", "")[:300], **fields)
    audit.record("vote.signup", request=request, event=event, obj=voter, after={"voter": voter.pk})
    return voter


def session_key(event):
    return f"voter-{event.pk}"


def current_voter(request, event):
    """Who this request votes as, or None. An account votes as itself (its
    voter row is made on its first visit); an email voter as whoever
    confirmed a link in this browser."""
    if event.voting_mode == Mode.ACCOUNT and request.user.is_authenticated:
        voter = Voter.objects.filter(event=event, user=request.user).first()
        return voter or new_voter(request, event, user=request.user, confirmed_at=db_now())
    if event.voting_mode == Mode.EMAIL and (pk := request.session.get(session_key(event))):
        return Voter.objects.filter(event=event, pk=pk).first()
    return None


def rate_rules(request, voter):
    return [(ratelimit.ip_key(request, "ballot"), *BALLOT_IP_LIMIT), (f"ballot-voter:{voter.pk}", *BALLOT_VOTER_LIMIT)]


# The ballot page.

def ballot_rows(event, voter, chosen):
    own = own_projects(event, voter)
    return [{"project": p, "votes": chosen.get(p.pk, 0), "own": p.pk in own} for p in ballot_projects(event, voter)]


def ballot(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.voting_mode == Mode.OFF:
        return render(request, "portal/voting/off.html", {"event": event}, status=404)
    if (event.voting_mode == Mode.ACCOUNT and request.user.is_authenticated
            and request.user.email_confirmed_at is None):
        # One ballot per account is only worth something if an account is a
        # real inbox: confirm it first.
        return render(request, "portal/voting/confirm_first.html", {"event": event}, status=403)
    voter = current_voter(request, event)
    if voter is None:
        if event.voting_mode == Mode.ACCOUNT:
            return redirect_to_login(request.get_full_path())
        return render(request, "portal/voting/email.html", {"event": event, "form": EmailForm()})
    closed = voting_closed_reason(event)
    chosen, error, status = ballot_of(voter), None, 200
    if request.method == "POST":
        if closed:
            return render(request, "portal/closed.html", {"event": event, "reason": closed}, status=409)
        try:
            ratelimit.check(*rate_rules(request, voter))
        except exceptions.Throttled:
            return ratelimit.refused(request)
        try:
            chosen = {}
            for row in ballot_rows(event, voter, {}):
                raw = request.POST.get(f"p{row['project'].pk}", "0").strip() or "0"
                if not raw.isascii() or not raw.lstrip("-").isdigit():
                    raise Invalid(f"votes for {row['project'].title} must be a whole number")
                chosen[row["project"].pk] = int(raw)
            changed = cast(request, event, voter, chosen)
            messages.success(request, "Your ballot is saved. You can change it until voting closes."
                             if changed else "Nothing changed.")
            return redirect("ballot", slug=event.slug)
        except exceptions.APIException as exc:
            error, status = str(exc.detail), exc.status_code
    rows = ballot_rows(event, voter, chosen)
    top = math.isqrt(event.vote_credits)
    spent = cost(chosen)
    return render(request, "portal/voting/ballot.html", {
        "event": event, "voter": voter, "rows": rows, "closed": closed, "error": error,
        "budget": event.vote_credits, "spent": spent, "remaining": event.vote_credits - spent,
        "choices": range(top + 1) if top <= 10 else None, "top": top,
    }, status=status)


class EmailForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Your email address")

    def clean_email(self):
        """A quoted local part ("a.b"@gmail.com) is legal but reaches an
        inbox normalize_email can't see through, so it could be a second
        ballot. Nobody needs one to vote."""
        email = self.cleaned_data["email"].strip()
        if email.startswith('"'):
            raise forms.ValidationError("Use the address without quotation marks.")
        return email


@require_POST
def request_link(request, slug):
    """Mail a one-time link that opens this event's ballot. Every spelling of
    an inbox leads to one voter: a second spelling gets no second ballot
    (it is logged as vote.duplicate_refused). The link goes to the address
    just typed, not the one that signed up first: if a provider treats a
    +tag as a different inbox, whoever typed that spelling first must not
    get every later link. The link replaces the last one, so only the newest
    request can open the ballot. The page says the same thing either way, so
    it can't be used to find out who voted."""
    event = get_object_or_404(Event, slug=slug, voting_mode=Mode.EMAIL)
    if reason := voting_closed_reason(event):
        return render(request, "portal/closed.html", {"event": event, "reason": reason}, status=409)
    form = EmailForm(request.POST)
    if not form.is_valid():
        return render(request, "portal/voting/email.html", {"event": event, "form": form}, status=422)
    email = form.cleaned_data["email"]
    normalized = normalize_email(email)
    try:
        ratelimit.check((ratelimit.ip_key(request, "vote-link"), *LINK_IP_LIMIT),
                        (f"vote-link:{event.pk}:{normalized}", *LINK_EMAIL_LIMIT))
    except exceptions.Throttled:
        return ratelimit.refused(request)
    token = secrets.token_urlsafe(32)
    voter = Voter.objects.filter(event=event, email_normalized=normalized).first()
    if voter is None:
        voter = new_voter(request, event, email=email, email_normalized=normalized, token_hash=_hash(token))
    else:
        if voter.email.lower() != email.lower():
            audit.record("vote.duplicate_refused", request=request, event=event, obj=voter,
                         after={"voter": voter.pk})
        # A fresh link replaces the old one, which stops working.
        voter.token_hash = _hash(token)
        voter.save(update_fields=["token_hash"])
    link = request.build_absolute_uri(reverse("vote-confirm", args=[token]))
    send_mail(f"Your ballot for {event.name}",
              f"Open this link to vote in {event.name}:\n\n{link}\n\n"
              "It works once, in the browser you open it in. If you didn't ask for it, ignore this email.",
              None, [email])
    return render(request, "portal/voting/sent.html", {"event": event})


def confirm(request, token):
    """The mailed link. Opening it shows a button; the POST behind the button
    confirms. Mail scanners fetch every link in a message, and a GET that
    confirmed would be used up by the scanner before the voter clicked."""
    voter = Voter.objects.select_related("event").filter(token_hash=_hash(token)).first()
    if voter is None:
        return render(request, "portal/voting/confirm.html", {"invalid": True}, status=404)
    event = voter.event
    if request.method != "POST":
        return render(request, "portal/voting/confirm.html", {"voter": voter, "event": event})
    first = voter.confirmed_at is None
    voter.token_hash = None               # single use
    voter.confirmed_at = voter.confirmed_at or db_now()
    voter.save(update_fields=["token_hash", "confirmed_at"])
    request.session.cycle_key()
    request.session[session_key(event)] = voter.pk
    if first:
        audit.record("vote.confirm", request=request, event=event, obj=voter, after={"voter": voter.pk})
    return redirect("ballot", slug=event.slug)


# The ballot API, for account-mode events.

BallotOut = inline_serializer("Ballot", {
    "event": serializers.CharField(), "open": serializers.BooleanField(),
    "closed_reason": serializers.CharField(allow_null=True), "budget": serializers.IntegerField(),
    "spent": serializers.IntegerField(), "remaining": serializers.IntegerField(),
    "projects": inline_serializer("BallotProject", {
        "id": serializers.IntegerField(), "title": serializers.CharField(), "team": serializers.CharField(),
        "votes": serializers.IntegerField(), "cost": serializers.IntegerField(),
        "own_team": serializers.BooleanField()}, many=True)})


class BallotIn(serializers.Serializer):
    votes = serializers.DictField(child=serializers.IntegerField(min_value=0, max_value=1000),
                                  help_text="project id to number of votes. This is the whole ballot: "
                                            "projects left out get none.")


def ballot_data(event, voter):
    chosen = ballot_of(voter)
    closed = voting_closed_reason(event)
    return {"event": event.slug, "open": closed is None, "closed_reason": closed, "budget": event.vote_credits,
            "spent": cost(chosen), "remaining": event.vote_credits - cost(chosen),
            "projects": [{"id": r["project"].pk, "title": r["project"].title, "team": r["project"].team.name,
                          "votes": r["votes"], "cost": r["votes"] ** 2, "own_team": r["own"]}
                         for r in ballot_rows(event, voter, chosen)]}


@extend_schema(methods=["GET"], responses=BallotOut,
               description="Your ballot in an event that votes by account: the projects in your own random "
                           "order, your votes, and your budget. 404 if the event has no vote, 409 if it votes "
                           "by email link instead.")
@extend_schema(methods=["POST"], request=BallotIn, responses={200: BallotOut},
               description="Replace your whole ballot. n votes cost n² credits. 409 outside the voting window, "
                           "over budget or for your own team's project; 422 for a project not on the ballot; "
                           "429 when rate limited.")
@api_view(["GET", "POST"])
def api_ballot(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if event.voting_mode == Mode.OFF:
        raise exceptions.NotFound("this event has no community vote")
    if event.voting_mode == Mode.EMAIL:
        raise Conflict("this event votes by email link: use the ballot page")
    if request.user.is_authenticated and request.user.email_confirmed_at is None:
        raise exceptions.PermissionDenied("confirm your email address before voting")
    voter = current_voter(request, event)
    if request.method == "POST":
        # Refusals are returned, not raised: DRF rolls back the request's
        # transaction on a raised error, and with it this request's
        # rate-limit hit, which would let failed requests go uncounted.
        try:
            ratelimit.check(*rate_rules(request, voter))
            body = BallotIn(data=request.data)
            body.is_valid(raise_exception=True)
            votes = {}
            for key, n in body.validated_data["votes"].items():
                if not str(key).isascii() or not str(key).isdigit():
                    raise Invalid(f"{key!r} is not a project id")
                votes[int(key)] = n
            cast(request, event, voter, votes)
        except exceptions.APIException as exc:
            return Response(exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}, status=exc.status_code)
    return Response(ballot_data(event, voter))


# Tallies, and who may see them.

def tallies(event):
    """{project id: (votes, voters)} over ballots that aren't voided."""
    rows = (Vote.objects.filter(voter__event=event, voter__voided_at__isnull=True)
            .values("project").annotate(total=Sum("votes"), voters=Count("voter")))
    return {r["project"]: (r["total"], r["voters"]) for r in rows}


def counted_voters(event):
    return Voter.objects.filter(event=event, voided_at__isnull=True, votes__isnull=False).distinct().count()


def public_tallies(event):
    """The tallies, if the public may see them: once results are published,
    and never while voting is open."""
    if event.voting_mode == Mode.OFF or not event.results_published_at or voting_is_open(event):
        return None
    return tallies(event)


# The organizer's page: tallies, the abuse panel, voiding.

def abuse_report(event):
    """Every voter, with the flags a person should look at: a network with
    several voters on it, an account made just before its ballot, and
    ballots cast identically by several voters. Flags are never acted on
    automatically; plenty of honest people share an office network. Only
    voters who cast a ballot are listed: opening the ballot page makes a
    voter row, and a refused first ballot leaves one with nothing in it."""
    voters = list(Voter.objects.filter(event=event, votes__isnull=False).distinct()
                  .select_related("user").prefetch_related("votes").order_by("pk"))
    first_cast = dict(AuditLog.objects.filter(event=event, action="vote.cast", object_type="voter")
                      .values("object_id").annotate(first=Min("ts")).values_list("object_id", "first"))
    networks, ballots = defaultdict(list), defaultdict(list)
    for v in voters:
        v.ballot = ballot_of(v)
        v.spent = cost(v.ballot)
        v.network = ratelimit.network(v.ip, v4_bits=24)
        v.flags = []
        networks[v.network].append(v)
        if v.ballot:
            ballots[ballot_hash(v.ballot)].append(v)
        cast_at = first_cast.get(str(v.pk))
        if v.user and cast_at and cast_at - v.user.date_joined < NEW_ACCOUNT:
            v.flags.append("new account")
    clusters = sorted(((net, vs) for net, vs in networks.items() if net != "unknown" and len(vs) >= CLUSTER_SIZE),
                      key=lambda item: -len(item[1]))
    identical = sorted((vs for vs in ballots.values() if len(vs) >= CLUSTER_SIZE), key=len, reverse=True)
    for _, vs in clusters:
        for v in vs:
            v.flags.append("cluster")
    for vs in identical:
        for v in vs:
            v.flags.append("identical ballots")
    return {"voters": sorted(voters, key=lambda v: (not v.flags, v.pk)), "clusters": clusters,
            "identical": identical, "new_accounts": sum("new account" in v.flags for v in voters),
            "duplicates": AuditLog.objects.filter(event=event, action="vote.duplicate_refused").count(),
            "opened": Voter.objects.filter(event=event, votes__isnull=True).count()}


@organizer_required
def voting_page(request, event):
    """While voting is open the page shows how many ballots there are, not
    the tallies: with the tallies an organizer could void a ballot and read
    what it held from the difference."""
    rows = None
    if not voting_is_open(event):
        counts = tallies(event)
        projects = (Project.objects.filter(event=event, status=Project.Status.SUBMITTED, duplicate_of__isnull=True)
                    .select_related("team"))
        rows = sorted(({"project": p, "votes": counts.get(p.pk, (0, 0))[0], "voters": counts.get(p.pk, (0, 0))[1]}
                       for p in projects), key=lambda r: (-r["votes"], r["project"].title))
    return render(request, "portal/organizer/voting.html", {
        "event": event, "rows": rows, "counted": counted_voters(event), "closed": voting_closed_reason(event),
        "voided": event.voters.filter(voided_at__isnull=False).count(), "cluster_size": CLUSTER_SIZE,
        **abuse_report(event),
    })


@organizer_required
@require_POST
def void_voter(request, event, pk):
    """Take a ballot out of the tallies, with a reason. It is final: a void
    that could be undone would let an organizer read one voter's ballot by
    comparing the tallies with and without it."""
    voter = get_object_or_404(event.voters, pk=pk, voided_at__isnull=True)
    reason = request.POST.get("reason", "").strip()[:300]
    if not reason:
        messages.error(request, "Say why you're voiding this ballot, for the audit log.")
        return redirect("voting-manage", slug=event.slug)
    voter.voided_at, voter.voided_reason = db_now(), reason
    voter.save(update_fields=["voided_at", "voided_reason"])
    audit.record("vote.void", request=request, event=event, obj=voter,
                 before={"voided_at": None, "voided_reason": ""},
                 after={"voided_at": voter.voided_at, "voided_reason": voter.voided_reason})
    messages.success(request, f"Ballot {voter.pk} is out of the tallies for good.")
    return redirect("voting-manage", slug=event.slug)
