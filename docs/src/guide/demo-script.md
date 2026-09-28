# The five-minute demo

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
| 1:05 | same | **Start your project**, fill title and repo, **Submit**. Show it in the gallery. | Drafts are private; submitted projects are public. |
| 1:25 | organizer | Settings: move **Submissions close** to a minute ago, save. | Watch the deadline hold on every path. |
| 1:35 | participant | Try to save the project: *That window is closed*. Then in a terminal: `curl -X POST -H "Authorization: Bearer bb_demo_participant_61b0c4" -H "Content-Type: application/json" -d '{"title":"late"}' localhost:8080/api/events/sample-hack-2026/projects` → 409. | The app says 409 with the time; a database trigger on the database's own clock refuses it even if the app had a bug. |
| 1:55 | terminal | `python3 run.py .dogfood.toml` | The official checker: T1 and T2 verified. |
| 2:05 | terminal | `sh scripts/isolation_curl.sh` (let it scroll) | 94 attempts to reach what you shouldn't, every one refused with the right code. |
| 2:15 | diego.herrera@example.org (judge) | **Your reviews** → open a project: the scoresheet with ballot bubbles. | Judges see only their own assignments; another judge's scoresheet is a 404, naming another judge in the API is a 403. |
| 2:35 | organizer | **Sample Hack 2026 → Manage → Hand out reviews**: the preview proposes 8 top-ups. | The fixture's two unfinished batches: the planner finds them and balances load. |
| 2:50 | organizer | **Calibration and results → Run it again**. Scroll: flagged judges, *does one judge decide the podium?*, the ranking with *could be* ranges and the pairwise column. | `jdg_07` gave every project 4: weight zero, exactly as if absent. Every rank comes with an honest range; on this data the judges agree no more than chance, and the page says so. |
| 3:35 | organizer | **Publish results**. Open the public results page; click *signed copy*; paste it into `/verify`: *Valid*. | Published results are frozen to one run, fingerprinted, and signed with Ed25519. |
| 3:55 | priya1@example.org (team NorthKiln) | Results → **How your project was scored**. | "Why did we come fourth?" answered: each review, the judge's habit, how much it counted. Judges stay anonymous. |
| 4:15 | organizer | **Audit log**: filter by *results*. Then **Exports**: download `scores.csv`. | Every change is in a hash-chained log the database won't let anyone edit. Every stage exports as CSV. |
| 4:35 | terminal | `docker compose exec web python manage.py verify_audit` and `... normalization_proof \| tail -8` | The chain checks out; the maths claims are recomputed live and hold. |
| 4:50 | | Open the book: <https://keirsalterego.github.io/ballotbench/>. | MIT licensed; everything above is documented for a stranger. |

If there's time to spare, show the community vote on Demo Hack: in Settings
set **Voting mode** to signed-in accounts with a window that's open now,
then vote as priya (confirmed demo account) and show the quadratic budget
and the organizer's abuse panel.
