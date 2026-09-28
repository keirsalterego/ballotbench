# Architecture

ballotbench is one Django application in front of one Postgres database,
started by one `docker compose up`. Pages are rendered on the server; the
JSON API sits next to them and answers the same questions with the same
rules. There is no JavaScript framework, no queue, no cache and no outside
service, because a hackathon portal has hundreds of users, not millions, and
every moving part is something a volunteer organizer has to keep running.

```
browser ──► gunicorn :8080 ──► Django ──► Postgres 18
curl    ─┘   (web container)    │         (db container)
                                ├─ pages   portal/views, participant, judge, organizer, progress, results, oversight
                                ├─ API     portal/api, judge (api_*), results (api_results), exports
                                └─ rules   portal/access  +  triggers in the database
```

## Components

| Module | What it does |
|-|-|
| `models.py` | the schema, with its unique and check constraints |
| `migrations/0002-0005` | the triggers: deadline, team rules, judging rules, audit chain |
| `access.py` | roles, the scoped querysets every view starts from, the status-code rules, the deadline check on the database clock |
| `auth.py` | bearer tokens (hashed), for the API and, via a middleware, the pages |
| `audit.py` | writes one audit row per change, in the change's transaction |
| `importer.py`, `commands/seed.py` | the idempotent fixture import and the demo accounts |
| `participant.py` | teams, invite links, the project form |
| `judge.py` | the judge's queue, the scoresheet, recusal, the judging API |
| `organizer.py` | event settings, tracks, prizes, rubric, role invites |
| `assignment.py` | the review planner, a pure function (see JUDGING.md) |
| `progress.py` | applying plans, manual changes, the progress dashboard |
| `calibration.py` | the calibration model, bootstrap intervals, the agreement test, a pure module |
| `results.py` | calibration runs, publishing, the results pages and API |
| `oversight.py` | audit log page, duplicate resolution, the exports page |
| `exports.py` | CSV exports |
| `duplicates.py` | duplicate submission detection |

The two pieces with real logic, the planner and the calibration, take plain
tuples and return plain objects. They know nothing about Django, so they're
tested on thousands of random inputs in milliseconds, and the database layer
around them is thin.

## A request, end to end

`GET /api/judge/scores?judge=jdg_24` with judge B's token:

1. gunicorn hands the request to Django. `ATOMIC_REQUESTS` opens a
   transaction for the whole request.
2. DRF runs `BearerTokenAuthentication`: SHA-256 the token, look up the hash,
   refuse a revoked token or an inactive user (401).
3. `api.judge_scores` resolves `jdg_24` to a user. It isn't the caller, and
   the caller organizes no event, so it raises `PermissionDenied`: **403**.
   A name that matches nobody also gets 403, so the answer doesn't reveal
   which judges exist.
4. Without `?judge=`, the queryset starts from
   `access.judge_assignments(user)`: the caller's own assignments, in events
   where they still hold a judge membership. Reviews are filtered from that,
   never looked up by id.

`POST /api/events/sample-hack-2026/projects` with the participant's token:

1. Authenticated as above.
2. The caller needs a team in the event, or it's **403**.
3. `access.submissions_closed_reason` asks the database for `now()` against
   the event's window. Past the close: **409** with the close time.
4. If it were open, the write happens inside `access.guarded()`, a savepoint
   that turns a trigger's refusal into a 409 or 422. The deadline trigger
   checks the same clock again, so a request that races the deadline by a
   millisecond is still refused.
5. An audit row is written in the same transaction.

## Where each rule is enforced, and why there

| Rule | App (for the message) | Database (for the guarantee) |
|-|-|-|
| deadline | `submissions_closed_reason`, on the DB clock | `project_deadline` trigger |
| a judge sees only their own work | querysets start from `judge_assignments(user)`; naming another judge is 403 | assignments can't exist for non-judges or own-team projects |
| one team per person, team size | form checks | unique constraint, `team_member_rules` trigger with a row lock |
| no judging your own team | planner skips it | `assignment_rules`, `team_member_zz_not_judge`, `membership_judge_not_member` |
| scores in range | form and API validation | `score_rules` trigger |
| rubric frozen once scored | page hides the inputs | `rubric_frozen` trigger |
| audit log can't change | no update code exists | `audit_readonly`, `audit_no_truncate`, the hash chain |
| results hidden until published, and while voting is open | `results.visible_run`, `voting.public_tallies`; `results.publish` refuses while voting is open | (read rule; no write to guard) |
| a vote: window, budget, own team, eligible project, confirmed and not voided | `voting.cast` | `vote_rules` trigger, locking the voter row |
| one ballot per account and per inbox | `voting.normalize_email`, `request_link` | unique constraints on `portal_voter` |
| rate limits | `ratelimit.allow`, counted in `portal_ratehit` | (shared across workers because it is in the database) |

The database is the last word because a portal has more write paths than
anyone remembers: pages, the API, the admin, a management command, a shell
at 2 a.m. during the event. The app's checks are for clear error messages;
the triggers are for when someone forgets.

## Status codes

The same everywhere, pages and API:

- **400**: the request is malformed: a required field missing, a wrong type,
  a track from another event.
- **401**: no credentials, or a bad token, on a route that needs them. Pages
  redirect a browser to the login page instead.
- **403**: you're signed in but your role can't do this, or you named another
  person's data (`?judge=`).
- **404**: the object exists but isn't yours to see: another judge's
  assignment, another team's draft, unpublished results. Saying 403 would
  confirm it exists.
- **409**: the window for this action is closed, or a conflict of interest.
- **422**: values out of range.

## Trade-offs

- **Server-rendered pages, not a SPA.** Fewer moving parts, works without
  JavaScript, one place for the rules. The cost is less interactivity, which
  a judging form doesn't need.
- **Triggers in SQL.** They're less familiar to many Django developers than
  model methods, and they tie us to Postgres. In exchange, the rules hold for
  every write path, including ones that don't exist yet. Each trigger's
  migration explains it in a docstring.
- **The calibration in pure Python, no numpy.** A hackathon has tens of judges
  and hundreds of reviews: a fit takes 16 ms and 200 bootstrap refits take
  about 3 seconds. One dependency fewer in the image.
- **No email leaves the box.** The portal must run offline, so invite links
  are shown once to the person who makes them, to send however they like.
  The one thing that has to be mailed, a voter's confirmation link, lands in
  an outbox table that site admins read in the admin. A real deployment sets
  `DJANGO_EMAIL_BACKEND` to SMTP.
- **Sessions and tokens side by side.** Browsers use sessions (with CSRF);
  scripts and the checker use bearer tokens (no cookies, so no CSRF risk).
  Pages accept tokens too so that the isolation probe tests what a browser
  gets.
- **Single process.** gunicorn with three workers, no background worker.
  Nothing in the portal is slow enough to need one; calibration runs inside
  the organizer's request.

## Beyond T2 (tier T4)

| Module | What it does |
|-|-|
| `signing.py` | the Ed25519 key (one file, made on first use with mode 0600), canonical JSON, sign and verify; a pure module |
| `records.py` | signed participation records for judges and participants, `/verify`, the public key, certificates |
| `embed.py` | the frameable gallery `/embed/<slug>` and `/embed.js` |
| `bundles.py`, `commands/export_event`, `commands/import_event` | whole-event bundles out, and in as a new event through `importer.py` |
| `webhooks.py`, `commands/deliver_webhooks` | the outbox sender, the address checks, the organizer's page |

- **Records are checkable without us.** What's signed is the record's
  canonical JSON (sorted keys, no spaces, UTF-8), so the Python snippet on
  `/verify` checks one with nothing but the public key. A record never holds
  a score: judges' scores stay private even from the people they judged.
- **Framing is opt-in per view.** `X_FRAME_OPTIONS = "DENY"` stays the
  default; only the two embed views are exempt, and the embed renders as an
  anonymous visitor whatever cookies arrive, so it can't leak a draft into
  someone else's page. The iframe reports its height with `postMessage`, and
  `embed.js` accepts it only from that iframe and the portal's origin.
- **Webhooks are the one background worker.** `audit.record` queues a
  `WebhookDelivery` next to the audit row, in the same transaction, so the
  outbox and the log can't disagree. A separate `webhooks` service (same
  image) sends them: it claims a round, one due delivery per webhook, by
  leasing them under `SKIP LOCKED` row locks (so two senders never claim the
  same one), commits, and sends them all at once with nothing locked. The
  web process never makes an outbound request.
- **SSRF.** A webhook URL must resolve only to public addresses, when it's
  saved and again at every send, and the connection goes to the address that
  was checked (no second DNS lookup to rebind), with one 10 s deadline for
  the whole attempt and no redirects.
