# ballotbench

A self-hosted hackathon portal for submissions and judging, built for
[Dogfood 2026](https://dogfoodhack.com). Teams submit projects, judges score
them against a weighted rubric, and the organizer gets a ranking in which
every judge's habits (leniency, how widely they spread marks) are taken out,
with an honest range for every rank. Deadlines, judge isolation and the audit
log are enforced by Postgres itself, not just by the pages in front of it.

```text
T1  gallery is public ................. PASS
T1  project from fixtures shown ....... PASS
T1  closed event refuses submissions .. PASS
T2  judge sees own scores ............. PASS
T2  judge cannot see peer scores ...... PASS
T2  participant blocked ............... PASS
T2  csv export works .................. PASS

claimed T1 T2, verified T1 T2
```

That's [`acceptance-report.txt`](acceptance-report.txt), produced by the
organizers' `run.py` against the Docker build.

## Run it

You need Docker with the Compose plugin.

```sh
git clone https://github.com/keirsalterego/ballotbench.git
cd ballotbench
docker compose up
```

Open <http://localhost:8080>. The first boot builds the image, creates the
database, loads the fixture event and prints the demo logins. After the
images are built it needs no network: no CDN, no web fonts from elsewhere,
no email service, no hosted anything. To prove it:

```sh
docker compose down -v
docker compose -f docker-compose.yml -f docker-compose.offline.yml up
```

### Demo logins

Password for all of them: `ballotbench-demo`.

| Who | Email | API header |
|-|-|-|
| Admin | `admin@ballotbench.local` | |
| Organizer | `organizer@ballotbench.local` | `Authorization: Bearer bb_demo_organizer_5c1e0a` |
| Judge A (`jdg_24`) | `diego.herrera@example.org` | `Authorization: Bearer bb_demo_judge_a_8d24f1` |
| Judge B (`jdg_29`) | `ines.rocha@example.org` | `Authorization: Bearer bb_demo_judge_b_3a9e77` |
| Participant | `priya1@example.org` | `Authorization: Bearer bb_demo_participant_61b0c4` |

These are public, so they are for the demo only: set `BALLOTBENCH_DEMO_SEED`
to `0` in `docker-compose.yml` and they are never created.

Two events are seeded:

- **Sample Hack 2026** is the fixture event on its real dates. Submissions
  closed on 1 March 2026, so it refuses new ones. Its 126 reviews are loaded,
  so calibration and results work straight away.
- **Demo Hack (open)** is open for submissions for 14 days from the first
  boot, for trying the whole flow: join, form a team, invite someone, submit.
  Its judging window and its community vote (signed-in accounts, 25 credits
  each) open after that; as organizer, move either in Settings to try them
  now.

### Check it yourself

```sh
python3 run.py .dogfood.toml                                      # the official checker
sh scripts/isolation_curl.sh                                      # 79 attempts at things you shouldn't reach
docker compose exec web python manage.py normalization_proof      # every number in JUDGING.md, recomputed
docker compose exec web python manage.py verify_audit             # the audit log's hash chain
```

## What it does

**Participants** sign up, join an event, start a team and share single-use
invite links, then draft, edit and submit a project until the deadline.
After the deadline, the page, the API and the database all refuse changes.

**Judges** see only their own assignments. They score each project on a
scoresheet (one bubble per criterion), save drafts, submit, and step aside if
they have a conflict.

**Organizers** create events with dates, tracks, prizes and a weighted
rubric; invite judges and co-organizers by link; hand out reviews with a
planner that balances load and never gives a judge their own team; watch a
live progress dashboard; run calibration and read which judges it couldn't
learn from and why; resolve duplicate submissions; publish results frozen to
one run; read the audit log; and export every stage as CSV.

**Voters** rank the projects in a community vote, if the organizer turns one
on: signed-in accounts, or anyone who confirms an email address by a link
(one ballot per inbox, so `a.b+x@googlemail.com` and `ab@gmail.com` count
once). Ballots are quadratic: n votes for a project cost n² of a fixed
budget. Each voter sees the projects in their own random order and can't
vote for their own team, and nobody sees a tally until voting has closed and
the organizer publishes. Organizers see the tallies as they come in, next to
an abuse panel that flags networks with many voters, brand-new accounts and
identical ballots, and they can void a ballot with a reason. Nothing is
voided automatically.

**Anyone signed in** comments on submitted projects; the event's organizers
can hide a comment, and it disappears for everyone else.

**Anyone** browses and searches the public gallery, and reads results once
they're published.

## What's worth a look

- **The deadline is enforced by a trigger** on the database's clock, so it
  holds for the API, the pages, the admin and a shell alike. The app checks
  first so it can answer 409 with the close time.
- **Judge isolation is in the queries.** Every judge query starts from the
  judge's own assignments. Another judge's review is a 404, naming another
  judge is a 403, and `scripts/isolation_curl.sh` tries it all over HTTP.
- **Calibration you can defend.** A reviewer-calibration model fits each
  judge's leniency, scale and noise. Shifting or stretching any judge's scores
  changes nothing (to 1e-15); a judge who gives everyone the same score counts
  exactly as if absent. Every rank comes with a bootstrap range, and each run
  tests whether the judges agree more than chance. On the fixture they don't,
  and the portal says so rather than printing a confident ranking of noise.
  [JUDGING.md](JUDGING.md) has the maths and the proof.
- **The fixture's traps are handled visibly**: the constant judge `jdg_07`,
  the single-review judges, the eight projects from unfinished batches, and
  the duplicate `prj_41`.
- **The vote's rules are in the database too.** A trigger locks the voter's
  row and checks the window, the budget (sum of votes² within the credits),
  own team and eligible projects, so two tabs can't overspend a ballot. Rate
  limits (ballots, email links, comments, logins, sign-ups) are counted in
  Postgres, so every worker shares them. [THREAT-MODEL.md](THREAT-MODEL.md)
  lists who attacks a portal like this and what stops them.
- **A hash-chained audit log** that the database won't let anyone edit, and a
  command that names the first tampered row if a superuser does.
- **Results you can check.** Each calibration run stores the SHA-256 of the
  exact scores it read.

## Documents

| | |
|-|-|
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, a request end to end, where each rule is enforced and why |
| [DATA-MODEL.md](DATA-MODEL.md) | every table, its constraints and triggers, how data gets in and out |
| [JUDGING.md](JUDGING.md) | assignment, scoring maths, calibration, its guarantees and limits |
| [THREAT-MODEL.md](THREAT-MODEL.md) | who attacks a hackathon portal, how, and what does and doesn't stop them |
| [NOTES.md](NOTES.md) | build notes: what broke and what I changed my mind about |
| `/api/docs` | the API, generated from the same OpenAPI document as `/api/schema` |

## Develop

```sh
python3 -m venv .venv && .venv/bin/pip install -r src/requirements-dev.txt
docker run -d -p 5434:5432 -e POSTGRES_USER=ballotbench -e POSTGRES_PASSWORD=ballotbench postgres:18.4
cd src
export DJANGO_DEBUG=1 POSTGRES_PORT=5434 BALLOTBENCH_DEMO_SEED=1
../.venv/bin/python manage.py migrate && ../.venv/bin/python manage.py seed
../.venv/bin/python manage.py runserver 8080
cd .. && POSTGRES_PORT=5434 .venv/bin/python -m pytest     # tests run on real Postgres: the triggers are under test
```

## What it doesn't do yet

- **No webhooks, certificates, signed records or embeddable widget** (T4).
  The REST API and its OpenAPI document are there.
- **No email leaves the box.** The portal runs offline, so invite links are
  shown once to whoever creates them, and voting links land in an outbox
  table that site admins read in the admin. Set `DJANGO_EMAIL_BACKEND` to
  Django's SMTP backend to really send them.
- **Sign-up doesn't verify email ownership.** Logins and sign-ups are rate
  limited per address, but an account-mode vote is only as strong as the
  sign-up: someone with many addresses can make many accounts. The abuse
  panel flags new accounts and shared networks; it doesn't stop them.
- **Email-mode votes are one per inbox, not one per person.** Someone with
  ten real inboxes (or a domain with a catch-all) gets ten ballots. There is
  no CAPTCHA, no phone check and no proof of personhood; the organizer's
  judgement, helped by the flags, is the last line.
- **Rate limits are per client address**, the same address the audit log
  records (`audit.client_ip`). Behind a proxy that address must be the
  visitor's, not the proxy's, or every visitor shares one limit.
- **Calibration assumes linear judges.** It can't correct a judge who only
  compresses the top of the scale, and it can't detect judges who collude.
  See [known limits](JUDGING.md#10-known-limits).
- **Deleting a closed event** is a management command (`delete_event`), not a
  button, because the database refuses to delete submitted projects after the
  deadline.

## License

[MIT](LICENSE).
