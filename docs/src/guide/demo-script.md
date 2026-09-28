# The five-minute demo

## The automatic version

`scripts/demo.sh` runs a whole event against the portal and narrates it,
printing the page to show in the browser at each step:

From a fresh clone, nothing else to set up:

```sh
git clone https://github.com/keirsalterego/ballotbench.git && cd ballotbench
sh scripts/demo.sh                     # builds and starts the portal if it isn't running, then runs
sh scripts/demo.sh --fresh --pause     # empty database first, then wait for Enter before each step
BALLOTBENCH_PORT=9000 sh scripts/demo.sh   # if port 8080 is taken
```

It creates a new event each run (Demo Day plus the time), so it can run
again without a reset. In twelve steps:
1. An organizer creates the event, with a track, a prize and a weighted rubric.
2. Four judges accept single-use invites.
3. Four teams sign up and submit; one invites a teammate.
4. The deadline passes: a late edit is refused by the API and the page.
5. The organizer hands out reviews.
6. Four judges score: harsh, generous, steady, and one who gives everything a 3.
7. Isolation attacks between judges and teams are refused.
8. Calibration flags the constant judge and ranks with honest ranges.
9. A community vote with a quadratic budget, each voter in their own order,
   and publishing refused while it's open.
10. Publishing, the signed results verified, a doctored copy refused.
11. A team reads why it placed where it did; another team can't.
12. The exports and the audit trail.

It needs only Python 3 (standard library) and, for `--fresh`, Docker.

## By hand

A shot list for recording one full event lifecycle (create, submit, judge,
publish) in five minutes. It uses both seeded events: the open **Demo Hack**
for the live lifecycle, and **Sample Hack 2026** (the fixture, 126 real
reviews) for judging maths worth showing.

Before recording:

```sh
docker compose down -v && docker compose up -d --build --wait
```

Open three browser windows (a normal one and two private ones) so you can be
three people at once. Every demo password is `ballotbench-demo`.

| Time | Who | What to do | What to say |
|-|-|-|-|
| 0:00 | anyone | Open <http://localhost:8080>. | One `docker compose up`, no network needed, seeded with the shared Dogfood fixture. |
| 0:15 | organizer@ballotbench.local | **My events → New event**, or open **Demo Hack (open) → Manage**. Show dates, add a track, set *functionality* weight 2. | Organizers set windows, tracks, prizes and a weighted rubric. The rubric freezes at the first score. |
| 0:45 | new account (private window) | **Create an account**, **join as a participant** on Demo Hack, **Create team**, **Make an invite link**. | Single-use invite links, stored only as hashes. |
| 1:05 | same | **Start your project**, fill title and repo, **Submit**. Show it in the gallery, then open **Edit** again and leave that tab open. | Drafts are private; submitted projects are public. |
| 1:25 | organizer | Settings: move **Submissions close** to a minute ago, save. | Watch the deadline hold on every path. |
| 1:35 | participant | In the edit tab you left open, press **Save changes**: *That window is closed*. Reload the event page: the project is now read-only. Then in a terminal, the fixture event, which closed in March: `curl -X POST -H "Authorization: Bearer bb_demo_participant_61b0c4" -H "Content-Type: application/json" -d '{"title":"late"}' localhost:8080/api/events/sample-hack-2026/projects` → 409. | The page, the API and a database trigger on the database's own clock all refuse; even a bug in the app couldn't let a late edit through. |
| 1:55 | terminal | `python3 run.py .dogfood.toml` | The official checker: T1 and T2 verified. |
| 2:05 | terminal | `sh scripts/isolation_curl.sh` (let it scroll) | 94 attempts to reach what you shouldn't, every one refused with the right code. |
| 2:15 | diego.herrera@example.org (judge) | **Your reviews** (in the header) → open a project: the scoresheet with ballot bubbles. | Judges see only their own assignments; another judge's scoresheet is a 404, naming another judge in the API is a 403. |
| 2:35 | organizer | **Sample Hack 2026 → Manage → Hand out reviews**: the preview proposes 8 top-ups. | The fixture's two unfinished batches: the planner finds them and balances load. |
| 2:50 | organizer | **Calibration and results → Run calibration**. Scroll: flagged judges, *does one judge decide the podium?*, the ranking with *could be* ranges and the pairwise column. | The fixture's constant judge (`jdg_07`, listed by name as Iva Petrova) gave every project 4: weight zero, exactly as if absent. Every rank comes with an honest range; on this data the judges agree no more than chance, and the page says so. |
| 3:35 | organizer | **Publish results**. Open the public results page; click *signed copy*; paste it into `/verify`: *Valid*. | Published results are frozen to one run, fingerprinted, and signed with Ed25519. |
| 3:55 | priya1@example.org (team NorthKiln) | Results → **How your project was scored** (Glass Signal, third). | "Why did we come third?" answered: each review, the judge's habit, how much it counted. Judges stay anonymous. |
| 4:15 | organizer | **Audit log**: filter by *results*. Then **Exports**: download `scores.csv`. | Every change is in a hash-chained log the database won't let anyone edit. Every stage exports as CSV. |
| 4:35 | terminal | `docker compose exec web python manage.py verify_audit` and `... normalization_proof \| tail -8` | The chain checks out; the maths claims are recomputed live and hold. |
| 4:50 | | Open the book: <https://keirsalterego.github.io/ballotbench/> (it redirects to the custom domain). | MIT licensed; everything above is documented for a stranger. |

If there's time to spare, show the community vote on Demo Hack: in Settings
set **Voting mode** to signed-in accounts with a window that's open now,
then vote as priya (confirmed demo account) and show the quadratic budget
and the organizer's abuse panel.
