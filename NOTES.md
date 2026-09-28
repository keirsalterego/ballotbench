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
