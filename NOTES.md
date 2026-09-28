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
