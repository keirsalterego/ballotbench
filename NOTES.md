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

## The confirmation link that confirmed itself

My first voting link confirmed the voter on GET, which is what the spec
said and what every tutorial does. Then I remembered that corporate mail
scanners (and some webmail previews) fetch every link in a message before
the person sees it. With a single-use token, the scanner would use it up and
the voter would click a dead link. Opening the link now shows a button, and
the POST behind it confirms. One extra click, and the link survives being
looked at.

## A refused request that forgot it happened

The rate limiter counts hits in Postgres, inside the request's transaction
(every request is atomic). My API views raised `Conflict` for an
over-budget ballot, DRF's exception handler marks the transaction for
rollback, and the rollback took the rate-limit hit with it. So failed
requests were free, which is exactly backwards: the requests worth limiting
are the ones that fail. The API views now return their refusals as
responses instead of raising them, and the hit commits. The page views
already did.

## Where the ballot's privacy stops

I went back and forth on what `vote.cast` should record. The choices in
the clear would make the log a full record, but every organizer of the
event reads that log, and a community ballot is between the voter and the
tally. So the row has the voter, the credits spent, the number of projects
and a fingerprint of the ballot. A plain SHA-256 of something as small as a
quadratic ballot can be reversed by trying every ballot, so the fingerprint
is an HMAC keyed with the server's secret. It still lets the abuse panel see
several voters casting the same ballot, which is the one thing it was for.

## One inbox, one ballot, and not telling anyone who voted

The spec said a second sign-up with another spelling of the same inbox
(`a.b+x@googlemail.com` after `ab@gmail.com`) must be refused. Saying "that
inbox has already voted" would let anyone check whether a given address
voted. Instead the page reads the same either way, no second ballot is made,
the attempt is logged as `vote.duplicate_refused` for the abuse panel, and
a fresh link goes to the address that signed up first, which is the same
inbox. The person who owns it can still get in; nobody else learns anything.

## Hiding results while the vote is open

Refusing to publish while voting is open wasn't quite enough: an organizer
could publish the judges' results first and then open a vote, and voters
would have the ranking in front of them. Published results now go back to
a 404 for the public while a vote is open, and come back when it closes.
