# Data model

Postgres 18. Every rule that must hold whatever code path writes a row lives
in the database: foreign keys, unique and check constraints, and triggers.
The application checks the same rules first so it can answer with a clear
409 or 422, but it doesn't have to be right for the data to stay right.
Models are in `src/portal/models.py`; triggers are in the migrations
`0002` to `0005`.

## Tables

### People and access

**`portal_user`**: an account. Signs in with `email` (unique, and a check
constraint keeps it lowercase). `is_staff` is the global admin role; every
other role is per event.

**`portal_apitoken`**: `user`, `label`, `token_hash` (unique), `created_at`,
`revoked_at`. Only the SHA-256 of a token is stored; the token itself is shown
once.

**`portal_membership`**: `user`, `event`, `role` (participant, judge or
organizer), `tracks` (many-to-many; for judges, the tracks they may review,
empty meaning any), `external_id`.
- unique `(user, event, role)`
- unique `(event, role, external_id)`: fixture judge ids like `jdg_24`
- trigger `membership_judge_not_member`: nobody becomes a judge of an event
  they're on a team in

**`portal_roleinvite`**: single-use link that grants judge or organizer in one
event. `token_hash` (unique), `expires_at`, `used_by`, `used_at`, `tracks`.
Check: `used_by` set only with `used_at`.

### Events

**`portal_event`**: `slug` (unique), `name`, `external_id` (unique),
`submissions_open/close`, `judging_open/close`, `voting_open/close`,
`results_published_at`, `published_run` (the calibration run the public
results are frozen to), `voting_mode` (off, account or email),
`vote_credits` (each voter's quadratic budget), `reviews_per_project` (k),
`max_team_size`.
- checks: each window opens before it closes; k ≥ 1; team size ≥ 1

**`portal_track`**: `event`, `name`, `external_id`. Unique `(event, name)`
and `(event, external_id)`.

**`portal_prize`**: `event`, optional `track`, `name`, `description`. Unique
`(event, name)`.

### Teams and projects

**`portal_team`**: `event`, `name`, `external_id`. Unique
`(event, external_id)`. Names are **not** unique: the fixture has three
different teams called StillTrail.

**`portal_teammember`**: `team`, `event`, `user`, `joined_at`.
- unique `(event, user)`: one team per person per event
- trigger `team_member_rules`: copies the team's event into `event` (so the
  unique constraint means what it says), locks the team row, and refuses a
  member past `max_team_size`. The lock makes two simultaneous invite
  acceptances queue rather than both squeeze in.
- trigger `team_member_zz_not_judge`: a judge of the event can't join a team
  in it

**`portal_teaminvite`**: `team`, `token_hash` (unique), `created_by`,
`expires_at`, `used_by`, `used_at`. Single use, 72 hours or until submissions
close, whichever is first.

**`portal_project`**: `event`, `team`, `track`, `title`, `tagline`,
`summary`, `description`, `repo_url`, `demo_url`, `tags` (text array),
`status` (draft or submitted), `submitted_at`, `duplicate_of` (self),
`duplicate_cleared` (an organizer said it isn't one; never flagged again),
`external_id`.
- unique `(event, external_id)`
- check: submitted ⇔ `submitted_at` set
- check: not its own duplicate
- index `(event, status)` for the gallery
- trigger `project_same_event`: team and track belong to the project's event
- trigger `project_deadline`: see below

### Judging

**`portal_rubriccriterion`**: `event`, `key`, `name`, `weight`,
`min_value`, `max_value`, `position`.
- unique `(event, key)`; checks: weight > 0, min < max
- trigger `rubric_frozen`: once any score exists in the event, no criterion
  can be added, deleted, reweighted or re-ranged. Renaming is allowed.

**`portal_judgeassignment`**: `event`, `judge`, `project`, `status`
(pending, done, recused), `source` (seed, auto, manual).
- unique `(judge, project)`
- trigger `assignment_rules`: the project is in the event, the judge has a
  judge membership in it, and the judge isn't on the project's team

**`portal_review`**: one per assignment (`assignment` unique). `comment`,
`submitted_at` (null while a draft).

**`portal_score`**: `review`, `criterion`, `value`.
- unique `(review, criterion)`
- trigger `score_rules`: the criterion belongs to the review's event and the
  value is inside its range
- `criterion` is `ON DELETE RESTRICT`: a scored criterion can't be deleted
  alone, but an event can be deleted whole

### Calibration

**`portal_calibrationrun`**: `event`, `created_at`, `created_by`, `method`,
`params` (JSON: priors and the rubric weights used), `input_digest` (SHA-256
of exactly the scores read), `connected`, `converged`, `signal_p`,
`n_reviews`.

**`portal_calibratedproject`**: `run`, `project`, `n_reviews`, `raw_mean`,
`quality` (q), `display` (q on the 0..1 scale), `se`, `rank`, `raw_rank`,
`rank_low`, `rank_high` (90% bootstrap interval), `excluded` (why it isn't
ranked: duplicate, not submitted, no usable reviews). Unique `(run, project)`.

**`portal_judgecalibration`**: `run`, `judge`, `n_reviews`, `offset`,
`scale`, `noise`, `flag` (ok, constant, single_review, discordant). Unique
`(run, judge)`.

A run is never updated. Publishing points `event.published_run` at one.

### Community voting and comments

**`portal_voter`**: one ballot in one event. `event`, `user` (account mode)
or `email` as typed plus `email_normalized` (email mode), `token_hash` (the
SHA-256 of the one-time confirmation link), `confirmed_at`, `ip`,
`user_agent`, `voided_at`, `voided_reason`.
- unique `(event, user)` and `(event, email_normalized)`: one ballot per
  account and per inbox
- checks: an account or an address; a voided ballot has a reason

**`portal_vote`**: `voter`, `project`, `votes` (≥ 1; a zero is no row).
Unique `(voter, project)`.
- trigger `vote_rules` (migration 0012): locks the voter row, then refuses
  the write outside the voting window (`BB409`), for a voided or unconfirmed
  voter (`BB409`), for a project that isn't a submitted, non-duplicate
  project of the voter's event (`BB422`), for the voter's own team
  (`BB423`), or when the sum of votes² would pass `vote_credits` (`BB409`).

**`portal_comment`**: `project`, `author`, `body`, `created_at`, `hidden_at`,
`hidden_by`.
- checks: body not empty and at most 2000 characters

**`portal_ratehit`**: `key`, `created_at`. One counted action for a rate
limit; `ratelimit.allow()` deletes a key's rows once they leave its window.

**`portal_outboundemail`**: `to`, `subject`, `body`, `created_at`. Mail the
portal would send, kept for site admins to read (`portal.mail.OutboxBackend`).

### The audit log

**`portal_auditlog`**: `seq`, `ts`, `actor`, `event`, `action`,
`object_type`, `object_id`, `before` and `after` (JSON), `ip`, `prev_hash`,
`row_hash`.
- trigger `audit_append` (BEFORE INSERT): takes a transaction-scoped advisory
  lock, sets `seq` to the next number, `ts` to the clock, `prev_hash` to the
  previous row's hash, and `row_hash = sha256(prev_hash | seq | ts | actor |
  event | action | object | before | after | ip)`.
- triggers `audit_readonly` and `audit_no_truncate`: UPDATE, DELETE and
  TRUNCATE are refused.
- `actor` and `event` have no foreign-key constraint on purpose: deleting a
  user or an event must not delete, or be blocked by, its history.
- `manage.py verify_audit` recomputes the chain and names the first row that
  doesn't fit.

Why `seq` and not the primary key: ids are handed out by a sequence before the
trigger takes its lock, so two concurrent inserts can commit in the opposite
order to their ids. `seq` is assigned under the lock.

## The deadline, in the database

`project_deadline` runs before every INSERT, UPDATE and DELETE on
`portal_project`. If the database clock (`now()`) is past the event's
`submissions_close`, it refuses a new project, a deleted one, or any change to
content (title, text, links, tags, track, team, status). It allows changes to
`duplicate_of` and `duplicate_cleared`, which are organizer bookkeeping.

The one bypass is the session setting `ballotbench.import`, which only the
fixture importer and `delete_event` set, with `SET LOCAL` so it ends with
their transaction, and both write an audit row saying so.

## Refusals and what the API returns

Triggers raise with their own SQLSTATE, which `access.guarded()` maps to a
status code:

| SQLSTATE | Meaning | HTTP |
|-|-|-|
| `BB409` | a window is closed (deadline, frozen rubric) | 409 |
| `BB410` | team is full | 409 |
| `BB423` | conflict of interest | 409 |
| `BB422` | an inconsistent reference (wrong event, out-of-range score) | 422 |
| `BB403` | the audit log is append only | 403 |

## Getting data in

- **The fixture**: `manage.py seed` on every boot. Imported rows keep their
  fixture ids in `external_id`, with `UNIQUE (event, external_id)`, so a
  second run inserts nothing and never overwrites what people changed.
  Fixture projects come in as submitted, through the import bypass, because
  they were submitted before a close date that has passed.
- **Another event in the fixture's shape**: `portal.importer.import_event(data)`
  is the same importer; `manage.py seed --fixtures path.json` loads one.

## Getting data out

- **CSV**, organizer only, audited, at every stage: registrations, teams,
  projects, assignments, scores (each criterion and the weighted score),
  results (raw and calibrated, rank intervals, the run's digest), judges
  (per-judge calibration), audit (with the hash chain).
  `GET /api/events/<slug>/export/<kind>.csv`.
- **JSON**: everything the pages show is in the API (`/api/docs`).
- **The database itself**: plain Postgres, `pg_dump` works.

## Beyond T2 (tier T4)

**`portal_webhook`**: `event`, `url`, `secret` (64 hex characters; it keys
the HMAC, so it's stored as is, shown once and never written to the audit
log), `actions` (text array of action prefixes, empty meaning every change),
`active`, `created_by` (who it sends on behalf of: it pauses once they no
longer organize the event, and whoever resumes it takes it over), `created_at`.

**`portal_webhookdelivery`**: the outbox. `webhook`, `action`, `payload`
(JSON: the audit row's action, event, actor, object, before and after),
`status` (pending, delivered, failed), `attempts`, `next_attempt_at`
(defaults to the database clock; while a sender has it claimed, the end of
its lease), `last_error`, `created_at`. Index
`(status, next_attempt_at)` for the sender. A row is written by
`audit.record` in the change's transaction, so it rolls back with it. Failed
sends wait 30 s, then twice as long each time; the eighth failure is final.

**Signed records** aren't stored: each is built from the tables above when
asked for, and the issuance is an audit row (`record.issue`). The signing key
is a file (`BALLOTBENCH_SIGNING_KEY_FILE`, `/data/signing_key.pem` in
Docker), not a table, so a database dump doesn't carry it.

**Event bundles** (`bundles.py`) are the fixture's shape plus `format`,
`prizes`, `criteria` (with weights and ranges), `assignments` (those without
a review) and, per row, everything the fixture leaves out: event dates,
judges' tracks, every project field with `status` and `duplicate_of`, and
each review's `submitted` flag, time, status and comment. Rows are named by
`external_id`, or `prj_<pk>` and the like. Importing one as a new event goes
through `import_event(..., new=True)` with the deadline bypass, in one
transaction; URLs, emails and every enum are validated first, and the bypass
is switched off again when the import ends rather than when the request does.
