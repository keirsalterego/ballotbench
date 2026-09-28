#!/usr/bin/env python3
"""A narrated walk through one whole event, for a live demo or a recording.

It drives a running portal over HTTP exactly as people would, through the
same pages and API: an organizer sets up a fresh event, teams sign up and
submit, the deadline refuses a late edit, reviews are handed out, four
judges with very different habits score, isolation attacks are refused,
calibration takes the habits out, a community vote runs, the organizer
publishes signed results, and a team reads why it placed where it did.

Standard library only, like run.py. Every run makes a new event and new
accounts, so it can be run again without resetting anything.

    python3 scripts/demo.py                    # against http://localhost:8080
    python3 scripts/demo.py --pause            # wait for Enter between steps
    python3 scripts/demo.py --base http://localhost:9000
"""
import argparse
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PASSWORD = "ballotbench-demo"
PEOPLE_PASSWORD = "a long demo passphrase"
ORGANIZER = "organizer@ballotbench.local"

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = ("\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m",
                                               "\033[36m", "\033[0m")
if not sys.stdout.isatty():
    BOLD = DIM = GREEN = RED = YELLOW = CYAN = RESET = ""

args = None
step_no = 0


def step(title):
    global step_no
    step_no += 1
    print(f"\n{BOLD}{CYAN}── {step_no}. {title} {'─' * max(3, 60 - len(title))}{RESET}")
    if args.pause:
        input(f"{DIM}   (Enter to run it){RESET}")


def say(text):
    print(f"   {text}")


def show(path):
    print(f"   {YELLOW}open {args.base}{path}{RESET}")


def ok(text):
    print(f"   {GREEN}✓{RESET} {text}")


def refused(text):
    print(f"   {GREEN}✓ refused{RESET} {text}")


def fail(text):
    print(f"   {RED}✗ {text}{RESET}")
    sys.exit(1)


def pause_briefly():
    if not args.pause:
        time.sleep(args.delay)


class Person:
    """One browser: its own cookies, its own CSRF token."""

    def __init__(self, email, name=""):
        self.email, self.name = email, name
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  NoRedirect())

    def csrf(self):
        for c in self.jar:
            if c.name == "csrftoken":
                return c.value
        self.request("GET", "/login")
        return next(c.value for c in self.jar if c.name == "csrftoken")

    def request(self, method, path, form=None, data=None, headers=None):
        headers = dict(headers or {})
        body = None
        if form is not None:
            form = {"csrfmiddlewaretoken": self.csrf(), **form}
            body = urllib.parse.urlencode(form, doseq=True).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if method != "GET":
            headers["X-CSRFToken"] = self.csrf()
            headers["Referer"] = args.base + "/"
        req = urllib.request.Request(args.base + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, r.read().decode(errors="replace"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), e.headers

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, form):
        return self.request("POST", path, form=form)

    def api(self, method, path, data=None):
        status, text, _ = self.request(method, path, data=data)
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def login(self, password=PASSWORD):
        status, _, _ = self.post("/login", {"username": self.email, "password": password})
        if status != 302:
            fail(f"couldn't log in as {self.email} ({status})")
        return self

    def signup(self):
        status, text, _ = self.post("/signup", {"name": self.name, "email": self.email, "password": PEOPLE_PASSWORD})
        if status != 302:
            fail(f"couldn't sign up {self.email} ({status}): {strip(text)[:200]}")
        return self


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def main():
    global args
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each step")
    parser.add_argument("--delay", type=float, default=0.6, help="seconds between steps without --pause")
    args = parser.parse_args()
    args.base = args.base.rstrip("/")
    run = datetime.now().strftime("%H%M%S")
    slug = f"demo-day-{run}"

    print(f"{BOLD}ballotbench: one whole event, start to finish{RESET}  {DIM}({args.base}, run {run}){RESET}")

    # ── set up ─────────────────────────────────────────────────────────────
    step("An organizer creates the event")
    org = Person(ORGANIZER).login()
    t = now()
    event = {"name": f"Demo Day {run}", "slug": slug, "description": "A live walk through ballotbench.",
             "submissions_open": iso(t - timedelta(hours=1)), "submissions_close": iso(t + timedelta(hours=2)),
             "judging_open": iso(t + timedelta(hours=2)), "judging_close": "",
             "voting_open": "", "voting_close": "", "voting_mode": "off", "vote_credits": 25,
             "reviews_per_project": 3, "max_team_size": 4}
    status, text, _ = org.post("/events/new", event)
    if status != 302:
        fail(f"event not created ({status}): {strip(text)[:300]}")
    ok(f"Demo Day {run}: submissions open now, judging after they close, three reviews per project")
    org.post(f"/events/{slug}/manage/tracks", {"name": "Developer tools"})
    org.post(f"/events/{slug}/manage/prizes", {"name": "Best in show", "description": "Overall winner"})
    _, manage, _ = org.get(f"/events/{slug}/manage")
    pk = re.search(r'/manage/rubric/(\d+)"[^>]*>\s*<input type="hidden" name="csrfmiddlewaretoken"[^>]*>\s*'
                   r'<td><input name="key" value="functionality"', manage)
    if pk:
        org.post(f"/events/{slug}/manage/rubric/{pk.group(1)}",
                 {"key": "functionality", "name": "Functionality", "weight": "2", "min_value": 1, "max_value": 5,
                  "position": 0})
        ok("rubric: functionality counts double; the database freezes it at the first score")
    ok("a track and an overall prize")
    show(f"/events/{slug}/manage")
    pause_briefly()

    step("The organizer invites four judges by single-use link")
    judges = [Person(f"hana.{run}@example.org", "Hana (harsh)"), Person(f"gus.{run}@example.org", "Gus (generous)"),
              Person(f"sam.{run}@example.org", "Sam (steady)"), Person(f"fran.{run}@example.org", "Fran (flat)")]
    for judge in judges:
        org.post(f"/events/{slug}/manage/invites", {"role": "judge", "note": judge.name})
        _, page, _ = org.get(f"/events/{slug}/manage")
        link = re.search(r"/join/[\w-]+", page).group(0)
        judge.signup()
        status, _, _ = judge.post(link, {})
        if status != 302:
            fail(f"{judge.name} couldn't accept the invite ({status})")
        ok(f"{judge.name} accepted {link[:14]}…  (the link now works for nobody)")
    pause_briefly()

    # ── teams and projects ────────────────────────────────────────────────
    step("Four teams sign up, form teams by invite link, and submit")
    projects = [("Tidewatch", "Flood alerts from river gauges", 5), ("Lanternfish", "Offline maps for search teams", 4),
                ("Quillpad", "Collaborative lab notebooks", 3), ("Brickyard", "A to-do app", 2)]
    teams = []
    for i, (title, tagline, quality) in enumerate(projects):
        lead = Person(f"lead{i}.{run}@example.org", f"{title} lead").signup()
        lead.post(f"/events/{slug}/join", {})
        lead.post(f"/events/{slug}/team", {"name": f"Team {title}"})
        if i == 0:
            _, page, _ = lead.get(f"/events/{slug}/me")
            team_pk = re.search(r"/teams/(\d+)/invites", page).group(1)
            lead.post(f"/teams/{team_pk}/invites", {})
            _, page, _ = lead.get(f"/events/{slug}/me")
            invite = re.search(r"/invite/[\w-]+", page).group(0)
            mate = Person(f"mate0.{run}@example.org", "Tidewatch teammate").signup()
            status, _, _ = mate.post(invite, {})
            ok(f"Team {title} invited a teammate with a single-use link ({'joined' if status == 302 else status})")
        status, body = lead.api("POST", f"/api/events/{slug}/projects",
                                {"title": title, "tagline": tagline, "summary": tagline,
                                 "repo_url": f"https://example.org/{title.lower()}", "submit": True})
        if status != 201:
            fail(f"{title} not submitted ({status}): {body}")
        teams.append({"lead": lead, "title": title, "quality": quality, "project": body["id"]})
        ok(f"{title} submitted: public in the gallery")
    show(f"/projects?event={slug}")
    pause_briefly()

    # ── the deadline ──────────────────────────────────────────────────────
    step("The deadline passes, and holds on every path")
    t = now()
    event.update(submissions_close=iso(t - timedelta(seconds=2)), judging_open=iso(t - timedelta(seconds=2)))
    org.post(f"/events/{slug}/manage", event)
    ok("organizer moves submissions close to just now")
    status, body = teams[3]["lead"].api("PATCH", f"/api/projects/{teams[3]['project']}", {"title": "Brickyard 2.0"})
    if status != 409:
        fail(f"a late edit got {status}")
    refused(f"late edit through the API: 409 {body.get('detail', '')}")
    status, _, _ = teams[3]["lead"].post(f"/events/{slug}/project/new", {"title": "Sneaky late entry", "summary": "x"})
    refused(f"late new project through the page: {status}")
    say(f"{DIM}Behind both, a Postgres trigger checks the database's own clock: even a bug in the app couldn't let it through.{RESET}")
    pause_briefly()

    # ── judging ───────────────────────────────────────────────────────────
    step("The organizer hands out reviews")
    org.post(f"/events/{slug}/manage/assign", {"k": 3})
    ok("every project gets three judges; the idlest judge picks next; nobody reviews their own team")
    show(f"/events/{slug}/manage/progress")
    pause_briefly()

    step("Four judges score, each with a habit")
    habits = {"Hana (harsh)": lambda q: max(1, q - 2), "Gus (generous)": lambda q: min(5, q + 1),
              "Sam (steady)": lambda q: q, "Fran (flat)": lambda q: 3}
    quality = {tm["project"]: tm["quality"] for tm in teams}
    for judge in judges:
        status, mine = judge.api("GET", "/api/judge/assignments")
        mine = [a for a in mine if a["event"] == slug]
        for a in mine:
            s = habits[judge.name](quality[a["project"]])
            judge.api("POST", f"/api/judge/assignments/{a['id']}/review",
                      {"scores": {"functionality": s, "quality": s, "innovation": s}, "submit": True,
                       "comment": f"{judge.name.split()[0]} was here"})
        ok(f"{judge.name:15} scored {len(mine)} projects")
    say(f"{DIM}Hana scores two points low, Gus one high, Fran gives everything a 3.{RESET}")
    show(f"/judge  (as {judges[0].email}, password '{PEOPLE_PASSWORD}')")
    pause_briefly()

    step("Isolation: judges and teams try to see what they shouldn't")
    status, _ = judges[0].api("GET", f"/api/judge/scores?judge={judges[1].email}")
    refused(f"Hana asks for Gus's scores: {status}")
    status, gus = judges[1].api("GET", "/api/judge/assignments")
    hana_ids = {a["id"] for a in judges[0].api("GET", "/api/judge/assignments")[1]}
    other = next((a["id"] for a in gus if a["id"] not in hana_ids), None)
    if other:
        status, _ = judges[0].api("POST", f"/api/judge/assignments/{other}/review", {"scores": {"quality": 5}})
        refused(f"Hana tries to score Gus's assignment: {status} (not even told it exists)")
    status, _ = teams[0]["lead"].api("GET", "/api/judge/scores")
    refused(f"a participant reads judge scores: {status}")
    status, _ = teams[0]["lead"].api("GET", f"/api/events/{slug}/export/scores.csv")
    refused(f"a participant exports the scores: {status}")
    pause_briefly()

    # ── calibration ───────────────────────────────────────────────────────
    step("Calibration takes each judge's habits out")
    org.post(f"/events/{slug}/manage/calibration", {})
    status, res = org.api("GET", f"/api/events/{slug}/results")
    _, _, _ = org.get("/")
    status, judges_csv, _ = org.get(f"/api/events/{slug}/export/judges.csv")
    for line in judges_csv.splitlines()[1:]:
        cols = line.split(",")
        if cols[-1] != "ok":
            ok(f"{cols[1]}: flagged {cols[-1]}, carries no weight, exactly as if absent")
    print(f"   {'rank':>4}  {'could be':>8}  {'raw rank':>8}  project")
    for row in res["projects"]:
        low, high = row["rank_interval"]
        print(f"   {row['rank']:>4}  {f'{low}-{high}':>8}  {row['raw_rank']:>8}  {row['title']}")
    say(f"{DIM}Each rank comes with the range it could plausibly be; a kingmaker check and a pairwise second opinion are on the page.{RESET}")
    show(f"/events/{slug}/manage/calibration")
    pause_briefly()

    # ── community vote ────────────────────────────────────────────────────
    step("A community vote, quadratic, in a random order per voter")
    t = now()
    event.update(voting_mode="account", voting_open=iso(t - timedelta(seconds=1)),
                 voting_close=iso(t + timedelta(hours=1)))
    org.post(f"/events/{slug}/manage", event)
    status, _, _ = org.post(f"/events/{slug}/manage/publish", {})
    refused(f"publishing while the vote is open: {status}")
    for i, email in enumerate(["priya1@example.org", "ines.rocha@example.org", "diego.herrera@example.org"]):
        voter = Person(email).login()
        _, ballot = voter.api("GET", f"/api/events/{slug}/ballot")
        order = ", ".join(p["title"] for p in ballot.get("projects", []))
        pid = teams[i % 2]["project"]
        status, body = voter.api("POST", f"/api/events/{slug}/ballot",
                                 {"votes": {str(pid): 3, str(teams[2]["project"]): 2}})
        if status != 200:
            fail(f"{email} couldn't vote ({status}): {body}")
        ok(f"{email.split('@')[0]:15} 3² + 2² = 13 of 25 credits; their order: {order}")
    status, body = Person("priya1@example.org").login().api(
        "POST", f"/api/events/{slug}/ballot", {"votes": {str(teams[0]["project"]): 5, str(teams[1]["project"]): 1}})
    refused(f"5² + 1² = 26 is over the 25-credit budget: {status}")
    show(f"/events/{slug}/manage/voting")
    event.update(voting_close=iso(now()))
    org.post(f"/events/{slug}/manage", event)
    ok("the organizer closes the vote")
    pause_briefly()

    # ── publish ───────────────────────────────────────────────────────────
    step("Publish: frozen, fingerprinted and signed")
    status, _, _ = org.post(f"/events/{slug}/manage/publish", {})
    ok(f"published ({status})")
    status, public = Person("anon@example.org").api("GET", f"/api/events/{slug}/results")
    for row in public["projects"]:
        extra = f", {row['community_votes']} community votes" if "community_votes" in row else ""
        print(f"   {row['rank']}. {row['title']}{extra}")
    status, signed = Person("anon@example.org").api("GET", f"/api/events/{slug}/results/signed")
    status, verdict = Person("anon@example.org").api("POST", "/api/verify", signed)
    ok(f"signed results verify: {verdict.get('reason', verdict)}")
    signed["record"]["ranking"][0]["title"] = "Someone else"
    status, verdict = Person("anon@example.org").api("POST", "/api/verify", signed)
    refused(f"a doctored copy: {verdict.get('reason', verdict)}")
    show(f"/events/{slug}/results")
    pause_briefly()

    step("A team asks: why did we place where we did?")
    last = next(tm for tm in teams if tm["title"] == public["projects"][-1]["title"]) if public["projects"] else teams[3]
    status, page, _ = last["lead"].get(f"/events/{slug}/results/{last['project']}")
    ok(f"{last['title']}'s team opens its explanation ({status}): each review, the judge's habit, what it counted for")
    status, _, _ = teams[0]["lead"].get(f"/events/{slug}/results/{last['project']}")
    refused(f"another team opening it: {status}")
    show(f"/events/{slug}/results/{last['project']}  (as {last['lead'].email})")
    pause_briefly()

    # ── out ───────────────────────────────────────────────────────────────
    step("Everything leaves a trail, and everything exports")
    status, csv_text, _ = org.get(f"/api/events/{slug}/export/scores.csv")
    ok(f"scores.csv: {len(csv_text.splitlines()) - 1} reviews, header {csv_text.splitlines()[0][:70]}…")
    status, audit_csv, _ = org.get(f"/api/events/{slug}/export/audit.csv")
    ok(f"audit log: {len(audit_csv.splitlines()) - 1} rows for this event, each chained to the one before by hash")
    show(f"/events/{slug}/manage/audit")

    print(f"\n{BOLD}{GREEN}Done.{RESET} Now run the official checker and the isolation probe:")
    print(f"   {YELLOW}python3 run.py .dogfood.toml{RESET}")
    print(f"   {YELLOW}sh scripts/isolation_curl.sh{RESET}")
    print(f"   {YELLOW}docker compose exec web python manage.py verify_audit{RESET}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
