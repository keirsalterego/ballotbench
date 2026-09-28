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
results are frozen to), `reviews_per_project` (k), `max_team_size`.
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
`duplicate_of`, which is organizer bookkeeping.

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
