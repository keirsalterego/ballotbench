# ballotbench

ballotbench is a hackathon portal you run yourself. Teams submit projects,
judges score them against a rubric the organizer weights, and the organizer
gets a ranking that takes each judge's habits out and says honestly how sure
it is. It starts with one `docker compose up` and needs no network after
that.

I built it for [Dogfood 2026](https://dogfoodhack.com), a hackathon where
the thing you build is a hackathon platform, and the winner's portal gets
used to run the next events. That shaped every decision: it has to come up on
a volunteer's laptop, and its results have to survive a team asking "why did
we come fourth?"

## What makes a ranking defensible

Three things, which the rest of this book keeps coming back to:

1. **Nobody can see what they shouldn't, or change what they shouldn't.**
   Judges can't read each other's scores; nobody can edit a project after the
   deadline. These rules live in the database, not only in the pages, so
   they hold for every way in: the web pages, the API, the admin, a shell.
2. **The maths is written down and tested.** Judges score differently; the
   calibration model that corrects for it is described in full, and its
   guarantees (shifting or stretching one judge's scores changes nothing, a
   judge who gives everyone the same score counts as absent) are checked by
   tests and by a command you can run.
3. **Everything leaves a trail.** Every change writes a row to an audit log
   that the database won't let anyone edit, chained by hashes so a quiet edit
   by a database superuser still shows.

## Where to start

- To try it: [Run it in five minutes](guide/quickstart.md).
- To see a whole event: [A whole event, start to finish](guide/tour.md).
- To judge the judging: [Assignment, scoring and calibration](judging/method.md).
- To run it for a real event: [Running it for real](reference/operations.md).

The source is at <https://github.com/keirsalterego/ballotbench>, MIT licensed.

## This book

This book is published at <https://keirsalterego.github.io/ballotbench/>,
rebuilt whenever the docs change on main. Its source is in `docs/`, and the
chapters on judging, architecture and the data model include the
repository's top-level documents directly, so there is one copy of each. To
read it locally with [mdBook](https://rust-lang.github.io/mdBook/) 0.5:

```sh
mdbook serve docs --open
```
