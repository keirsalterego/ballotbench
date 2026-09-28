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
  Its judging window opens after that; as organizer, move it in Settings.

### Check it yourself

```sh
python3 run.py .dogfood.toml                                      # the official checker
sh scripts/isolation_curl.sh                                      # 83 attempts at things you shouldn't reach
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
- **A hash-chained audit log** that the database won't let anyone edit, and a
  command that names the first tampered row if a superuser does.
- **Results you can check.** Each calibration run stores the SHA-256 of the
  exact scores it read.

## Beyond T2 (tier T4)

Not claimed in `.dogfood.toml`, because `run.py` can't check it, but built and
tested (`tests/test_records.py`, `test_embed.py`, `test_bundles.py`,
`test_webhooks.py`) and probed by `scripts/isolation_curl.sh`:

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
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, a request end to end, where each rule is enforced and why |
| [DATA-MODEL.md](DATA-MODEL.md) | every table, its constraints and triggers, how data gets in and out |
| [JUDGING.md](JUDGING.md) | assignment, scoring maths, calibration, its guarantees and limits |
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

- **No public voting or comments yet** (tier T3). I'm not claiming T3.
- **Tier T4 isn't claimed**, though everything in
  [Beyond T2](#beyond-t2-tier-t4) works. The signing key can't be rotated
  yet: a new key makes records signed by the old one fail to verify.
- **No email.** The portal runs offline, so invite links are shown once to
  whoever creates them. A real deployment would add an SMTP backend.
- **Sign-up doesn't verify email ownership**, and there is **no login rate
  limit**. Put it behind a proxy with TLS and rate limiting if it's public.
- **Calibration assumes linear judges.** It can't correct a judge who only
  compresses the top of the scale, and it can't detect judges who collude.
  See [known limits](JUDGING.md#10-known-limits).
- **Deleting a closed event** is a management command (`delete_event`), not a
  button, because the database refuses to delete submitted projects after the
  deadline.

## License

[MIT](LICENSE).
