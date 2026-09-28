# Build notes

A running log of what surprised me, what I changed my mind about, and why.
Newest last.

## Team names repeat in the fixture

I started with `UNIQUE (event, name)` on teams, which felt obviously right.
The seed fell over on its first run: the fixture has three different teams
called StillTrail and two each called AmberSwitch and OpenSignal. They have
different ids and different members, so they are different teams. Dropped
the constraint; teams are told apart by id, and the UI shows the id-based
link. Projects, by contrast, are checked for duplicates on purpose.

## The planner's first version wasn't balanced

My first planner went project by project, giving each the least-loaded
eligible judge. A property test with 12 projects, 6 judges and k=3 ended at
loads 7, 7, 6, 6, 5, 5, not 6 each. Driving by judge instead (the idlest
judge picks the neediest project) didn't fix it either: by the end, the two
idle judges already held both of the last projects that needed someone.
Greedy can't see that coming. What fixed it was a repair pass afterwards:
move one of the plan's new reviews from the busiest judge to the idlest one
who is allowed to take it, until no move narrows the gap. Existing reviews
are never moved. The property test now checks loads stay within one on 200
random events.

## Tier T4: what surprised me

**A route would have swallowed another.** I first added
`api/events/import` at the end of `urls.py`, but the older
`api/events/<slug:slug>` matches first, with slug `import`, and would have
answered every import with 405. It sits above that pattern now, with a
comment saying why.

**Pasted JSON can't always be echoed back.** The verify API returns the
record it was sent, so a record containing a lone surrogate (`"\ud800"`,
perfectly legal JSON) made DRF's renderer throw a 500. The malformed-input
test found it. Looking for its siblings turned up `NaN`, which Python's
`json` reads but DRF's strict output refuses. Both now fail the canonical
encoding step and come back as "not valid", and both are test cases.

**`SET LOCAL` outlives the function that sets it.** The importer turns on
the deadline bypass for "its own transaction", but inside a request with
`ATOMIC_REQUESTS` its transaction is only a savepoint: the bypass would have
stayed on for the rest of the request. Harmless for the seed, not for an
API endpoint, so the import now switches it off before returning (a failed
import's savepoint rollback undoes it anyway).

**The fixture importer trusted its file; a bundle importer can't.** Bundles
come from any organizer, and the fixture path wrote `repo_url` straight into
a link. A `javascript:` URL would have been stored XSS on the project page.
Bundles now go through the same URL, email and choice checks the forms use.

**The round trip calibrates identically, not approximately.** I expected to
need a tolerance: floating point sums depend on order. But the export lists
reviews by id and the import creates them in that order, so the fit sees the
same sequence and every q matches to the last bit. The test asserts equality.

**An iframe that reports `scrollHeight` can grow but never shrink**:
`document.documentElement.scrollHeight` is never less than the iframe's own
height. The embed reports the height of its content box instead; I checked
it in headless Chromium on a page that embeds the widget.
