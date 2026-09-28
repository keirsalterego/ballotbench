# Reading the calibration page

The calibration page is where an organizer turns a pile of reviews into a
ranking, and it's the page I'd want open when a team asks why they came
fourth. It's at **Manage → Calibration and results**, or
`/events/<slug>/manage/calibration`. Only the event's organizers and site
admins can open it; anyone else gets a 403.

This chapter goes through it block by block, using the fixture event
(Sample Hack 2026) as it looks straight after `docker compose up`. The
numbers below are from a freshly seeded stack; yours will match until
someone changes a score.

## Running it

The button at the top says **Run calibration** the first time and **Run it
again on the current scores** after that. A run reads every submitted review
that has a score for every criterion; drafts and half-filled scoresheets are
ignored. It fits the model, bootstraps the rank intervals (200 refits) and
runs the agreement test, which takes a few seconds on the fixture.

Every run is kept, and a run is never updated. The page always shows the
latest one. That matters for two reasons: you can run it as often as you
like while judging is still going, and a published result can't change
underneath you, because publishing points at one run and later runs are new
rows. Each run also writes a `calibration.run` row to the audit log with its
fingerprint, the number of reviews, the agreement p-value and the judges it
flagged.

## The run facts

The first block is a short list of facts about the run.

| Line | What it says | On the fixture |
|-|-|-|
| When | the time the run was made (UTC) and who made it | your run |
| Reviews read | submitted, complete reviews the run used | 126 |
| Fingerprint | SHA-256 of exactly the scores the run read | `397dd35f…178f4a` |
| Judges linked | whether every judge can be compared with every other | Yes |
| Agreement | whether the judges agree more than chance would | no (p = 0.70) |

### Fingerprint

The fingerprint is a SHA-256 over the scores the run read, written out in a
fixed order. It's there so that a ranking can be checked against the scores
it claims to come from: if one score changes, the fingerprint changes. The
same fingerprint is stored with the run, shown on the public results page,
returned by the results API as `input_digest`, and written into every row of
`results.csv`.

On a freshly seeded fixture it's always:

```text
397dd35f376eb052b0dfcd8fda8309f46bad4771e9caa4e78035175807178f4a
```

The fingerprint doesn't prove the scores are the ones the judges meant to
give. It proves the ranking came from these scores. For the first question,
the audit log records every `review.save` and `review.submit` with the
before and after values, and `manage.py verify_audit` checks nobody has
edited that log. [Recompute the fingerprint](#recompute-the-fingerprint)
below shows how to check it by hand.

### Judges linked

Calibration compares judges through the projects they share. If the judges
split into groups that share no project, directly or through other judges,
their scales can't be put side by side: a lenient group and a harsh group
look exactly like a strong batch and a weak one.

- **Yes** means every judge is connected. The fixture is.
- **No** is flagged, with the advice to hand out bridging reviews. Open
  **Hand out reviews**: the planner detects the split, adds bridging reviews
  until the groups connect, and says how many it added. Once those reviews
  are in, run calibration again.

### Agreement

This line answers the question to ask before looking at any rank: do the
judges agree about which projects are better any more than chance would? The
test shuffles every score across the same judge-project slots 1000 times and
counts how often a shuffle spreads the project means at least as far apart
as the real scores do. That fraction is the p-value.

- **p below 0.05**: "The judges agree on which projects are better far more
  than chance would." There's a real signal. The ranking still has
  uncertainty, which the "could be" column shows.
- **p of 0.05 or more**: "The judges agree no more than chance would", with a
  flag. The ranking is computed anyway, but most of its order is noise.
- **No reviews yet** if the run read nothing.

The fixture reads p = 0.70 (the page and `normalization_proof` run the same
test: 2000 shuffles, the same seed): its scores behave like independent draws.

When p is high, I would not publish a ranking to three decimals. What I'd do,
roughly in this order:

1. **Treat overlapping ranks as ties.** Read the "could be" column, not the
   rank. If you must name winners, name a group whose ranges sit clearly
   above the rest, and say it is a group.
2. **Get more reviews.** The signal grows with reviews per project. Raise k
   in the event settings, hand out more reviews, then run it again.
3. **Look at the flagged judges** (next section). Nine discordant judges out
   of thirty, as on the fixture, says the judges weren't reading the rubric
   the same way, or the rubric doesn't separate the projects.
4. **Say so when you announce.** "The judges couldn't separate projects 4 to
   20" is a fair thing to tell teams, and the public results page already
   shows each project's range.

Calibration can take out a judge's leniency and scale. It can't create
agreement that isn't in the scores, and the page says that in as many words.

## Judges the model couldn't learn from

This table lists judges whose reviews carry no weight in the calibrated
scores: their fitted scale is exactly 0, which removes them from every
project's score as if they hadn't reviewed. Their reviews still count in the
raw means and in each project's review count. Each row shows the judge's
name (or email), how many reviews they wrote, and why.

| Flag | The page says | What it means | What to do |
|-|-|-|-|
| constant | Gave every project the same score, so their scores say nothing about which is better. | every review has the same weighted score | ask them; if they misread the rubric, have them rescore, otherwise top up their projects with another judge |
| single review | Only one review: there's nothing to compare it with. | one review can't reveal a judge's leniency or scale | give them more reviews that overlap with other judges |
| discordant | Their scores don't rise with everyone else's on the projects they share. | their fitted scale came out at zero or below | read their comments first; more overlapping reviews settle it either way |

On the fixture, the page lists twelve judges. It shows names; the fixture ids
are here so you can match them to `fixtures.json` and to the method chapter:

| Fixture id | Name on the page | Reviews | Flag |
|-|-|-|-|
| `jdg_07` | Iva Petrova | 3 | constant |
| `jdg_01` | Tomas Varga | 1 | single review |
| `jdg_23` | Anya Sokolova | 1 | single review |
| `jdg_04` | Noor Haddad | 4 | discordant |
| `jdg_08` | Marek Nowak | 3 | discordant |
| `jdg_10` | Hiro Tanaka | 3 | discordant |
| `jdg_14` | Emeka Adeyemi | 3 | discordant |
| `jdg_17` | Bruno Costa | 2 | discordant |
| `jdg_18` | Lars Berg | 3 | discordant |
| `jdg_19` | Mira Kaur | 4 | discordant |
| `jdg_21` | Sana Aziz | 4 | discordant |
| `jdg_27` | Leila Nasser | 2 | discordant |

**`jdg_07` gave 4 on every criterion of all three projects.** A judge who
gives everyone the same score tells you nothing about which project is
better, so the model gives them no weight (a per-judge z-score would divide
by zero here). The side effect is on the projects they reviewed: one of
them, `prj_19` (Small Relay), has only two reviews, so its calibrated score
rests on the other judge alone.

**`jdg_01` and `jdg_23` wrote one review each.** With one review there is
nothing to separate a judge's leniency from the project's quality, so they
get no weight either. `jdg_01`'s one review is of `prj_07`, which matters in
the first worked example below.

**Discordant is not an accusation.** It means that on the two to four
projects a judge shared with others, their scores went down, or stayed flat,
while everyone else's went up. With so few reviews, one honest disagreement
can do that. On the fixture it's mostly noise, which fits the agreement line.
The flags are recomputed on every run, so a judge can move between ok and
discordant as reviews come in.

Each judge's fitted numbers (offset, scale, noise and flag) are in the
**Judges** export, `judges.csv`.

## The ranking

Below the judges is the ranking itself, one row per project that has at
least one review in the run. Projects with no reviews at all don't appear.

| Column | What it is |
|-|-|
| Rank | the calibrated rank among ranked projects; ties, which are rare, go to the higher raw mean |
| Could be | the 90% bootstrap range for the rank, "a to b" |
| Raw rank | the rank by plain mean of weighted scores, with "up n" or "down n" |
| Project | the title, linked to the project page, and any flag |
| Reviews | submitted, complete reviews of this project in the run, flagged judges included |
| Raw mean | the plain mean of those reviews' weighted scores, 0 to 1 |
| Calibrated | the calibrated score, mapped back onto the rubric's 0 to 1 scale |

### Rank and calibrated score

The calibrated score is the model's estimate of each project's quality with
every judge's leniency and scale taken out. It's put back on the 0 to 1 scale
(the average project, plus so many standard deviations of the project means)
so it reads like a rubric score, but it's only comparable within one run. A
project seen by few judges, or by noisy ones, is pulled towards the middle
rather than trusted on thin evidence.

### Could be

This is the column I'd read first. For each run, every project's reviews are
resampled with replacement and the whole model refitted, 200 times, and the
column shows the range the project's rank falls in 90% of the time. A narrow
range means the scores place the project consistently; a wide one means they
can't tell it from its neighbours. Where two projects' ranges overlap, the
judges couldn't really separate them.

On the fixture, most ranges are wide (half of them span 27 places or more),
which is the agreement line again, seen project by project.

One caution: the bootstrap can only resample the reviews that exist. A
project with two reviews has only three possible resamples, so a narrow range
on a low-coverage project is less reassuring than it looks.

### Raw rank, up and down

The raw rank orders the same projects by their plain mean. The label next to
it compares the two: "up 30" means the project is 30 places higher after
calibration than on raw means, "down 4" means 4 places lower, and no label
means it didn't move. A big move with a narrow "could be" is calibration
doing its job: a project that drew a harsh judge, say. A big move with a wide
"could be" is the model reshuffling noise.

### Low coverage

A project with fewer reviews in the run than the event's k (reviews per
project, 3 by default) is flagged **low coverage**. On the fixture, eight
projects from the two unfinished batches have two reviews each:

| Project | Title | Reviewers | Rank | Could be | Raw rank |
|-|-|-|-|-|-|
| `prj_10` | Still Beacon | `jdg_15`, `jdg_29` | 16 | 5 to 20 | 3, down 13 |
| `prj_15` | Copper Orbit | `jdg_13`, `jdg_10` (discordant) | 19 | 5 to 32 | 12, down 7 |
| `prj_18` | Open Kiln | `jdg_24`, `jdg_04` (discordant) | 26 | 5 to 36 | 19, down 7 |
| `prj_19` | Small Relay | `jdg_29`, `jdg_07` (constant) | 23 | 18 to 29 | 13, down 10 |
| `prj_24` | Glass Beacon | `jdg_18`, `jdg_19` (both discordant) | left out | | |
| `prj_29` | Flat Relay | `jdg_24`, `jdg_09` | 18 | 3 to 36 | 30, up 12 |
| `prj_39` | Paper Anchor | `jdg_26`, `jdg_18` (discordant) | 28 | 1 to 33 | 20, down 8 |
| `prj_40` | Slow Loom | `jdg_26`, `jdg_24` | 30 | 21 to 37 | 37, up 7 |

Most of them moved towards the middle. That is the shrinkage working: two
reviews, several of them from judges who carry no weight, aren't enough
evidence to keep a project near the top or the bottom. `prj_10` had the
third-best raw mean on two reviews and ends up 16th, with a range of 5 to 20.

To clear the flag, open **Hand out reviews**. With k = 3, the plan for the
fixture is exactly one top-up for each of the eight. The flag stays until
the new reviews are submitted and you run calibration again.

### Left out

Some projects are in the table but not ranked. They're at the bottom, greyed
out, with **left out:** and a reason. Their reviews still help calibrate the
judges who wrote them; the project just doesn't get a rank.

| Reason | When |
|-|-|
| duplicate | the project is flagged as a duplicate of an earlier one and nobody has put it back |
| not submitted | the project has reviews but is a draft |
| no usable reviews | every one of its reviews is from a judge who carries no weight |

On the fixture there are two:

- **`prj_41` Dry Harbour, left out: duplicate.** Team CopperLedger submitted
  Dry Harbour twice, with the same title and repository: `prj_07` on 1 March
  at 04:29 and `prj_41` three minutes before the deadline. The import flags
  the later one. Its four reviews still count towards calibrating their
  judges. Open **Duplicates** to confirm it or put it back; if you put it
  back, run calibration again and it's ranked like any other project.
- **`prj_24` Glass Beacon, left out: no usable reviews.** Both of its
  reviews are from discordant judges (`jdg_18` and `jdg_19`), so the model has
  nothing to go on. The calibrated score it shows is just the average
  project, and means nothing. It needs a review from another judge.

## Two worked examples

**`prj_07` Dry Harbour is ranked first, and I wouldn't announce it.** Its raw
rank is 31, so calibration moved it up 30 places. It has five reviews, but
two are from discordant judges (`jdg_19`, `jdg_21`) and one is `jdg_01`'s
single review, so its calibrated score rests on two judges, `jdg_12` and
`jdg_26`, who both scored it well relative to how they scored everything
else. Its "could be" is 1 to 39: nearly the whole field. On this data, first
place means "the two judges who count liked it", not "it won".

**`prj_11` Salt Ledger and `prj_34` Iron Switch are the ones I'd trust.**
They had the best raw means (raw rank 1 and 2) and calibration moved each of
them down four places, to 5 and 6. But their ranges are among the narrowest
on the page: 1 to 16 and 2 to 12. Whatever the resampling does, the data
keeps putting them near the top. If I had to name a shortlist from the
fixture, I would build it from the ranges, not the ranks.

## Publishing

Until results are published, the results page and the results API answer
404 to everyone except the event's organizers, who see the latest run with a
"Preview" banner.

**Publish results** freezes the public results to the run on this page:

- It records the time (on the database's clock) and the run on the event,
  and writes a `results.publish` audit row with the run and its fingerprint.
- `/events/<slug>/results` becomes public. It shows each ranked project's
  rank, title, team, track, calibrated score, number of reviews, the range it
  could have landed in, and the fingerprint. Left-out projects aren't listed.
  Judge names and flags are never public.
- `/api/events/<slug>/results` becomes public too, with the same projects
  plus each one's raw rank, raw mean and standard error (see
  [the API](../reference/api.md#results)).
- Running calibration again afterwards changes nothing public. The page then
  offers **Publish run n instead**, so moving the public result to a newer
  run is always a deliberate act, and it's audited.
- **Hide the results again** takes them down (`results.unpublish` in the
  audit log). Both pages go back to 404 for everyone but organizers.

One thing to watch: the **Results** and **Judges** exports always describe
the latest run, not the published one. Their `run` column says which run a
file came from.

Before I press publish I check, in order: judges linked, no low-coverage
flags left, duplicates decided, the flagged judges looked at, and what the
agreement line says. The last one decides how I word the announcement.

## Recompute the fingerprint

Anyone with the scores export can check a fingerprint. The export is
organizer only, so in practice an organizer downloads it and hands it to
whoever wants to check: a team, another organizer, a hackathon judge.

This is what a run hashes, from `run_calibration` in
[`src/portal/results.py`](https://github.com/keirsalterego/ballotbench/blob/main/src/portal/results.py)
and `digest` in
[`src/portal/calibration.py`](https://github.com/keirsalterego/ballotbench/blob/main/src/portal/calibration.py):

- one row per submitted review with every criterion scored, as
  `[review_id, judge_email, project_id, [[criterion_key, value], ...]]`, the
  pairs sorted by criterion key;
- the rows sorted, which puts them in `review_id` order;
- serialized with `json.dumps(rows, separators=(",", ":"))` and hashed with
  SHA-256.

Every piece of that is a column in `scores.csv`: `review_id`, `judge_email`,
`project_id` (the portal's numeric id, not the fixture's `prj_` id), and one
column per criterion, headed by its key. A review the run skipped has an
empty `submitted_at` or an empty `weighted_0_1`.

```sh
curl -s -H "Authorization: Bearer bb_demo_organizer_5c1e0a" \
  -o scores.csv http://localhost:8080/api/events/sample-hack-2026/export/scores.csv
python3 fingerprint.py scores.csv
```

```python
# fingerprint.py: recompute a calibration run's input digest from scores.csv
import csv, hashlib, json, sys

FIXED = {"review_id", "judge_email", "project_id", "project", "submitted_at", "weighted_0_1", "comment"}

with open(sys.argv[1], newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    criteria = [c for c in reader.fieldnames if c not in FIXED]
    rows = []
    for r in reader:
        if not r["submitted_at"] or not r["weighted_0_1"]:
            continue  # drafts and incomplete reviews aren't read
        rows.append([int(r["review_id"]), r["judge_email"], int(r["project_id"]),
                     sorted([c, int(r[c])] for c in criteria)])

canon = json.dumps(sorted(rows), separators=(",", ":"))
print(hashlib.sha256(canon.encode()).hexdigest())
```

On a freshly seeded stack this prints `397dd35f…178f4a`, the fixture's
fingerprint. If it doesn't match a run's fingerprint:

- **A score changed after the run.** That's what the fingerprint is for. The
  audit log says which review, when, and who. The fixture's judging window has
  no close date, so its judges can still change scores.
- **A judge's email changed.** The export uses the current address.
- **A cell was escaped.** The export puts a quote in front of any cell that
  starts with `=`, `+`, `-` or `@`, so a spreadsheet won't run it as a
  formula. Criterion keys and emails normally never start with those; if one
  does, strip the leading `'` first.

Compare with the fingerprint of the run you care about: the published one is
`input_digest` in the results API, the latest one is on this page and in
`results.csv`.
