"""Community voting: who may vote, what a ballot may hold, when, and who sees
the tallies. Most rules are tested twice: through the app, which explains a
refusal, and straight against the database, whose trigger has the last word."""
import re
from datetime import timedelta

import pytest
from django.core.mail import EmailMessage
from django.db import DatabaseError, IntegrityError, transaction

from portal import voting
from portal.access import db_now
from portal.mail import OutboxBackend
from portal.models import AuditLog, Event, OutboundEmail, Project, Team, TeamMember, User, Vote, Voter
from portal.organizer import EventForm

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db
DEMO = "demo-open"


def open_voting(event, mode="account", credits=25):
    now = db_now()
    Event.objects.filter(pk=event.pk).update(voting_mode=mode, vote_credits=credits,
                                             voting_open=now - timedelta(hours=1), voting_close=now + timedelta(hours=1))
    event.refresh_from_db()
    return event


def close_voting(event):
    now = db_now()
    Event.objects.filter(pk=event.pk).update(voting_open=now - timedelta(hours=2),
                                             voting_close=now - timedelta(minutes=1))
    event.refresh_from_db()


@pytest.fixture
def ballot():
    """demo-open with an open account vote and four submitted projects. The
    demo participant is on the team of the first one."""
    event = open_voting(Event.objects.get(slug=DEMO))
    participant = User.objects.get(email=EMAILS["participant"])
    projects = []
    for i in range(4):
        team = Team.objects.create(event=event, name=f"Voting team {i}")
        member = participant if i == 0 else User.objects.create_user(f"member{i}@example.org", "pw-for-tests-only")
        TeamMember.objects.create(team=team, event=event, user=member)
        projects.append(Project.objects.create(event=event, team=team, title=f"Ballot project {i}",
                                               status="submitted", submitted_at=db_now()))
    return event, projects


def voter_for(event, email):
    user = User.objects.create_user(email, "pw-for-tests-only")
    return Voter.objects.create(event=event, user=user, confirmed_at=db_now()), user


def signed_in(web, user):
    client = web()
    client.force_login(user)
    return client


def refused(match=None):
    return pytest.raises((DatabaseError, IntegrityError), match=match)


# normalize_email

@pytest.mark.parametrize("typed, inbox", [
    ("ab@gmail.com", "ab@gmail.com"),
    ("A.B@Gmail.com", "ab@gmail.com"),
    ("a.b+hack@googlemail.com", "ab@gmail.com"),
    ("  Ada.Lovelace+x+y@GOOGLEMAIL.COM ", "adalovelace@gmail.com"),
    ("bob+votes@example.org", "bob@example.org"),
    ("b.o.b@example.org", "b.o.b@example.org"),
    ("Bob@Example.ORG", "bob@example.org"),
])
def test_normalize_email(typed, inbox):
    assert voting.normalize_email(typed) == inbox


@pytest.mark.parametrize("typed", ["+tag@example.org", "nobody", "@example.org", "user@"])
def test_normalize_email_refuses_non_addresses(typed):
    with pytest.raises(ValueError):
        voting.normalize_email(typed)


# The ballot

def test_ballot_order_is_stable_per_voter_and_differs_between_voters(ballot):
    event, projects = ballot
    for i in range(8):
        team = Team.objects.create(event=event, name=f"More {i}")
        Project.objects.create(event=event, team=team, title=f"More {i}", status="submitted", submitted_at=db_now())
    voters = [voter_for(event, f"order{i}@example.org")[0] for i in range(3)]
    orders = [[p.pk for p in voting.ballot_projects(event, v)] for v in voters]
    assert orders == [[p.pk for p in voting.ballot_projects(event, v)] for v in voters], "stable on reload"
    assert len(set(map(tuple, orders))) > 1, "different voters get different orders"
    assert all(o != sorted(o) for o in orders), "not by id"
    titles = [[p.title for p in voting.ballot_projects(event, v)] for v in voters]
    assert all(t != sorted(t) for t in titles), "not alphabetical"


def test_ballot_page_shows_the_voters_own_order(web, ballot):
    event, _ = ballot
    client = web("participant")
    page = client.get(f"/events/{DEMO}/vote").content.decode()
    voter = Voter.objects.get(event=event, user__email=EMAILS["participant"])
    expected = [p.title for p in voting.ballot_projects(event, voter)]
    assert re.findall(r'<legend><a href="/projects/\d+">([^<]+)</a>', page) == expected
    assert "so you can't vote for it" in page


def test_account_vote_is_saved_and_audited_without_the_choices(web, ballot):
    event, projects = ballot
    client = web("participant")
    response = client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "3", f"p{projects[2].pk}": "4"})
    assert response.status_code == 302
    voter = Voter.objects.get(event=event, user__email=EMAILS["participant"])
    assert voting.ballot_of(voter) == {projects[1].pk: 3, projects[2].pk: 4}
    row = AuditLog.objects.filter(action="vote.cast", event=event).order_by("-seq").first()
    assert row.after["voter"] == voter.pk and row.after["credits"] == 25 and row.after["projects"] == 2
    assert row.after["ballot"] == voting.ballot_hash({projects[1].pk: 3, projects[2].pk: 4})
    assert len(row.after) == 4, "the log has the fingerprint, not who got how many votes"


def test_a_ballot_can_move_credits_between_projects(web, ballot):
    event, projects = ballot
    client = web("participant")
    client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "5"})
    assert client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "3", f"p{projects[2].pk}": "4"}).status_code == 302
    voter = Voter.objects.get(event=event, user__email=EMAILS["participant"])
    assert voting.ballot_of(voter) == {projects[1].pk: 3, projects[2].pk: 4}
    client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "0", f"p{projects[2].pk}": "0"})
    assert not voter.votes.exists()


def test_over_budget_is_refused_by_the_page_and_the_api(web, api, ballot):
    event, projects = ballot
    response = web("participant").post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "5", f"p{projects[2].pk}": "1"})
    assert response.status_code == 409 and "costs 26 credits" in response.content.decode()
    response = api("participant").post(f"/api/events/{DEMO}/ballot", {"votes": {projects[1].pk: 6}},
                                       content_type="application/json")
    assert response.status_code == 409 and "costs 36" in response.json()["detail"]
    assert not Vote.objects.filter(voter__event=event).exists()


def test_over_budget_is_refused_by_the_trigger(ballot):
    event, projects = ballot
    voter, _ = voter_for(event, "greedy@example.org")
    Vote.objects.create(voter=voter, project=projects[1], votes=4)
    Vote.objects.create(voter=voter, project=projects[2], votes=3)
    with refused("credits"), transaction.atomic():
        Vote.objects.create(voter=voter, project=projects[3], votes=1)
    with refused("credits"), transaction.atomic():
        Vote.objects.filter(voter=voter, project=projects[2]).update(votes=4)


def test_closed_window_is_409_on_the_page_and_the_api_and_refused_by_the_trigger(web, api, ballot):
    event, projects = ballot
    voter, _ = voter_for(event, "late@example.org")
    close_voting(event)
    response = web("participant").post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "1"})
    assert response.status_code == 409 and "closed" in response.content.decode()
    response = api("participant").post(f"/api/events/{DEMO}/ballot", {"votes": {projects[1].pk: 1}},
                                       content_type="application/json")
    assert response.status_code == 409
    with refused("not open"), transaction.atomic():
        Vote.objects.create(voter=voter, project=projects[1], votes=1)
    assert "closed" in web("participant").get(f"/events/{DEMO}/vote").content.decode()


def test_own_team_is_refused_by_the_app_and_the_trigger(web, api, ballot):
    event, projects = ballot
    own = projects[0]
    response = web("participant").post(f"/events/{DEMO}/vote", {f"p{own.pk}": "1"})
    assert response.status_code == 409 and "own team" in response.content.decode()
    response = api("participant").post(f"/api/events/{DEMO}/ballot", {"votes": {own.pk: 1}},
                                       content_type="application/json")
    assert response.status_code == 409
    voter = Voter.objects.get(event=event, user__email=EMAILS["participant"])
    with refused("own team"), transaction.atomic():
        Vote.objects.create(voter=voter, project=own, votes=1)


def test_a_voter_cant_vote_for_another_events_project(api, ballot):
    event, _ = ballot
    elsewhere = Project.objects.filter(event__slug=EVENT, status="submitted").first()
    voter, _ = voter_for(event, "tourist@example.org")
    with refused("not on this ballot") as caught, transaction.atomic():
        Vote.objects.create(voter=voter, project=elsewhere, votes=1)
    assert caught.value.__cause__.sqlstate == "BB422"
    response = api("participant").post(f"/api/events/{DEMO}/ballot", {"votes": {elsewhere.pk: 1}},
                                       content_type="application/json")
    assert response.status_code == 422


def test_one_ballot_per_account(ballot):
    event, _ = ballot
    _, user = voter_for(event, "twice@example.org")
    with refused(), transaction.atomic():
        Voter.objects.create(event=event, user=user, confirmed_at=db_now())


def test_voting_off_is_404(web, api):
    response = web("participant").get(f"/events/{EVENT}/vote")
    assert response.status_code == 404 and "isn't open" in response.content.decode()
    assert api("participant").get(f"/api/events/{EVENT}/ballot").status_code == 404


def test_account_ballot_needs_a_login(web, api, ballot):
    assert web().get(f"/events/{DEMO}/vote").status_code == 302
    assert api().get(f"/api/events/{DEMO}/ballot").status_code == 401
    assert api().post(f"/api/events/{DEMO}/ballot", {"votes": {}}, content_type="application/json").status_code == 401


def test_api_ballot_round_trip(api, ballot):
    event, projects = ballot
    client = api("participant")
    before = client.get(f"/api/events/{DEMO}/ballot").json()
    assert before["budget"] == 25 and before["remaining"] == 25 and before["open"]
    assert {p["id"] for p in before["projects"]} == {p.pk for p in projects}
    assert [p["own_team"] for p in before["projects"] if p["id"] == projects[0].pk] == [True]
    after = client.post(f"/api/events/{DEMO}/ballot", {"votes": {projects[3].pk: 2, projects[2].pk: 1}},
                        content_type="application/json").json()
    assert after["spent"] == 5 and after["remaining"] == 20
    assert [p["id"] for p in after["projects"]] == [p["id"] for p in before["projects"]], "same order"
    assert client.post(f"/api/events/{DEMO}/ballot", {"votes": {"x": 1}},
                       content_type="application/json").status_code in (400, 422)


def test_email_mode_api_points_to_the_page(api, ballot):
    event, _ = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    assert api("participant").get(f"/api/events/{DEMO}/ballot").status_code == 409


# Voting by email

def link_in(mail):
    return re.search(r"/vote/confirm/\S+", mail.body).group(0)


def test_email_voter_confirms_by_link_then_votes(web, ballot, mailoutbox):
    event, projects = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    client = web()
    assert "Send me a link" in client.get(f"/events/{DEMO}/vote").content.decode()
    assert client.post(f"/events/{DEMO}/vote/link", {"email": "Ada.L+hack@googlemail.com"}).status_code == 200
    assert [m.to for m in mailoutbox] == [["Ada.L+hack@googlemail.com"]]
    voter = Voter.objects.get(event=event)
    assert voter.email_normalized == "adal@gmail.com" and voter.confirmed_at is None
    link = link_in(mailoutbox[0])
    assert voter.token_hash and link.split("/")[-1] not in voter.token_hash, "only the hash is stored"

    assert client.get(link).status_code == 200, "opening the link only shows a button"
    assert Voter.objects.get(pk=voter.pk).confirmed_at is None
    assert client.post(link).status_code == 302
    assert Voter.objects.get(pk=voter.pk).confirmed_at is not None
    assert client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "2"}).status_code == 302
    assert voting.ballot_of(voter) == {projects[1].pk: 2}
    assert web().post(link).status_code == 404, "the link works once"
    assert "Send me a link" in web().get(f"/events/{DEMO}/vote").content.decode(), "another browser isn't the voter"


def test_unconfirmed_email_voter_cant_vote(ballot):
    event, projects = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    event.refresh_from_db()
    voter = Voter.objects.create(event=event, email="new@example.org", email_normalized="new@example.org")
    with pytest.raises(voting.Conflict, match="confirm"):
        voting.cast(None, event, voter, {projects[1].pk: 1})
    with refused("confirm"), transaction.atomic():
        Vote.objects.create(voter=voter, project=projects[1], votes=1)


def test_one_ballot_per_inbox(web, ballot, mailoutbox):
    event, _ = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    web().post(f"/events/{DEMO}/vote/link", {"email": "ab@gmail.com"})
    response = web().post(f"/events/{DEMO}/vote/link", {"email": "a.b+x@googlemail.com"})
    assert response.status_code == 200, "the page reads the same, so it can't tell anyone who voted"
    assert Voter.objects.filter(event=event).count() == 1
    assert [m.to for m in mailoutbox] == [["ab@gmail.com"], ["ab@gmail.com"]], "the link goes to the first spelling"
    assert AuditLog.objects.filter(event=event, action="vote.duplicate_refused").count() == 1
    with refused(), transaction.atomic():
        Voter.objects.create(event=event, email="a.b@gmail.com", email_normalized="ab@gmail.com")


def test_email_voter_cant_vote_for_a_team_on_the_same_inbox(ballot):
    event, projects = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    event.refresh_from_db()
    member = projects[1].team.members.first().user          # member1@example.org
    voter = Voter.objects.create(event=event, email="Member1+v@example.org", email_normalized="member1@example.org",
                                 confirmed_at=db_now())
    assert member.email == "member1@example.org"
    with pytest.raises(voting.Conflict, match="own team"):
        voting.cast(None, event, voter, {projects[1].pk: 1})


def test_link_requests_are_rate_limited(web, ballot, mailoutbox):
    event, _ = ballot
    Event.objects.filter(pk=event.pk).update(voting_mode="email")
    limit, _ = voting.LINK_EMAIL_LIMIT
    for i in range(limit):
        web().post(f"/events/{DEMO}/vote/link", {"email": "spam@example.org"}, REMOTE_ADDR=f"198.51.100.{i}")
    response = web().post(f"/events/{DEMO}/vote/link", {"email": "spam+again@example.org"}, REMOTE_ADDR="192.0.2.9")
    assert response.status_code == 429 and len(mailoutbox) == limit


def test_outbox_backend_keeps_mail_in_the_database():
    OutboxBackend().send_messages([EmailMessage("Your ballot", "a link", "from@x.org", ["voter@example.org"])])
    mail = OutboundEmail.objects.get(to="voter@example.org")
    assert mail.subject == "Your ballot" and mail.body == "a link"


# Tallies and results

def cast_directly(event, votes_by_voter):
    voters = []
    for i, votes in enumerate(votes_by_voter):
        voter, _ = voter_for(event, f"tally{i}@example.org")
        for project, n in votes.items():
            Vote.objects.create(voter=voter, project=project, votes=n)
        voters.append(voter)
    return voters


def test_organizers_see_tallies_and_nobody_else_does(web, ballot):
    event, projects = ballot
    cast_directly(event, [{projects[1]: 3}, {projects[1]: 2, projects[2]: 1}])
    assert voting.tallies(event) == {projects[1].pk: (5, 2), projects[2].pk: (1, 1)}
    page = web("organizer").get(f"/events/{DEMO}/manage/voting")
    assert page.status_code == 200 and page.context["counted"] == 2
    for role in ("participant", "judge_a"):
        assert web(role).get(f"/events/{DEMO}/manage/voting").status_code == 403


def test_voided_ballots_are_out_of_the_tallies(web, ballot):
    event, projects = ballot
    honest, sock = cast_directly(event, [{projects[1]: 3}, {projects[2]: 5}])
    client = web("organizer")
    client.post(f"/events/{DEMO}/manage/voting/voters/{sock.pk}", {"reason": ""})
    assert Voter.objects.get(pk=sock.pk).voided_at is None, "a void needs a reason"
    client.post(f"/events/{DEMO}/manage/voting/voters/{sock.pk}", {"reason": "same /24 as ten others"})
    assert voting.tallies(event) == {projects[1].pk: (3, 1)}
    assert voting.counted_voters(event) == 1
    row = AuditLog.objects.filter(action="vote.void", event=event).first()
    assert row.after["voided_reason"] == "same /24 as ten others" and row.object_id == str(sock.pk)
    with refused("voided"), transaction.atomic():
        Vote.objects.create(voter=sock, project=projects[3], votes=1)
    client.post(f"/events/{DEMO}/manage/voting/voters/{sock.pk}", {"action": "unvoid"})
    assert voting.tallies(event)[projects[2].pk] == (5, 1)
    assert AuditLog.objects.filter(action="vote.unvoid", event=event).exists()
    assert web("participant").post(f"/events/{DEMO}/manage/voting/voters/{honest.pk}",
                                   {"reason": "x"}).status_code == 403


def test_abuse_panel_flags_but_never_voids(web, ballot):
    event, projects = ballot
    same = {projects[1]: 2, projects[2]: 2}
    voters = cast_directly(event, [same, same, same, {projects[3]: 1}])
    Voter.objects.filter(pk__in=[v.pk for v in voters[:3]]).update(ip="203.0.113.5")
    Voter.objects.filter(pk=voters[3].pk).update(ip="203.0.113.77")
    for v in voters:
        voting.audit.record("vote.cast", event=event, obj=v)       # the panel dates a ballot by its first vote.cast
    report = voting.abuse_report(event)
    assert [(net, len(vs)) for net, vs in report["clusters"]] == [("203.0.113.0/24", 4)]
    assert [len(vs) for vs in report["identical"]] == [3]
    assert report["new_accounts"] == 4, "every account here was made just now"
    flagged = {v.pk: v.flags for v in report["voters"]}
    assert flagged[voters[0].pk] == ["new account", "cluster", "identical ballots"]
    assert flagged[voters[3].pk] == ["new account", "cluster"]
    assert not Voter.objects.filter(event=event, voided_at__isnull=False).exists()
    page = web("organizer").get(f"/events/{DEMO}/manage/voting").content.decode()
    assert "203.0.113.0/24" in page and "identical ballots" in page


def test_ballot_posts_are_rate_limited(web, api, ballot, monkeypatch):
    event, projects = ballot
    monkeypatch.setattr(voting, "BALLOT_VOTER_LIMIT", (2, 600))
    client = web("participant")
    for n in (1, 2):
        assert client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": str(n)}).status_code == 302
    assert client.post(f"/events/{DEMO}/vote", {f"p{projects[1].pk}": "3"}).status_code == 429
    response = api("participant").post(f"/api/events/{DEMO}/ballot", {"votes": {projects[1].pk: 4}},
                                       content_type="application/json")
    assert response.status_code == 429
    assert voting.ballot_of(Voter.objects.get(event=event, user__email=EMAILS["participant"])) == {projects[1].pk: 2}


@pytest.fixture
def judged():
    """The fixture event, with its reviews, running a community vote."""
    event = open_voting(Event.objects.get(slug=EVENT))
    project = Project.objects.filter(event=event, status="submitted", duplicate_of__isnull=True).order_by("pk").first()
    return event, project


def test_publish_is_refused_while_voting_is_open(web, judged):
    event, _ = judged
    response = web("organizer").post(f"/events/{EVENT}/manage/publish")
    assert response.status_code == 409 and "Nothing was published" in response.content.decode()
    event.refresh_from_db()
    assert event.results_published_at is None
    assert not AuditLog.objects.filter(event=event, action="results.publish").exists()


def test_published_results_hide_again_while_voting_is_open(web, api, judged):
    event, _ = judged
    close_voting(event)
    assert web("organizer").post(f"/events/{EVENT}/manage/publish").status_code == 302
    assert api().get(f"/api/events/{EVENT}/results").status_code == 200
    open_voting(event)
    assert api().get(f"/api/events/{EVENT}/results").status_code == 404
    assert web().get(f"/events/{EVENT}/results").status_code == 404
    assert api("organizer").get(f"/api/events/{EVENT}/results").status_code == 200


def test_community_votes_are_public_only_once_published(web, api, judged):
    event, project = judged
    voters = cast_directly(event, [{project: 3}, {project: 2}, {project: 1}])
    Voter.objects.filter(pk=voters[2].pk).update(voided_at=db_now(), voided_reason="sock puppet")
    web("organizer").post(f"/events/{EVENT}/manage/calibration")
    body = api("organizer").get(f"/api/events/{EVENT}/results").json()
    assert "community_voters" not in body and all("community_votes" not in r for r in body["projects"])
    close_voting(event)
    assert web("organizer").post(f"/events/{EVENT}/manage/publish").status_code == 302
    body = api().get(f"/api/events/{EVENT}/results").json()
    assert body["community_voters"] == 2
    votes = {r["project"]: r["community_votes"] for r in body["projects"]}
    assert votes[project.pk] == 5 and set(votes.values()) == {0, 5}
    assert "Community votes: 5 from 2 voters" in web().get(f"/events/{EVENT}/results").content.decode()


# Settings and seed

def test_organizers_can_set_the_vote_in_settings():
    assert {"voting_mode", "vote_credits", "voting_open", "voting_close"} <= set(EventForm().fields)


def test_demo_event_is_seeded_with_an_account_vote_after_submissions():
    demo = Event.objects.get(slug=DEMO)
    assert demo.voting_mode == "account" and demo.vote_credits == 25
    assert demo.submissions_close <= demo.voting_open < demo.voting_close
