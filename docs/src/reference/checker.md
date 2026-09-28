# The acceptance checker

Dogfood's organizers give every team the same checker,
[`run.py`](https://github.com/keirsalterego/ballotbench/blob/main/run.py): a
standard-library Python script that reads a team's `.dogfood.toml`, makes
seven HTTP requests against the running portal, and prints a report of
which tiers it could verify. This chapter explains the contract as
ballotbench meets it, and the stricter probe I wrote to go past it.

## `.dogfood.toml`, line by line

```toml
[portal]
base_url = "http://localhost:8080"

[tiers]
claimed = ["T1", "T2"]
pitch = "A self-hosted hackathon portal whose judging you can defend: weighted rubrics, calibrated judges, and deadlines and isolation enforced in the database."

[auth]
organizer   = "Authorization: Bearer bb_demo_organizer_5c1e0a"
judge_a     = "Authorization: Bearer bb_demo_judge_a_8d24f1"
judge_b     = "Authorization: Bearer bb_demo_judge_b_3a9e77"
participant = "Authorization: Bearer bb_demo_participant_61b0c4"

[routes]
gallery      = "/projects"
submit       = "/api/events/sample-hack-2026/projects"
judge_scores = "/api/judge/scores"
peer_scores  = "/api/judge/scores?judge=jdg_24"
csv_export   = "/api/events/sample-hack-2026/export/scores.csv"
```

| Line | Meaning |
|-|-|
| `base_url` | where the checker sends every request: the port `docker compose up` publishes |
| `claimed` | the tiers this entry claims. T1 is submissions and the gallery, T2 judging and isolation. Public voting (T3) is being built and isn't claimed. |
| `pitch` | a one-line description of the entry; `run.py` doesn't read it |
| `organizer`, `judge_a`, `judge_b`, `participant` | a complete HTTP header for each role. The checker splits it at the first `:` and sends it as is. These are the demo tokens the seed creates when `BALLOTBENCH_DEMO_SEED=1`. |
| `gallery` | the public page listing submitted projects |
| `submit` | where a participant creates a project, here in the fixture event, which is closed |
| `judge_scores` | where a judge reads their own scores |
| `peer_scores` | the URL that would return judge A's scores: `jdg_24` is judge A's id in the fixture |
| `csv_export` | an organizer's CSV export |

The four accounts are chosen so the checks mean something: `judge_a`
(`jdg_24`) and `judge_b` (`jdg_29`) both have fixture reviews and share no
project, so each has scores the other must not see, and the participant is
on a real fixture team (NorthKiln), so the late submission is refused for
being late, not for having no team.

## The seven checks

| Tier | Check | The request | Passes when | What answers it |
|-|-|-|-|-|
| T1 | gallery is public | `GET /projects`, no header | 200 | the gallery view, which lists submitted projects to anyone |
| T1 | project from fixtures shown | the same response | it contains the title of one of the fixture's first three projects | the seed imports the fixture on boot, and the gallery orders page one by id, so Glass Signal, Small Meadow and Deep Compass lead |
| T1 | closed event refuses submissions | `POST` to `submit` as `participant`, with a title and summary | any 4xx | 409 with the close time: the API checks the window on the database's clock, and the `project_deadline` trigger would refuse it anyway |
| T2 | judge sees own scores | `GET judge_scores` as `judge_a` | 200 | `/api/judge/scores` returns the caller's own reviews |
| T2 | judge cannot see peer scores | `GET peer_scores` as `judge_b` | 401 or 403 | 403: a judge may only name themselves, and gets a refusal, never an empty list |
| T2 | participant blocked | `GET judge_scores` as `participant` | 401 or 403 | 403 `judges only` |
| T2 | csv export works | `GET csv_export` as `organizer` | 200, and the first line has a comma | the scores export, whose first line is the CSV header |

The checker is lenient in places (any 4xx for the late submission, 401 or
403 for the peer probe). ballotbench answers with the specific code in each
case; [the API chapter](api.md#status-codes) has the contract.

A tier counts as verified only if every one of its checks passes and every
tier below it is verified too. `run.py` has checks for T1 and T2 only, so
those are the most it can verify.

## Regenerating `acceptance-report.txt`

The report in the repository is the checker's output against the Docker
build on a fresh volume. To make it again, from the repository root:

```sh
docker compose down -v
docker compose up -d --build --wait
python3 run.py .dogfood.toml > acceptance-report.txt
```

Run it from the root so it finds `fixtures.json` (it looks in the current
directory, next to `run.py`, next to the config, and in a `data/` folder
beside the config; `--fixtures path` names it outright). Any Python 3 works:
3.11 and newer read the TOML with `tomllib`, older ones with the script's
own small parser. The last line is the verdict:

```text
claimed T1 T2, verified T1 T2
```

CI does the same on every push to main and fails if that line is anything
else. The checker writes nothing to the portal apart from the audit row
every CSV export leaves; its one write request is refused.

## The isolation probe

The checker tries one peer probe and one participant probe. Passing it
means little on its own: a portal that returned 403 for every judge request
would pass. So
[`scripts/isolation_curl.sh`](https://github.com/keirsalterego/ballotbench/blob/main/scripts/isolation_curl.sh)
tries 68 things over HTTP, each with the exact status it must get back:

```sh
sh scripts/isolation_curl.sh                         # against http://localhost:8080
sh scripts/isolation_curl.sh http://localhost:9000   # or another base URL
```

It needs `sh`, `curl` and `sed`. First it reads the ids it needs with their
owners' own tokens (one of judge A's assignments, a project judge A
reviewed, the participant's project, another team's project), then:

| Group | Attempts | Expected |
|-|-|-|
| judge scores | judge B names judge A by fixture id and by email, and names the constant judge; the participant reads scores and lists assignments; no token; a made-up token; judge A reads their own | 403, 401, and 200 for the last |
| someone else's review | judge B and the participant post scores to judge A's assignment; judge B opens judge A's scoresheet page | 404 |
| deadline | the participant submits and edits after the close; edits another team's project; a judge and an anonymous caller create projects | 409, 403, 401 |
| exports | all eight kinds, anonymously, as the participant, as a judge, and as the organizer | 401, 403, 403, 200 |
| results before publication | anonymous, judge and participant read the fixture's results | 404 |
| organizer pages | a judge's and the participant's tokens on all seven organizer pages | 403 |
| public pages | the gallery, a submitted project, the API schema | 200 |

It prints one line per attempt and a total, and exits with status 1 if
anything came back different:

```text
== judge scores
ok   200 judge_a reads own scores
ok   403 judge_b names judge_a by fixture id
...

68 of 68 as expected
```

Two things to know when running it:

- **It expects the fixture's results to be unpublished.** If you've
  published them on the stack, the three results lines fail, correctly.
  Reset with `docker compose down -v`.
- **It speaks plain HTTP to port 8080**, so it's for the demo stack. A
  deployment with `DJANGO_SECURE=1` redirects it to HTTPS.

Like the checker, a passing run changes nothing: every write it tries is
refused. The organizer's exports leave their audit rows.

The test suite goes further than both (the same rules through the pages, the
API, the admin and raw SQL against a real Postgres), but the probe is the
one to run against a deployment, because it asks the running thing.
