# The API

The JSON API sits next to the pages and answers the same questions with the
same rules: every lookup goes through the same scoped querysets in
`portal/access.py`, and every write goes through the same database triggers.
Anything a page can tell you, the API can too, with one exception: the
organizer's tools (settings, invitations, handing out reviews, calibration,
duplicates) are pages only.

Every example below is a real request against the demo stack on
`http://localhost:8080`, with the demo tokens from `.dogfood.toml`. The
read-only ones you can paste as they are. The ones that write change the
demo data, so run them on a stack you're happy to reset with
`docker compose down -v`.

## Authentication

### Bearer tokens

Scripts authenticate with a token in the `Authorization` header:

```sh
curl -H "Authorization: Bearer bb_demo_judge_a_8d24f1" http://localhost:8080/api/judge/scores
```

A token is `bb_` followed by 43 random characters. The portal stores only
its SHA-256, so a token is shown once, when it's made, and a leaked database
doesn't leak working tokens. A token acts as its user, with all of that
user's roles; there are no scopes. A revoked token, or one whose user has
been deactivated, is refused.

The demo seed creates four tokens with fixed values, so the checker and this
book can use them. They're public, which is why a real deployment turns
them off (see [Running it for real](operations.md#demo-accounts)).

| Who | Token | Roles |
|-|-|-|
| organizer | `bb_demo_organizer_5c1e0a` | organizer of both seeded events |
| judge_a | `bb_demo_judge_a_8d24f1` | `jdg_24` in the fixture event, judge of the open demo event |
| judge_b | `bb_demo_judge_b_3a9e77` | `jdg_29` in the fixture event |
| participant | `bb_demo_participant_61b0c4` | team NorthKiln in the fixture event, participant in the demo event |

`judge_a` and `judge_b` share no project, so each has scores the other must
not see. There's no page for making tokens yet; [Running it for
real](operations.md#api-tokens) shows how to issue one from a shell.

### Browser sessions

A browser that's signed in can call the API with its session cookie. Then
Django's CSRF protection applies: a `POST` or `PATCH` needs the
`X-CSRFToken` header with the value of the `csrftoken` cookie, or it's
refused with 403. Bearer requests need no CSRF token, because a browser
never attaches an `Authorization` header on its own, so a forged cross-site
request can't carry one.

### Pages accept tokens too

The HTML pages accept the same bearer tokens. A `GET` with a token sees
exactly what that user would see in a browser, and no session is created.
This is what lets the isolation probe test the pages with `curl`. Page
forms still need a CSRF token, so scripts that write should use the API. A
bad token on a page gets a plain-text 401.

## Status codes

The same contract holds on the pages and in the API.

| Code | When | Example |
|-|-|-|
| 200, 201 | it worked; 201 when a project was created | |
| 400 | the request body is malformed: a missing required field, a wrong type, a track from another event | `{"title": ["This field is required."]}` |
| 401 | no credentials, or a bad token, on a route that needs them (API responses carry `WWW-Authenticate: Bearer`; pages send a browser to the login page instead) | `{"detail": "invalid or revoked token"}` |
| 403 | you're signed in but your role can't do this, or you named someone else's data | `{"detail": "judges can only read their own scores"}` |
| 404 | the thing doesn't exist, or it exists but isn't yours to see: another judge's assignment, another team's draft, unpublished results | `{"detail": "Not found."}` |
| 409 | the window for this is closed, or a conflict of interest | `{"detail": "Submissions for Sample Hack 2026 closed at 2026-03-01 18:00 UTC."}` |
| 422 | the values are wrong: a score out of range, an unknown criterion, submitting a review with a criterion missing | `{"detail": "Quality must be between 1 and 5"}` |

The line between 403 and 404 is deliberate. If you may know a thing exists
but can't touch it (another team's submitted project, which is in the
public gallery), you get 403. If you may not even know it exists (another
judge's assignment, another team's draft), you get 404, since a 403 would
confirm it's there.

A 409 or 422 can come from the app's own check or from a database trigger.
The app checks first so it can give a clear message; the trigger is the
backstop, and `access.guarded()` turns its refusal into the same status
code. Errors are always JSON: `{"detail": "..."}`, or a field-by-field
object for a 400.

## Endpoints

| Method | Path | Who may call it |
|-|-|-|
| GET | [`/api/events`](#list-events) | anyone |
| GET | [`/api/events/<slug>`](#one-event) | anyone |
| GET | [`/api/events/<slug>/projects`](#list-an-events-projects) | anyone; drafts only for their team and organizers |
| POST | [`/api/events/<slug>/projects`](#create-a-project) | a member of a team in the event |
| GET | [`/api/projects/<id>`](#one-project) | anyone who can see the project |
| PATCH | [`/api/projects/<id>`](#edit-or-submit-a-project) | the project's team |
| GET | [`/api/judge/assignments`](#your-assignments) | judges |
| POST | [`/api/judge/assignments/<id>/review`](#score-an-assignment) | the judge the assignment belongs to |
| GET | [`/api/judge/scores`](#your-scores) | judges, for themselves; organizers, for their events' judges |
| GET | [`/api/events/<slug>/results`](#results) | anyone once published; before that, the event's organizers |
| GET | [`/api/events/<slug>/export/<kind>.csv`](#csv-exports) | the event's organizers |
| GET | [`/api/schema`](#the-openapi-document-and-the-reference-page) | anyone |
| GET | [`/api/docs`](#the-openapi-document-and-the-reference-page) | anyone |

Site admins (`is_staff`) count as organizers of every event.

## Events

### List events

`GET /api/events`: every event, the latest deadline first, with its
windows, tracks and rubric. No authentication.

```sh
curl http://localhost:8080/api/events
```

```json
[
  {
    "slug": "demo-open",
    "name": "Demo Hack (open)",
    "...": "..."
  },
  {
    "slug": "sample-hack-2026",
    "name": "Sample Hack 2026",
    "description": "",
    "submissions_open": "2026-02-26T00:00:00Z",
    "submissions_close": "2026-03-01T18:00:00Z",
    "judging_open": "2026-03-01T18:00:00Z",
    "judging_close": null,
    "voting_open": null,
    "voting_close": null,
    "results_published_at": null,
    "reviews_per_project": 3,
    "max_team_size": 4,
    "tracks": [{"id": 1, "name": "Developer tools"}, {"id": 2, "name": "Data and analytics"}, "..."],
    "criteria": [
      {"key": "functionality", "name": "Functionality", "weight": "1.000", "min_value": 1, "max_value": 5},
      {"key": "quality", "name": "Quality", "weight": "1.000", "min_value": 1, "max_value": 5},
      {"key": "innovation", "name": "Innovation", "weight": "1.000", "min_value": 1, "max_value": 5}
    ]
  }
]
```

All times are UTC. A `null` close means the window never closes. Weights are
decimals, sent as strings so they don't lose precision.

### One event

`GET /api/events/<slug>`: the same object for one event, or 404.

```sh
curl http://localhost:8080/api/events/sample-hack-2026
```

## Projects

A project looks like this in every response:

```json
{
  "id": 1,
  "event": "sample-hack-2026",
  "team": "NorthKiln",
  "track": 4,
  "title": "Glass Signal",
  "tagline": "",
  "summary": "One line of what it does.",
  "description": "",
  "repo_url": "https://example.org/repo/01",
  "demo_url": "",
  "tags": [],
  "status": "submitted",
  "submitted_at": "2026-02-27T04:08:00Z",
  "duplicate_of": null
}
```

`track` is a track id from the event. `status` is `draft` or `submitted`.
`duplicate_of` is the id of the earlier project this one repeats, if it's
been flagged; flagged projects stay listed but aren't ranked.

### List an event's projects

`GET /api/events/<slug>/projects`: the event's submitted projects, in id
order, for anyone. Signed in, you also see your own team's drafts, and an
organizer sees every draft in the event.

```sh
curl http://localhost:8080/api/events/sample-hack-2026/projects
```

On the fixture that's 41 projects, including the duplicate `prj_41` (id 41,
`"duplicate_of": 7`).

### One project

`GET /api/projects/<id>`: one project, if you can see it. Another team's
draft is a 404, not a 403.

```sh
curl http://localhost:8080/api/projects/1
```

### Create a project

`POST /api/events/<slug>/projects`: creates a project for your team in that
event. You need to be on a team in the event; teams are made on the event's
page, since the API has no team routes.

| Field | Type | Notes |
|-|-|-|
| `title` | string | required |
| `tagline`, `summary`, `description` | string | optional |
| `repo_url`, `demo_url` | URL | optional |
| `tags` | list of strings | trimmed, lowercased and de-duplicated; at most 10 |
| `track` | track id | must be a track of this event |
| `submit` | boolean | `true` submits it; otherwise it's saved as a draft |

| Outcome | Code |
|-|-|
| created | 201, with the project |
| not signed in | 401 |
| no team in this event | 403 `you need a team in this event to submit a project` |
| before the window opens or after it closes | 409, with the time |
| a bad field | 400 |

Each call makes a new project; a team can hold more than one (the fixture's
team CopperLedger has two). When a project is submitted, the portal checks
it against the event's earlier projects and flags it if it repeats one.

This is the request the acceptance checker makes. The fixture event closed
on 1 March 2026, so it's refused:

```sh
curl -X POST http://localhost:8080/api/events/sample-hack-2026/projects \
  -H "Authorization: Bearer bb_demo_participant_61b0c4" \
  -H "Content-Type: application/json" \
  -d '{"title": "late", "summary": "x"}'
```

```json
{"detail": "Submissions for Sample Hack 2026 closed at 2026-03-01 18:00 UTC."}
```

with status 409. On the open demo event, once the participant has started a
team on the event's page, the same call creates a draft:

```sh
curl -X POST http://localhost:8080/api/events/demo-open/projects \
  -H "Authorization: Bearer bb_demo_participant_61b0c4" \
  -H "Content-Type: application/json" \
  -d '{"title": "Night Owl Radio", "tagline": "Offline-first radio for field teams",
       "summary": "Mesh radio notes.", "repo_url": "https://example.org/night-owl",
       "tags": ["radio", "mesh"]}'
```

```json
{
  "id": 42,
  "event": "demo-open",
  "team": "Night Owls",
  "track": null,
  "title": "Night Owl Radio",
  "tagline": "Offline-first radio for field teams",
  "summary": "Mesh radio notes.",
  "description": "",
  "repo_url": "https://example.org/night-owl",
  "demo_url": "",
  "tags": ["radio", "mesh"],
  "status": "draft",
  "submitted_at": null,
  "duplicate_of": null
}
```

### Edit or submit a project

`PATCH /api/projects/<id>`: changes the fields you send and leaves the rest.
Only members of the project's team may; send `"submit": true` to submit it.
`submitted_at` is set from the database's clock, and there's no way to
unsubmit through the API.

| Outcome | Code |
|-|-|
| saved | 200, with the project |
| you can't see the project | 404 |
| not signed in | 401 |
| you can see it but it isn't your team's | 403 `only the project's team can edit it` |
| the submission window is closed | 409 |
| a bad field | 400 |

```sh
curl -X PATCH http://localhost:8080/api/projects/42 \
  -H "Authorization: Bearer bb_demo_participant_61b0c4" \
  -H "Content-Type: application/json" \
  -d '{"demo_url": "https://example.org/night-owl/demo", "submit": true}'
```

```json
{"id": 42, "...": "...", "demo_url": "https://example.org/night-owl/demo",
 "status": "submitted", "submitted_at": "2026-09-28T05:13:51.891600Z", "duplicate_of": null}
```

After the deadline, editing your own project is a 409, and editing another
team's is a 403 (`prj_02` is another team's):

```sh
curl -X PATCH http://localhost:8080/api/projects/1 \
  -H "Authorization: Bearer bb_demo_participant_61b0c4" \
  -H "Content-Type: application/json" -d '{"title": "late"}'
# 409 {"detail": "Submissions for Sample Hack 2026 closed at 2026-03-01 18:00 UTC."}

curl -X PATCH http://localhost:8080/api/projects/2 \
  -H "Authorization: Bearer bb_demo_participant_61b0c4" \
  -H "Content-Type: application/json" -d '{"title": "mine"}'
# 403 {"detail": "only the project's team can edit it"}
```

## Judging

### Your assignments

`GET /api/judge/assignments`: your own assignments in every event you
judge, and nobody else's. 403 `judges only` if you judge no event.

```sh
curl -H "Authorization: Bearer bb_demo_judge_a_8d24f1" http://localhost:8080/api/judge/assignments
```

```json
[
  {"id": 16, "event": "sample-hack-2026", "project": 6, "project_title": "Dry Compass", "status": "done"},
  {"id": 38, "event": "sample-hack-2026", "project": 12, "project_title": "Open Beacon", "status": "done"},
  "..."
]
```

`status` is `pending`, `done` or `recused`. Stepping aside from a project
(recusal) is on the scoresheet page, not in the API.

### Score an assignment

`POST /api/judge/assignments/<id>/review`: saves your scores for one of your
own assignments.

| Field | Type | Notes |
|-|-|-|
| `scores` | object | criterion key to an integer in that criterion's range |
| `comment` | string | optional, up to 5000 characters; organizers see it, other judges never do |
| `submit` | boolean | `true` submits; otherwise it's a draft. Submitting needs every criterion. |

| Outcome | Code |
|-|-|
| saved | 200 |
| not your assignment, whatever the id | 404 |
| judging hasn't opened, or has closed | 409, with the time |
| you stepped aside from this project | 409 |
| a score out of range, an unknown criterion, or submitting with one missing | 422 |
| a score that isn't an integer | 400 |

You can save and resubmit as often as you like while judging is open. Every
save is in the audit log with the scores before and after.

```sh
curl -X POST http://localhost:8080/api/judge/assignments/16/review \
  -H "Authorization: Bearer bb_demo_judge_a_8d24f1" \
  -H "Content-Type: application/json" \
  -d '{"scores": {"functionality": 3, "quality": 4, "innovation": 5}, "comment": "Clear demo.", "submit": true}'
```

```json
{"assignment": 16, "submitted_at": "2026-09-28T05:13:51.974376Z", "weighted_total": 0.75}
```

`weighted_total` is the review's score on the 0 to 1 scale (see
[the method](../judging/method.md#2-a-reviews-score)). The fixture event's
judging window has no close, so this really does change the fixture's
scores, and with them the calibration fingerprint.

The same assignment, as another judge, doesn't exist:

```sh
curl -X POST http://localhost:8080/api/judge/assignments/16/review \
  -H "Authorization: Bearer bb_demo_judge_b_3a9e77" \
  -H "Content-Type: application/json" -d '{"scores": {"quality": 5}}'
# 404 {"detail": "No JudgeAssignment matches the given query."}
```

### Your scores

`GET /api/judge/scores`: your own reviews, drafts included, with every
criterion's score and the weighted total.

```sh
curl -H "Authorization: Bearer bb_demo_judge_a_8d24f1" http://localhost:8080/api/judge/scores
```

```json
[
  {
    "assignment": 16,
    "event": "sample-hack-2026",
    "project": 6,
    "project_title": "Dry Compass",
    "judge": "diego.herrera@example.org",
    "status": "done",
    "submitted_at": "2026-03-01T18:00:00Z",
    "comment": "Runs clean.",
    "scores": {"functionality": 2, "quality": 3, "innovation": 5},
    "weighted_total": 0.5833333333333334
  },
  "..."
]
```

Two query parameters:

- `event=<slug>` keeps one event's reviews.
- `judge=<judge>` names a judge, by fixture id (`jdg_24`), email or user id.
  A judge may name only themselves. Naming anyone else is a 403, never an
  empty list, so a refusal can't be mistaken for "no scores"; and a name
  that matches nobody is a 403 too, so the answer doesn't reveal which
  judges exist. An organizer may name any judge and gets that judge's
  reviews in the events they organize (404 `no such judge` if the name
  matches nobody).

| Caller | Result |
|-|-|
| a judge, no `judge=` | 200, their own reviews |
| a judge naming another judge, or nobody | 403 `judges can only read their own scores` |
| someone who judges nothing, no `judge=` | 403 `judges only` |
| an organizer naming a judge | 200, that judge's reviews in the organizer's events |
| no credentials | 401 |

The acceptance checker's peer probe:

```sh
curl -H "Authorization: Bearer bb_demo_judge_b_3a9e77" "http://localhost:8080/api/judge/scores?judge=jdg_24"
# 403 {"detail": "judges can only read their own scores"}

curl -H "Authorization: Bearer bb_demo_organizer_5c1e0a" \
  "http://localhost:8080/api/judge/scores?judge=jdg_24&event=sample-hack-2026"
# 200, jdg_24's eleven fixture reviews
```

## Results

`GET /api/events/<slug>/results`: the ranked results. Until they're
published this is a 404 for everyone except the event's organizers, who get
the latest calibration run (with `"published_at": null`). After publishing,
everyone gets the published run, and later runs change nothing here.

```sh
curl http://localhost:8080/api/events/sample-hack-2026/results
# 404 {"detail": "Not found."} until an organizer publishes

curl -H "Authorization: Bearer bb_demo_organizer_5c1e0a" http://localhost:8080/api/events/sample-hack-2026/results
```

```json
{
  "event": "sample-hack-2026",
  "published_at": null,
  "run": 1,
  "input_digest": "397dd35f376eb052b0dfcd8fda8309f46bad4771e9caa4e78035175807178f4a",
  "method": "offset-scale-noise/v1",
  "projects": [
    {
      "rank": 1,
      "raw_rank": 31,
      "project": 7,
      "title": "Dry Harbour",
      "team": "CopperLedger",
      "calibrated": 0.8321,
      "se": 0.0304,
      "raw_mean": 0.5833,
      "reviews": 5,
      "rank_interval": [1, 39]
    },
    "..."
  ]
}
```

That's the fixture after one calibration run and before publishing. Only
ranked projects are listed; duplicates and projects with no usable reviews
are left out. `rank_interval` is the 90% bootstrap range the page shows as
"could be"; `se` is the model's own standard error, which is narrower
because it treats every judge's habits as known. `input_digest` is the
fingerprint of the scores the run read ([how to check
it](../judging/reading.md#recompute-the-fingerprint)).

## CSV exports

`GET /api/events/<slug>/export/<kind>.csv`: one export per stage of the
event, for its organizers. 401 without credentials, 403 for anyone else,
404 for a kind that doesn't exist. Every download writes an `export.csv`
row to the audit log.

```sh
curl -H "Authorization: Bearer bb_demo_organizer_5c1e0a" \
  http://localhost:8080/api/events/sample-hack-2026/export/scores.csv | head -3
```

```text
review_id,judge_email,project_id,project,submitted_at,functionality,quality,innovation,weighted_0_1,comment
1,marek.nowak@example.org,1,Glass Signal,2026-03-01T18:00:00+00:00,2,4,2,0.4167,Runs clean.
3,pavel.ivanov@example.org,1,Glass Signal,2026-03-01T18:00:00+00:00,2,5,3,0.5833,Solid.
```

| Kind | One row per | Columns |
|-|-|-|
| `registrations` | membership | user_email, name, role, tracks |
| `teams` | team member | team_id, external_id, team, member_email, joined_at |
| `projects` | project, drafts included | project_id, external_id, title, team, track, status, submitted_at, repo_url, demo_url, tags, duplicate_of |
| `assignments` | assignment | assignment_id, judge_email, project_id, project, status, source, created_at |
| `scores` | review, drafts included | review_id, judge_email, project_id, project, submitted_at, one column per criterion, weighted_0_1, comment |
| `results` | project in the latest run | run, rank, rank_low, rank_high, raw_rank, project_id, project, reviews, raw_mean, calibrated, se, excluded, input_digest |
| `judges` | judge in the latest run | run, judge_email, reviews, offset, scale, noise, flag |
| `audit` | audit row for the event | seq, ts, actor, action, object_type, object_id, ip, before, after, prev_hash, row_hash |

The file is served as `text/csv` with a download name like
`sample-hack-2026-scores.csv`. Any cell that starts with `=`, `+`, `-` or
`@` (and isn't a number) gets a leading `'`, so a spreadsheet won't run a
project title as a formula. `results` and `judges` always describe the
latest run, published or not; the `run` column says which.

## The OpenAPI document and the reference page

`GET /api/schema` serves the OpenAPI 3.0 document, generated by
drf-spectacular from the same views, so it can't drift from the code. It's
YAML by default; ask for JSON with `?format=json` or `Accept:
application/json`.

```sh
curl http://localhost:8080/api/schema                 # YAML
curl "http://localhost:8080/api/schema?format=json"   # JSON
```

`GET /api/docs` is a reference page rendered on the server from that same
document. It needs no JavaScript and no CDN, so it works on a stack with no
network. Both are public.

The schema lists each route's success response only. The error codes are in
this chapter and in each route's description.
