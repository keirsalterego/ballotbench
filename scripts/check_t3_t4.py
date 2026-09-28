#!/usr/bin/env python3
"""A checker for tiers T3 and T4, in the style of the official run.py.

run.py checks T1 and T2 only. This file checks every T3 and T4 bullet from
the Dogfood spec against the running portal, over plain HTTP, and prints
the exact requests behind each result, so a judge can read it in ten
minutes and see nothing is taken on trust. Standard library only. It reads
base_url and the auth headers from .dogfood.toml, like run.py, and signs in
the seeded demo accounts (password "ballotbench-demo") where a page needs a
session. It sets up its own events, so it never changes the fixture event,
and can run any number of times.

    python3 scripts/check_t3_t4.py .dogfood.toml > t3-t4-report.txt
    python3 scripts/check_t3_t4.py .dogfood.toml --base http://localhost:8099   # a stack on another port

It changes the portal's data (it creates events, accounts, votes, comments
and a webhook), so run it against a demo stack, not a real event.
"""
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEMO_PASSWORD = "ballotbench-demo"
NEW_PASSWORD = "a long checker passphrase"
ORGANIZER, ADMIN, VOTERS = ("organizer@ballotbench.local", "admin@ballotbench.local",
                            ["priya1@example.org", "ines.rocha@example.org", "diego.herrera@example.org"])


def read_config(path):
    text = open(path, encoding="utf-8").read()
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:         # Python before 3.11: the two tables we need, read by hand
        cfg, section = {}, None
        for line in text.splitlines():
            line = line.split("#")[0].strip()
            if line.startswith("["):
                section = cfg.setdefault(line.strip("[]"), {})
            elif "=" in line and section is not None:
                k, v = line.split("=", 1)
                section[k.strip()] = v.strip().strip('"')
        return cfg


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    """One browser or one token: its own cookies, the requests it made."""

    def __init__(self, base, header=None):
        self.base, self.header = base, header
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())

    def csrf(self):
        token = next((c.value for c in self.jar if c.name == "csrftoken"), None)
        if token is None:
            self.call("GET", "/login", log=False)
            token = next(c.value for c in self.jar if c.name == "csrftoken")
        return token

    def call(self, method, path, form=None, data=None, raw=None, log=True):
        headers = {}
        if self.header:
            name, _, value = self.header.partition(":")
            headers[name.strip()] = value.strip()
        body = None
        if form is not None:
            body = urllib.parse.urlencode({"csrfmiddlewaretoken": self.csrf(), **form}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None or raw is not None:
            body = raw.encode() if raw is not None else json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if method != "GET" and not self.header:
            headers["X-CSRFToken"] = self.csrf()
            headers["Referer"] = self.base + "/"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as r:
                status, text, hdrs = r.status, r.read().decode(errors="replace"), r.headers
        except urllib.error.HTTPError as e:
            status, text, hdrs = e.code, e.read().decode(errors="replace"), e.headers
        if log and CURRENT is not None:
            who = getattr(self, "who", "anonymous")
            CURRENT.notes.append(f"{method} {path}  as {who}  -> {status}")
        return status, text, hdrs

    def json(self, method, path, data=None, raw=None, log=True):
        status, text, _ = self.call(method, path, data=data, raw=raw, log=log)
        try:
            return status, json.loads(text)
        except ValueError:
            return status, None


def session(base, email, password=DEMO_PASSWORD):
    c = Client(base)
    c.who = email
    status, _, _ = c.call("POST", "/login", form={"username": email, "password": password}, log=False)
    if status != 302:
        raise SystemExit(f"can't sign in as {email} ({status}): is this a demo stack (BALLOTBENCH_DEMO_SEED=1)?")
    return c


def signup(base, email, name):
    c = Client(base)
    c.who = email
    c.call("POST", "/signup", form={"name": name, "email": email, "password": NEW_PASSWORD}, log=False)
    return c


CURRENT = None
RESULTS = []


class Check:
    def __init__(self, tier, label):
        self.tier, self.label, self.notes, self.ok = tier, label, [], False

    def __enter__(self):
        global CURRENT
        CURRENT = self
        return self

    def __exit__(self, kind, exc, tb):
        global CURRENT
        if kind is AssertionError:
            self.notes.append(f"FAILED: {exc}")
            self.ok = False
        elif kind is not None:
            self.notes.append(f"ERROR: {kind.__name__}: {exc}")
            self.ok = False
        else:
            self.ok = True
        RESULTS.append(self)
        CURRENT = None
        return True


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def make_event(org, base, slug, mode, judged=False):
    """An event with five submitted projects, submissions closed, and a
    community vote open now in `mode`. With judged=True, two judges have
    reviewed every project, so there is something to calibrate and publish.
    Setup requests aren't logged: they aren't what a check checks."""
    t = now()
    form = {"name": f"Checker {slug}", "slug": slug, "description": "Made by scripts/check_t3_t4.py",
            "submissions_open": iso(t - timedelta(hours=2)), "submissions_close": iso(t + timedelta(hours=2)),
            "judging_open": iso(t + timedelta(hours=2)), "judging_close": "", "voting_open": "", "voting_close": "",
            "voting_mode": "off", "vote_credits": 25, "reviews_per_project": 3, "max_team_size": 4}
    status, text, _ = org.call("POST", "/events/new", form=form, log=False)
    if status != 302:
        raise SystemExit(f"couldn't create an event ({status})")
    projects = []
    for i, title in enumerate(["Tidewatch", "Lanternfish", "Quillpad", "Brickyard", "Ferrylight"]):
        lead = signup(base, f"{slug}-lead{i}@example.org", f"{title} lead")
        lead.call("POST", f"/events/{slug}/join", form={}, log=False)
        lead.call("POST", f"/events/{slug}/team", form={"name": f"{title} team"}, log=False)
        status, body = lead.json("POST", f"/api/events/{slug}/projects",
                                 {"title": title, "summary": f"{title}, for the checker", "submit": True}, log=False)
        projects.append({"id": body["id"], "title": title, "lead": lead})
    judges = []
    if judged:
        for i in range(2):
            org.call("POST", f"/events/{slug}/manage/invites", form={"role": "judge", "note": f"judge {i}"}, log=False)
            _, page, _ = org.call("GET", f"/events/{slug}/manage", log=False)
            judge = signup(base, f"{slug}-judge{i}@example.org", f"Judge {i}")
            judge.call("POST", re.search(r"/join/[\w-]+", page).group(0), form={}, log=False)
            judges.append(judge)
    t = now()
    form.update(submissions_close=iso(t - timedelta(seconds=5)), judging_open=iso(t - timedelta(seconds=5)),
                voting_mode=mode, voting_open=iso(t - timedelta(seconds=4)), voting_close=iso(t + timedelta(hours=1)))
    org.call("POST", f"/events/{slug}/manage", form=form, log=False)
    if judged:
        org.call("POST", f"/events/{slug}/manage/assign", form={"k": 2}, log=False)
        for i, judge in enumerate(judges):
            _, mine = judge.json("GET", "/api/judge/assignments", log=False)
            for a in (a for a in mine if a["event"] == slug):
                score = 2 + (a["project"] + i) % 4
                judge.json("POST", f"/api/judge/assignments/{a['id']}/review",
                           {"scores": {"functionality": score, "quality": score, "innovation": score}, "submit": True},
                           log=False)
    return form, projects


def close_vote(org, slug, form):
    form.update(voting_close=iso(now()))
    org.call("POST", f"/events/{slug}/manage", form=form)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python3 scripts/check_t3_t4.py .dogfood.toml [--base URL]")
    cfg = read_config(sys.argv[1])
    configured = cfg["portal"]["base_url"].rstrip("/")
    base = sys.argv[sys.argv.index("--base") + 1].rstrip("/") if "--base" in sys.argv else configured
    auth = cfg["auth"]
    run = datetime.now().strftime("%H%M%S")
    anon = Client(base)
    anon.who = "anonymous"
    org = session(base, ORGANIZER)
    judge_a, judge_b = Client(base, auth["judge_a"]), Client(base, auth["judge_b"])
    organizer_token, participant = Client(base, auth["organizer"]), Client(base, auth["participant"])
    for client, role in ((judge_a, "judge_a"), (judge_b, "judge_b"), (organizer_token, "organizer"),
                         (participant, "participant")):
        client.who = f"{role} (token)"

    print("DOGFOOD 2026 T3/T4 report (scripts/check_t3_t4.py; run.py covers T1 and T2)")
    print(f"portal: {base}" + (f" (--base; .dogfood.toml says {configured})" if base != configured else ""))
    print()

    acct = f"chk-{run}-a"
    email_slug = f"chk-{run}-e"
    acct_form, acct_projects = make_event(org, base, acct, "account", judged=True)
    email_form, email_projects = make_event(org, base, email_slug, "email")
    voters = [session(base, v) for v in VOTERS]

    # ── T3 ────────────────────────────────────────────────────────────────
    with Check("T3", "community voting, authenticated") as c:
        s, _ = anon.json("POST", f"/api/events/{acct}/ballot", {"votes": {}})
        expect(s == 401, f"anonymous ballot should be 401, got {s}")
        s, ballot = voters[0].json("GET", f"/api/events/{acct}/ballot")
        expect(s == 200 and len(ballot["projects"]) == 5, f"ballot should list 5 projects, got {s}")
        pid = acct_projects[0]["id"]
        s, body = voters[0].json("POST", f"/api/events/{acct}/ballot", {"votes": {str(pid): 3, str(acct_projects[1]['id']): 2}})
        expect(s == 200, f"a 13-credit ballot should be saved, got {s} {body}")
        new = signup(base, f"chk-{run}-fresh@example.org", "Unconfirmed")
        s, _ = new.json("GET", f"/api/events/{acct}/ballot")
        expect(s == 403, f"an account that hasn't confirmed its address should get 403, got {s}")

    with Check("T3", "community voting, quadratic budget") as c:
        s, body = voters[1].json("POST", f"/api/events/{acct}/ballot",
                                 {"votes": {str(acct_projects[0]["id"]): 5, str(acct_projects[1]["id"]): 1}})
        expect(s == 409, f"5² + 1² = 26 credits over a 25 budget should be 409, got {s}")
        s, body = voters[1].json("POST", f"/api/events/{acct}/ballot", {"votes": {str(acct_projects[2]["id"]): 5}})
        expect(s == 200, f"5² = 25 credits should fit exactly, got {s}")

    with Check("T3", "community voting, email gated") as c:
        address = f"chk.{run}+first@gmail.com"
        voter = Client(base)
        voter.who = address
        s, _, _ = voter.call("POST", f"/events/{email_slug}/vote/link", form={"email": address})
        expect(s in (200, 302), f"asking for a voting link should succeed, got {s}")
        admin = session(base, ADMIN)
        _, listing, _ = admin.call("GET", f"/admin/portal/outboundemail/?q={urllib.parse.quote(address)}")
        mail = re.search(r'/admin/portal/outboundemail/(\d+)/change/', listing)
        expect(mail, "the link should be in the outbox (Admin → Outbound emails)")
        _, detail, _ = admin.call("GET", f"/admin/portal/outboundemail/{mail.group(1)}/change/")
        link = re.search(r"/vote/confirm/[\w:.-]+", detail)
        expect(link, "the mail should carry a /vote/confirm/ link")
        s, page, _ = voter.call("GET", link.group(0))
        expect(s == 200 and "<form" in page, f"opening the link should show a button, not confirm yet ({s})")
        voter.call("POST", link.group(0), form={})
        s, page, _ = voter.call("GET", f"/events/{email_slug}/vote")
        expect(s == 200, f"the confirmed browser should get its ballot, got {s}")
        pid = email_projects[0]["id"]
        s, _, _ = voter.call("POST", f"/events/{email_slug}/vote", form={f"p{pid}": 2})
        expect(s in (200, 302), f"the email voter's ballot should save, got {s}")
        stranger = Client(base)
        s, page, _ = stranger.call("GET", f"/events/{email_slug}/vote")
        expect('name="email"' in page and f"p{pid}" not in page,
               "a browser without a confirmed link should be asked for an address, not shown the ballot")

    with Check("T3", "project comments") as c:
        pid = acct_projects[3]["id"]
        s, _ = anon.json("POST", f"/api/projects/{pid}/comments", {"body": "hello"})
        expect(s == 401, f"anonymous comment should be 401, got {s}")
        s, comment = voters[2].json("POST", f"/api/projects/{pid}/comments", {"body": f"Nice work ({run})"})
        expect(s == 201, f"a signed-in comment should be 201, got {s}")
        s, listed = anon.json("GET", f"/api/projects/{pid}/comments")
        expect(s == 200 and any(x["id"] == comment["id"] for x in listed), "the comment should be public")
        org.call("POST", f"/comments/{comment['id']}/moderate", form={"action": "hide"})
        s, listed = anon.json("GET", f"/api/projects/{pid}/comments")
        expect(not any(x["id"] == comment["id"] for x in listed), "a hidden comment should vanish for everyone else")

    with Check("T3", "randomized project ordering on ballots") as c:
        orders = []
        for v in voters:
            s, ballot = v.json("GET", f"/api/events/{acct}/ballot")
            orders.append([p["id"] for p in ballot["projects"]])
        s, again = voters[0].json("GET", f"/api/events/{acct}/ballot")
        expect([p["id"] for p in again["projects"]] == orders[0], "each voter's order should be stable")
        expect(len({tuple(o) for o in orders}) > 1, f"three voters should not all see one order: {orders}")
        expect(orders[0] != sorted(orders[0]), "the order should not simply be by id")
        c.notes.append("orders: " + " | ".join(",".join(map(str, o)) for o in orders))

    with Check("T3", "results hidden during the voting window") as c:
        org.call("POST", f"/events/{acct}/manage/calibration", form={})
        s, _, _ = org.call("POST", f"/events/{acct}/manage/publish", form={})
        expect(s == 409, f"publishing while the vote is open should be 409, got {s}")
        s, _ = anon.json("GET", f"/api/events/{acct}/results")
        expect(s == 404, f"results should be 404 to the public during the vote, got {s}")
        s, _ = voters[0].json("GET", f"/api/events/{acct}/results")
        expect(s == 404, f"results should be 404 to a voter during the vote, got {s}")
        close_vote(org, acct, acct_form)
        s, _, _ = org.call("POST", f"/events/{acct}/manage/publish", form={})
        expect(s == 302, f"publishing after the vote closes should work, got {s}")
        s, res = anon.json("GET", f"/api/events/{acct}/results")
        expect(s == 200 and "community_votes" in res["projects"][0], "published results should carry the community votes")

    with Check("T3", "anti-abuse: rate limits") as c:
        # The vote above has closed, so the flood goes to an event of its own.
        flood_slug = f"chk-{run}-f"
        _, flood_projects = make_event(org, base, flood_slug, "account")
        pid = str(flood_projects[0]["id"])
        codes = [voters[2].json("POST", f"/api/events/{flood_slug}/ballot", {"votes": {pid: 1}}, log=False)[0]
                 for _ in range(22)]
        c.notes.append(f"POST /api/events/{flood_slug}/ballot x22  as {VOTERS[2]}  -> {codes}")
        expect(codes[0] == 200 and 429 in codes, f"one voter saving a ballot 22 times should hit 429: {codes}")

    with Check("T3", "anti-abuse: duplicate detection") as c:
        same_inbox = f"chk.{run}+second@googlemail.com".replace(".", "", 1)
        other = Client(base)
        other.call("POST", f"/events/{email_slug}/vote/link", form={"email": same_inbox})
        s, csv_text, _ = organizer_token.call("GET", f"/api/events/{email_slug}/export/audit.csv")
        expect("vote.duplicate_refused" in csv_text, "a second spelling of one inbox should be refused and logged")
        c.notes.append(f"the page answers {s} either way, so it can't reveal who voted; the audit log records "
                       f"vote.duplicate_refused for {same_inbox}")
        s, fixture = anon.json("GET", "/api/events/sample-hack-2026/projects")
        expect(any(p.get("duplicate_of") for p in fixture), "the fixture's duplicate submission (prj_41) should be flagged")

    with Check("T3", "anti-abuse: audit trail") as c:
        s, csv_text, _ = organizer_token.call("GET", f"/api/events/{acct}/export/audit.csv")
        lines = csv_text.splitlines()
        expect(s == 200 and lines[0].startswith("seq,ts,actor,action"), f"the audit export should work, got {s}")
        actions = {line.split(",")[3] for line in lines[1:]}
        for needed in ("vote.cast", "comment.hide", "results.publish", "event.update"):
            expect(needed in actions, f"the audit log should record {needed}")
        expect(all(re.search(r",[0-9a-f]{64}$", line) for line in lines[1:]), "every row should carry its SHA-256 chain hash")
        s, _, _ = judge_a.call("GET", f"/api/events/{acct}/export/audit.csv")
        expect(s == 403, f"a judge reading the audit log should be 403, got {s}")

    # ── T4 ────────────────────────────────────────────────────────────────
    with Check("T4", "REST API with an OpenAPI document") as c:
        s, text, hdrs = anon.call("GET", "/api/schema")
        expect(s == 200 and text.startswith("openapi: 3"), f"/api/schema should serve OpenAPI 3, got {s}")
        for route in ("/api/events/{slug}/ballot", "/api/judge/record", "/api/events/import"):
            expect(route in text, f"the schema should document {route}")
        s, _ = anon.json("GET", "/api/events")
        expect(s == 200, "the events API should answer")

    with Check("T4", "webhooks") as c:
        s, page, _ = org.call("POST", f"/events/{acct}/manage/webhooks", form={"url": "http://127.0.0.1:9/x", "actions": ""})
        expect(s == 422 and "private address" in page, "an address on the portal's own network should be refused (422)")
        s, _, _ = org.call("POST", f"/events/{acct}/manage/webhooks",
                           form={"url": "https://93.184.215.14/ballotbench-hook", "actions": ""})
        expect(s in (200, 302), f"a public webhook URL should be accepted, got {s}")
        org.call("POST", f"/events/{acct}/manage/tracks", form={"name": f"Hooked {run}"})
        s, page, _ = org.call("GET", f"/events/{acct}/manage/webhooks")
        expect("93.184.215.14" in page and "track.create" in page,
               "the endpoint should be listed with a queued delivery for the change just made")
        c.notes.append("the webhooks page lists the endpoint and a queued track.create delivery")
        s, _, _ = participant.call("GET", f"/events/{acct}/manage/webhooks")
        expect(s == 403, f"a participant opening the webhooks page should be 403, got {s}")

    with Check("T4", "certificate and record generation") as c:
        judge_web = session(base, "diego.herrera@example.org")
        s, page, _ = judge_web.call("GET", "/events/sample-hack-2026/certificate")
        expect(s == 200 and "Certificate" in page and '"signature"' in page.replace("&quot;", '"'),
               f"a judge's certificate should render with its signed record, got {s}")
        other = session(base, "ines.rocha@example.org")
        s, _, _ = other.call("GET", "/events/sample-hack-2026/certificate?person=diego.herrera@example.org")
        expect(s == 404, f"someone else's certificate should be 404, got {s}")
        s, record = participant.json("GET", "/api/participant/record?event=sample-hack-2026")
        expect(s == 200 and record["record"].get("team"), "a participant's signed record should name their team")

    with Check("T4", "signed, publicly verifiable judge records") as c:
        s, key = anon.json("GET", "/.well-known/ballotbench-signing-key")
        expect(s == 200 and key.get("algorithm") == "Ed25519", "the public key should be published")
        s, record = judge_a.json("GET", "/api/judge/record?event=sample-hack-2026")
        expect(s == 200 and "signature" in record and "scores" not in json.dumps(record["record"]),
               "a judge's record should be signed and carry no scores")
        s, verdict = anon.json("POST", "/api/verify", raw=json.dumps(record))
        expect(verdict.get("valid") is True, f"the genuine record should verify: {verdict}")
        record["record"]["reviews_submitted"] = 99
        s, verdict = anon.json("POST", "/api/verify", raw=json.dumps(record))
        expect(verdict.get("valid") is False, "a doctored record should not verify")
        s, _ = judge_b.json("GET", "/api/judge/record?event=sample-hack-2026&judge=jdg_24")
        expect(s == 403, f"judge B fetching judge A's record should be 403, got {s}")

    with Check("T4", "embeddable gallery widget") as c:
        s, js, hdrs = anon.call("GET", "/embed.js")
        expect(s == 200 and "javascript" in hdrs.get("Content-Type", ""), f"/embed.js should serve JavaScript, got {s}")
        s, page, hdrs = anon.call("GET", "/embed/sample-hack-2026")
        expect(s == 200 and "Glass Signal" in page, "the embed should list the event's projects")
        expect("frame-ancestors *" in hdrs.get("Content-Security-Policy", ""), "the embed should be frameable")
        s, _, hdrs = anon.call("GET", "/projects")
        expect(hdrs.get("X-Frame-Options") == "DENY", "every other page should stay unframeable")

    with Check("T4", "bulk import and export") as c:
        s, bundle = organizer_token.json("GET", f"/api/events/{acct}/export/bundle.json")
        expect(s == 200 and len(bundle["projects"]) == 5, f"the bundle should hold the event's 5 projects, got {s}")
        admin = session(base, ADMIN)
        copy = f"{acct}-copy"
        s, body = admin.json("POST", f"/api/events/import?slug={copy}&name=Imported%20{run}", bundle)
        expect(s == 201, f"a site admin importing the bundle as a new event should be 201, got {s} {body}")
        s, again = organizer_token.json("GET", f"/api/events/{copy}/export/bundle.json")
        if s == 403:
            s, again = admin.json("GET", f"/api/events/{copy}/export/bundle.json")
        expect(s == 200 and sorted(p["title"] for p in again["projects"]) == sorted(p["title"] for p in bundle["projects"])
               and len(again["scores"]) == len(bundle["scores"]), "the re-exported copy should hold the same projects and scores")
        s, _ = organizer_token.json("POST", f"/api/events/import?slug={copy}-2", bundle)
        expect(s == 403, f"an organizer (not a site admin) importing should be 403, got {s}")
        for kind in ("registrations", "teams", "projects", "scores", "results", "audit"):
            s, text, _ = organizer_token.call("GET", f"/api/events/{acct}/export/{kind}.csv")
            expect(s == 200 and "," in text.splitlines()[0], f"{kind}.csv should export")

    # ── report ────────────────────────────────────────────────────────────
    for c in RESULTS:
        dots = "." * max(3, 44 - len(c.label))
        print(f"{c.tier}  {c.label} {dots} {'PASS' if c.ok else 'FAIL'}")
        for note in c.notes:
            print(f"       {note}")
    print()
    for tier in ("T3", "T4"):
        checks = [c for c in RESULTS if c.tier == tier]
        print(f"{tier}: {sum(c.ok for c in checks)} of {len(checks)} checks pass")
    return 0 if all(c.ok for c in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
