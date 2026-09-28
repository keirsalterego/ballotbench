![ballotbench: hackathon judging you can defend](docs/banner.jpg)

# ballotbench

A self-hosted hackathon portal for submissions and judging, built for
[Dogfood 2026](https://dogfoodhack.com). Teams submit projects, judges score
them against a weighted rubric, and the organizer gets a ranking in which
every judge's habits (leniency, how widely they spread marks) are taken out,
with an honest range for every rank. Deadlines, judge isolation and the audit
log are enforced by Postgres itself, not just by the pages in front of it.

**[Watch the five-minute demo](https://youtu.be/Z8NRxI5cFX4)**: one whole
event, from set-up to signed results. **[Read the docs](https://keirsalterego.github.io/ballotbench/)**:
guides for organizers, participants and judges, the judging maths, the API.

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
no email service, no hosted anything. To prove it, switch off your Wi-Fi
and run `docker compose up`: it still comes up, seeded, on
<http://localhost:8080>. The [quickstart](https://keirsalterego.github.io/ballotbench/guide/quickstart.html#with-the-network-off)
has a stricter check with no route out of the containers at all.

### Demo logins

Password for all of them: `ballotbench-demo`.

| Who | Email | API header |
|-|-|-|
| Admin | `admin@ballotbench.local` | |
| Organizer | `organizer@ballotbench.local` | `Authorization: Bearer bb_demo_organizer_5c1e0a` |
| Judge A (`jdg_24`) | `diego.herrera@example.org` | `Authorization: Bearer bb_demo_judge_a_8d24f1` |
| Judge B (`jdg_29`) | `ines.rocha@example.org` | `Authorization: Bearer bb_demo_judge_b_3a9e77` |
| Participant | `priya1@example.org` | `Authorization: Bearer bb_demo_participant_61b0c4` |

These are public, so they are for the demo only. For a real event, set
`BALLOTBENCH_DEMO_SEED` to `0` in `docker-compose.yml`: then nothing demo is
loaded (no accounts, no fixture event, no demo event), and you make the first
admin with `docker compose exec web python manage.py createsuperuser`.

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
sh scripts/demo.sh --fresh --pause                                # a whole event, narrated (--fresh wipes the database)
python3 run.py .dogfood.toml                                      # the official checker (T1, T2)
python3 scripts/check_t3_t4.py .dogfood.toml                      # the same style of checks for T3 and T4
sh scripts/isolation_curl.sh                                      # tries to reach what it shouldn't, and checks public pages stay public
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
on: signed-in accounts that have confirmed their address, or anyone who
confirms an email address by a link
(one ballot per inbox, so `a.b+x@googlemail.com` and `ab@gmail.com` count
once). Ballots are quadratic: n votes for a project cost n² of a fixed
budget. Each voter sees the projects in their own random order and can't
vote for their own team, and nobody sees a tally until voting has closed and
the organizer publishes. While voting is open organizers see how many
ballots are in, not the tallies, next to an abuse panel that flags networks
with many voters, brand-new accounts and identical ballots, and they can
void a ballot with a reason, for good. Nothing is voided automatically.

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
- **"Why did we come fourth?" gets an answer.** After results are published,
  each team can open a page that shows every review of its project with the
  judge anonymized: what that judge gave them, what that judge gives a typical
  project, and how much the review counted, including why some counted for
  nothing. [How it works](JUDGING.md#12-explaining-a-rank-to-the-team).
- **Extra reviews go where they can change who wins.** After a calibration,
  the assignment page lists the projects whose plausible rank crosses the
  prize line and gives each exactly one more review, instead of spreading
  judge time evenly over projects that can't win and can't miss.
- **A kingmaker check.** Before publishing, the fit runs again without each
  judge in turn and lists anyone whose absence would change the podium.
- **A second opinion no judge's habits can move.** Next to each calibrated
  rank is a Bradley-Terry ranking built only from which of two projects each
  judge scored higher.
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
  exact scores it read, and the published ranking is available as an
  Ed25519-signed document anyone can verify offline, so the portal can be held
  to what it published.

## T3 and T4, checked the same way

`run.py` only has checks for T1 and T2, so `.dogfood.toml` claims those two.
T3 and T4 are checked by [`scripts/check_t3_t4.py`](scripts/check_t3_t4.py),
written in the same style: one standard-library file, plain HTTP against
the running portal, the same `.dogfood.toml`. It prints the exact request
and status behind every result, and CI runs it on every push. Its output is
committed as [`t3-t4-report.txt`](t3-t4-report.txt):

```sh
python3 scripts/check_t3_t4.py .dogfood.toml
```

| Spec bullet | Checks |
|-|-|
| **T3** Community voting: email gated, link based or authenticated | authenticated ballots (anonymous 401, an unconfirmed account 403); email-gated ballots through a mailed link that confirms only on a button press; the quadratic budget (26 of 25 credits refused, 25 accepted) |
| Project comments | anonymous 401, signed-in 201, public listing, an organizer hides one and it vanishes for everyone else |
| Results hidden during the voting window | publishing refused with 409 while the vote is open, results 404 to the public and to voters, then published with community votes once it closes |
| Randomized project ordering on ballots | three voters see different orders, each stable across requests, none simply by id |
| Anti abuse: rate limits, duplicate detection, audit trail | one voter's 21st ballot save in ten minutes gets 429; a second spelling of one inbox is refused and logged; the fixture's duplicate submission is flagged; the audit export records votes, hidden comments and publication, every row with its chain hash, and is 403 to a judge |
| **T4** REST API and webhooks | the OpenAPI document covers the voting, records and import routes; a webhook to a private address is refused, a public one is accepted and queues a delivery for the next change; 403 to a participant |
| Certificate and record generation | a judge's certificate carries their signed record; someone else's is 404; a participant's signed record names their team |
| Signed, publicly verifiable judge records | the public Ed25519 key is published; a judge's record verifies and carries no scores; a doctored copy doesn't; judge B asking for judge A's record gets 403 |
| Embeddable gallery widget | `/embed.js` serves JavaScript; the embed lists the projects and is frameable; every other page stays `X-Frame-Options: DENY` |
| Bulk import and export | the bundle export, a site admin importing it as a new event and exporting the same projects and scores back; an organizer importing gets 403; every CSV export |

It creates its own events and accounts (never touching the fixture event),
so run it against a demo stack, not a real event.

## Beyond T2 (tier T4)

`.dogfood.toml` claims T1 and T2, the tiers `run.py` can verify. The public
vote and comments above (T3) and everything in this section (T4) are built
and tested but not claimed, because the checker has no way to confirm them.
T4's tests are `tests/test_records.py`, `test_embed.py`, `test_bundles.py`
and `test_webhooks.py`, and `scripts/isolation_curl.sh` probes it:

- **Signed participation records.** `GET /api/judge/record?event=<slug>`
  (and `/api/participant/record`) returns what you did in an event, never a
  score, signed with the portal's Ed25519 key. Anyone can check one at
  `/verify` or `POST /api/verify`, or offline with the public key at
  `/.well-known/ballotbench-signing-key`. Another judge's record is a 403.
- **Certificates.** `/events/<slug>/certificate` prints to one clean page
  (Save as PDF) and carries the signed record and a link that verifies it.
  Only your own; organizers can open anyone's with `?person=<email>`.
- **An embeddable gallery.** One tag on any page,
  `<script src="http://localhost:8080/embed.js" data-event="sample-hack-2026" async></script>`,
  inserts an iframe of the event's submitted projects (and its results, once
  published) that sizes itself to its content. `/embed/<slug>` is the only
  page any site may frame; everything else stays `X-Frame-Options: DENY`.
- **Event bundles.** `GET /api/events/<slug>/export/bundle.json` is the
  whole event in one file: the fixture's shape plus dates, rubric weights,
  prizes, judges' tracks, drafts and every review. `POST
  /api/events/import?slug=<new>` or `manage.py import_event bundle.json
  --slug <new>` brings it back as a new event. Exported again it's the same
  file, and it calibrates to exactly the same numbers.
- **Webhooks.** Organizers add endpoints under Manage, Webhooks. Every
  audited change is queued in the change's own transaction and POSTed by the
  `webhooks` service with an HMAC-SHA256 signature, retried with backoff.
  Only public addresses, checked when saved and again when sent; set
  `BALLOTBENCH_WEBHOOKS_ALLOW_PRIVATE=1` for a receiver on your own network.

## Documents

| | |
|-|-|
| [The book](https://keirsalterego.github.io/ballotbench/) | everything below plus guides for each role, the API with examples, and running it for a real event |
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
```

In another terminal, from the repository root:

```sh
POSTGRES_PORT=5434 .venv/bin/python -m pytest     # tests run on real Postgres: the triggers are under test
```

## What it doesn't do yet

- **Signed records can't be revoked, and the signing key can't be rotated
  in place.** A new key file means records signed with the old one no longer
  verify against the published key.
- **No email leaves the box.** The portal runs offline, so invite links are
  shown once to whoever creates them, and voting links land in an outbox
  table that site admins read in the admin. Set `DJANGO_EMAIL_BACKEND` to
  Django's SMTP backend to really send them.
- **An account is only as real as its inbox.** Accounts must confirm their
  address before they can vote, and logins, sign-ups and resets are rate
  limited, but someone with many real inboxes can still make many accounts.
  The abuse panel flags new accounts and shared networks; it doesn't stop
  them.
- **Email-mode votes are one per inbox, not one per person.** Someone with
  ten real inboxes (or a domain with a catch-all) gets ten ballots. There is
  no CAPTCHA, no phone check and no proof of personhood; the organizer's
  judgement, helped by the flags, is the last line.
- **Rate limits are per client address**, the same address the audit log
  records. Behind a reverse proxy, set `BALLOTBENCH_TRUSTED_PROXIES` to the
  number of proxies, or every visitor shares the proxy's limit.
- **Calibration assumes linear judges.** It can't correct a judge who only
  compresses the top of the scale, and it can't detect judges who collude.
  See [known limits](JUDGING.md#14-known-limits).
- **Deleting a closed event** is a management command (`delete_event`), not a
  button, because the database refuses to delete submitted projects after the
  deadline.

## License

[MIT](LICENSE).
