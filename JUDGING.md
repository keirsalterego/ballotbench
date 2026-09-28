# Judging

How ballotbench hands out reviews, turns rubric scores into a ranking, and
why I think the ranking can be defended to a team that didn't win. Every
number on this page comes from `manage.py normalization_proof`, which
recomputes it from the database; the output is in [section 8](#8-the-proof-on-the-fixture).

## 1. Assigning reviews

`portal/assignment.py` is a pure function over plain records, so it's tested
on thousands of random events without a database (`tests/test_assignment.py`).

1. **The idlest judge picks next.** Of the judges who can still help, the one
   with the fewest reviews takes a project. That keeps loads within one review
   of each other wherever eligibility allows.
2. **They take the neediest project they may review.** Fewest reviews so far,
   then fewest judges left who could take it, so scarce judges go where only
   they can help.
3. **Eligibility** is enforced in the planner and again by a database trigger:
   never a project from your own team, only your tracks (a judge with no tracks
   takes any), never a project you already hold or stepped aside from.
4. **Repair.** Greedy can corner itself: at the end, the idle judges may
   already hold the last projects that need someone. A repair pass moves one
   of the plan's new reviews from the busiest judge to the idlest eligible one
   until no move narrows the gap. Existing reviews never move.
5. **Connectivity.** Calibration compares judges through the projects they
   share. If the judge-project graph splits into groups that share nothing,
   their scales can't be compared, so the planner adds bridging reviews
   (union-find over the graph) and the page says how many it added.

The organizer previews the plan, then applies it; on apply it's recomputed
from the database, never taken from the form. Manual changes are allowed, but
a review that has been started can't be taken back, since that would quietly
delete a judge's scores. A judge who spots a conflict steps aside; the project
shows up as needing a top-up.

On the fixture, the plan for k = 3 is exactly the eight top-ups the fixture's
two unfinished batches call for: `prj_10, prj_15, prj_18, prj_19, prj_24,
prj_29, prj_39, prj_40` each have two reviews. The progress dashboard flags
them as low coverage until the new reviews come in.

## 2. A review's score

Each criterion has a weight and a range (1 to 5 by default). A review's score
is the weighted mean of its criteria, each first mapped to 0..1 by its own
range, so a 1-10 criterion and a 1-5 criterion count by their weights, not by
the width of their scales:

```
score = Σ_c w_c · (x_c − min_c) / (max_c − min_c)  /  Σ_c w_c
```

A review missing a criterion has no score and isn't used. The fixture's
scale isn't stated; its values run 2 to 5, so I assume 1 to 5.

**The rubric freezes at the first score.** A trigger refuses any change to
criteria, weights or ranges once the event has a score. Weights tuned after
reading the scores are a way to pick a winner.

## 3. Why not just average, or z-score

- **Raw means** reward drawing a lenient judge. With three reviews per project,
  one generous judge moves a project a long way.
- **Per-judge z-scores** fix leniency but assume every judge saw an average
  batch. A judge who only saw the five best projects gets the best of those
  pushed down to the middle. They also divide by zero for a judge who gives
  the same score every time, which the fixture has (`jdg_07`).

## 4. The model

Each review's score y is modelled as

```
y_ij = a_j + s_j · q_i + e_ij,     e_ij ~ N(0, v_j),     q_i ~ N(0, 1)
```

- `q_i`: project i's quality, the thing we want.
- `a_j`: judge j's leniency (offset).
- `s_j`: how strongly their scores follow quality (scale). A judge who
  separates good from weak sharply has a large `s_j`.
- `v_j`: how noisy they are.

The fit (`portal/calibration.py`) alternates two least-squares steps until q
stops moving (tolerance 1e-12):

```
q_i  = Σ_j s_j (y_ij − a_j) / v_j   /   (1 + Σ_j s_j² / v_j)        then centre and scale q to mean 0, sd 1
s_j  = S_xy / (S_xx + 1),   a_j = ȳ_j − s_j · q̄_j                  ridge least squares on the judge's own projects, s_j ≥ 0
v_j  = (3 · var(y_j)/2 + RSS_j) / (3 + n_j)                         empirical-Bayes shrink of the noise
```

- The `q_i` step is the posterior mean under the N(0, 1) prior: a project seen
  by few or noisy judges is pulled towards the middle instead of trusted
  blindly. That's the answer to the unfinished batches.
- A judge's leniency is anchored by the projects they share with other
  judges, not by their own batch, which is what z-scores get wrong.
- The noise prior stops a judge with three reviews from being fitted exactly
  and then trusted infinitely.
- The **ridge** on `s_j` (the `+ 1`) is there because my first version had
  none, and on the fixture it didn't converge: a judge whose few projects
  landed close together in q got a huge scale, which dragged those projects
  closer, and so on. The penalty is in q's units, which have no scale of
  their own, so it doesn't break the invariances below.

This is the reviewer-calibration model used for NeurIPS reviewing (Lawrence,
2014; Ge, Welling & Ghahramani), fitted by alternating least squares rather
than full Bayesian inference, which is plenty for tens of judges.

### Judges it can't learn from

These get `s_j = 0`. Because every term a judge contributes to `q_i` is
multiplied by `s_j`, a judge with `s_j = 0` drops out **exactly**, as if their
reviews weren't there. Raw means still include them; the calibration page
lists them with the reason.

| Flag | Meaning | On the fixture |
|-|-|-|
| `constant` | every review the same score | `jdg_07` (4 on every criterion, 3 reviews) |
| `single_review` | one review: nothing to compare it with | `jdg_01`, `jdg_23` |
| `discordant` | their scores fall, or don't rise, as everyone else's rise on the projects they share | 9 judges |

### What it guarantees (tested)

- **Shift one judge** (add a constant to all their scores): every `q` is
  unchanged, to 1e-15.
- **Stretch one judge** (multiply their scores by a positive factor): same.
- **Shift and stretch every judge at once**, each differently: same.
- **Remove the constant judge**: every `q` is unchanged, exactly 0.

These hold because every step is equivariant: a judge's `a_j`, `s_j` and
`v_j` absorb any affine change of their own scores, and the ridge and priors
are either in q's units or built from the judge's own spread.

## 5. How sure is the ranking?

The model's own standard error, `1/√(1 + Σ s_j²/v_j)`, assumes each judge's
offset and scale are known exactly. With three or four reviews per judge they
aren't. So each run also bootstraps: resample each project's reviews with
replacement, refit, 200 times, and record where each project lands. The pages
show the 90% range as "could be 4 to 12". Where two projects' ranges overlap,
the judges couldn't really tell them apart, and the page says so rather than
printing a confident third decimal.

## 6. Does the data have a signal at all?

Each run also asks whether the judges agree about which projects are better
more than chance would: it compares the spread of the project means with the
spread after shuffling every score across the same judge-project slots (2000
shuffles). If they don't, no method can produce a meaningful ranking, and the
calibration page says so in plain words.

**On the fixture they don't** (p ≈ 0.70). The fixture's scores look like
independent draws: projects' means spread no more than shuffled scores do, and
nine judges' scores run against the consensus. So the honest result on the
fixture is: the constant and single-review judges are handled, the duplicate is
out, the ranking is computed, and **almost every rank's interval is wide**
(median 28 places). I'd rather show that than a crisp ranking that is noise.

## 7. The fixture's traps

| Trap | What ballotbench does | Where you see it |
|-|-|-|
| `jdg_07` scores 4 on everything | flagged `constant`, weight exactly 0 | calibration page, `judges.csv`, the proof |
| `jdg_01` (and `jdg_23`) have one review | flagged `single_review`, weight 0 | same |
| two unfinished batches: 8 projects with 2 reviews | shrunk towards the middle, wider intervals, "low coverage" flag, 8 top-ups proposed | progress dashboard, assignment preview |
| `prj_41` repeats `prj_07` (same team, title, repo) | detected at import, `duplicate_of` set, out of the rankings until an organizer decides; its reviews still calibrate its judges | duplicates page, gallery badge, audit log |
| judge load 1 to 11 | noise shrinkage trusts busy judges more; new work goes to the idlest | judge table |

## 8. The proof on the fixture

`docker compose exec web python manage.py normalization_proof`, on a fresh seed:

```text
Normalization proof: Sample Hack 2026, 126 reviews, 30 judges, 41 projects
Model: y = a_j + s_j*q_i + e, fitted in 210 iterations (converged), judge-project graph in 1 component(s).
Judges carrying no weight (s_j = 0):
  jdg_07    3 review(s)  constant
  jdg_04    4 review(s)  discordant
  jdg_08    3 review(s)  discordant
  jdg_10    3 review(s)  discordant
  jdg_14    3 review(s)  discordant
  jdg_17    2 review(s)  discordant
  jdg_18    3 review(s)  discordant
  jdg_19    4 review(s)  discordant
  jdg_21    4 review(s)  discordant
  jdg_27    2 review(s)  discordant
  jdg_01    1 review(s)  single_review
  jdg_23    1 review(s)  single_review
  the other 18 judges: ok
Agreement: variance of project means 0.0080, 0.0089 on average when every score is shuffled across the same slots (permutation p = 0.698, 2000 shuffles).
  The judges agree on which projects are better no more than chance would. Calibration takes out
  judge habits; it can't create a signal the scores don't hold, so most rank moves below are noise.
Left out of the ranking: prj_41 (Dry Harbour) repeats prj_07; its 4 reviews still count towards calibrating their judges.
rank  could be  raw  move  project  title             n raw mean calibrated
   1      1-40   32   +31  prj_07   Dry Harbour       5    0.583      0.832
   2      1-34    5    +3  prj_37   Salt Loom         4    0.771      0.804
   3      2-37   23   +20  prj_01   Glass Signal      3    0.611      0.774
   4      2-38    9    +5  prj_08   North Drift       5    0.700      0.762
   5      1-16    1    -4  prj_11   Salt Ledger       4    0.833      0.749
   6      2-12    2    -4  prj_34   Iron Switch       3    0.833      0.746
   7      3-37   15    +8  prj_09   Hollow Signal     3    0.639      0.741
   8      4-37   26   +18  prj_12   Open Beacon       3    0.611      0.739
   9      4-40   27   +18  prj_27   Flat Thread       3    0.611      0.732
  10      2-30    4    -6  prj_25   Dry Relay         3    0.778      0.716
  11      3-16    7    -4  prj_33   Slow Trail        3    0.750      0.709
  12      4-32    8    -4  prj_21   Copper Kiln       3    0.722      0.708
  13      6-35   18    +5  prj_02   Small Meadow      3    0.639      0.686
  14      8-28   17    +3  prj_31   Salt Ferry        3    0.639      0.684
  15      3-36   10    -5  prj_04   Green Switch      3    0.694      0.675
  16      5-20    3   -13  prj_10   Still Beacon      2    0.792      0.666
  17      3-39   22    +5  prj_35   Warm Beacon       5    0.617      0.652
  18      3-37   31   +13  prj_29   Flat Relay        2    0.583      0.651
  19      5-33   12    -7  prj_15   Copper Orbit      2    0.667      0.651
  20     12-32   20        prj_24   Glass Beacon      2    0.625      0.639
  21     11-38   29    +8  prj_03   Deep Compass      3    0.583      0.634
  22      1-30    6   -16  prj_16   Salt Kiln         3    0.750      0.624
  23     11-30   14    -9  prj_36   Salt Drift        3    0.667      0.615
  24     18-30   13   -11  prj_19   Small Relay       2    0.667      0.589
  25      4-28   16    -9  prj_17   Small Loom        3    0.639      0.589
  26     12-34   28    +2  prj_14   Green Lantern     5    0.600      0.582
  27      5-37   19    -8  prj_18   Open Kiln         2    0.625      0.580
  28     22-38   24    -4  prj_28   Flat Meadow       3    0.611      0.579
  29      1-34   21    -8  prj_39   Paper Anchor      2    0.625      0.571
  30      2-36   11   -19  prj_38   Deep Beacon       3    0.694      0.567
  31     21-38   38    +7  prj_40   Slow Loom         2    0.500      0.564
  32      4-39   37    +5  prj_30   Paper Harbour     3    0.528      0.559
  33     19-38   35    +2  prj_06   Dry Compass       3    0.528      0.558
  34     17-40   36    +2  prj_22   Dry Bridge        3    0.528      0.556
  35      4-39   34    -1  prj_26   Amber Hours       3    0.556      0.544
  36      5-39   25   -11  prj_32   Loud Ledger       3    0.611      0.529
  37     12-40   30    -7  prj_13   Quiet Anchor      3    0.583      0.515
  38     14-40   33    -5  prj_20   Paper Thread      3    0.556      0.504
  39     29-40   39        prj_05   North Compass     3    0.472      0.500
  40     25-40   40        prj_23   Slow Quarry       3    0.472      0.490

37 of 40 projects change rank. Kendall's tau, calibrated vs raw: 0.454
'could be' is a 90% bootstrap interval (each project's reviews resampled, 200 refits). Median width 28 places: on these scores most ranks are not distinguishable.
Invariance checks on these scores (max |change in q| over all projects):
  PASS  jdg_24 adds 0.3 to every score: 1.1e-15, ranking identical
  PASS  jdg_24 doubles every score: 0.0e+00, ranking identical
  PASS  every judge gets a random shift and stretch: 3.1e-15, ranking identical
  PASS  constant judge(s) jdg_07 removed: 0.0e+00, ranking identical

Second opinion: the rubric read as pairwise picks (Bradley-Terry, portal/pairwise.py):
  254 picks; no picks from jdg_01, jdg_07, jdg_23 (one review, or every pair tied)
  Kendall's tau with the calibrated ranking 0.646, with raw means 0.628
  PASS  jdg_24's scores squared (not a shift or stretch): picks identical; the calibration's q moves by up to 0.04, since it only undoes linear habits

Benchmark on synthetic events with a known true order (40 projects, 12 judges, 3 reviews each,
one harsh judge who only sees the strongest projects). Kendall's tau against the truth:
  calibration 0.830   raw means 0.677   per-judge z-scores 0.742   pairwise picks 0.799   (mean of 20 events)
  calibration beats raw means in 20 of 20, z-scores in 20 of 20
Every invariance check holds.
```

The benchmark events are synthetic because the fixture has no ground truth.
Each has 40 projects, 12 judges with their own leniency, scale and noise, and
one harsh judge who only sees the eight strongest projects: the case that
breaks z-scores. The model recovers the true order better than raw means and
better than z-scores in every one of the 20 events, and on average better
than the pairwise second opinion, which throws away how far apart a judge put
two projects.

## 9. A second opinion: pairwise picks

The calibration undoes linear habits. A judge who squashes only the top of
the scale is non-linear, and it can't. So the calibration page shows a second
ranking next to it that no way of using the scale can move.

Every judge who scored two projects differently has, in effect, picked the
better one. `portal/pairwise.py` collects those picks across all judges and
fits a Bradley-Terry model, `P(A beats B) = p_A / (p_A + p_B)`, with Hunter's
MM iteration and one virtual win and loss per project against a reference, a
weak prior that keeps an unbeaten project finite. Only the order of each
judge's own scores enters, so any increasing transformation of any judge's
scores leaves it unchanged; the proof squares one judge's scores to show it.
The constant judge and the single-review judges make no picks at all.

What it gives up is magnitude: "A slightly better than B" and "A far better
than B" are the same pick. That's why it's a second opinion rather than the
ranking. Where the two agree, trust the rank more; where they disagree, the
project's rank depends on how you read the judges' scales, and its "could be"
range will usually be wide as well.

## 10. Publishing results

Results are hidden from everyone but the event's organizers until published,
in the pages and the API (404 before then). Publishing freezes the result to
one calibration run. Each run stores the SHA-256 of exactly the scores it read,
in a canonical order, so anyone with the scores export can recompute it and
check the published ranking came from those scores. Later runs change nothing
public until someone publishes again, and every run and publication is in the
audit log.

## 11. Known limits

- **Linear judges only.** The model corrects a judge who is lenient or who
  spreads scores widely. It can't correct one who only compresses the top of
  the scale, or who uses the scale differently for different criteria (Wang &
  Shah, "Your 2 is my 1").
- **Collusion.** Two judges who agree to push a project look like two judges
  who agree. Nothing statistical separates them; conflict-of-interest rules
  and the audit log are the defence.
- **Thin data.** With three reviews per project and a few per judge, ranks are
  uncertain. The intervals say so; they don't fix it. More reviews per project
  do.
- **One number per review.** Calibration works on the weighted total. A
  per-criterion model would need far more reviews than a hackathon has.

## References

- D. Hunter, "MM algorithms for generalized Bradley-Terry models", Annals of Statistics, 2004.
- N. Lawrence, "Reviewer calibration for NIPS", 2014. https://inverseprobability.com/2014/08/02/reviewer-calibration-for-nips
- H. Ge, M. Welling, Z. Ghahramani, "A Bayesian model for calibrating conference review scores". https://mlg.eng.cam.ac.uk/hong/unpublished/nips-review-model.pdf
- M. Roos, J. Rothe, B. Scheuermann, "How to calibrate the scores of biased reviewers", AAAI 2011.
- J. Wang, N. Shah, "Your 2 is my 1, your 3 is my 9", 2018. https://arxiv.org/abs/1806.05085
