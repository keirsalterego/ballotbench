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

My first voting link confirmed the voter on GET, which is what my plan
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

My plan said a second sign-up with another spelling of the same inbox
(`a.b+x@googlemail.com` after `ab@gmail.com`) must be refused. Saying "that
inbox has already voted" would let anyone check whether a given address
voted. Instead the page reads the same either way, no second ballot is made,
the attempt is logged as `vote.duplicate_refused` for the abuse panel, and
a fresh link goes to the address just typed. (At first it went to the
address that signed up first; see the end of these notes for why not.) The
person who owns the inbox can still get in; nobody else learns anything.

## Hiding results while the vote is open

Refusing to publish while voting is open wasn't quite enough: an organizer
could publish the judges' results first and then open a vote, and voters
would have the ranking in front of them. Published results now go back to
a 404 for the public while a vote is open, and come back when it closes.

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

## Three branches at once, and what the docs caught

I split the last stretch into three branches built side by side: the book,
the public vote, and the T4 pieces. Two things I didn't expect:

**Both code branches took migration 0013.** Each was right on its own branch
and together they gave Django two leaf nodes. The webhooks migration became
0014 and depends on the voting one; nothing outside a scratch database had
applied either, so renaming was safe. Next time I'd reserve numbers up front.

**Writing the docs found real bugs.** Explaining the calibration page line by
line turned up that the page and `normalization_proof` disagreed: the page
leaves out a project whose every reviewer the model ignores (`prj_24`, both
of whose judges are discordant), the proof didn't, so JUDGING.md quoted a
ranking the product never shows. They also ran the agreement test with
different shuffle counts. Documenting the operations side found that
`BALLOTBENCH_DEMO_SEED=0` still loaded the fixture event, that `DJANGO_SECURE`
made the health check fail on its own redirect, and that an organizer's
`?judge=jdg_24` could match a judge in another event, since fixture ids are
only unique per event. All fixed, each with a test. The lesson I keep
relearning: the fastest code review is trying to explain the code to a
stranger.

## What a security review found in the vote and the duplicates

**Void, read, unvoid.** Organizers saw the tallies live and could undo a
void, so voiding one ballot, reading the tallies and counting it again
showed exactly what that voter chose, while every page said ballots were
private. A void is now final, and while voting is open the organizer page
shows how many ballots there are, not the tallies. After the close the
tallies are there, and a void still subtracts a ballot you can see; but it
is gone for good, so reading it costs the organizer that vote.

**The first spelling of an inbox owned its links.** Asking for a link as
`alice+nope@yahoo.com` sent every later link for `alice@yahoo.com` to the
`+nope` address. For providers where a +tag is its own inbox, that's a
stolen ballot. The link now goes to whatever was typed; a new link replaces
the old one. Quoted local parts (`"a.b"@gmail.com`) are refused: legal, but
nobody needs one to vote, and normalizing them right is a rabbit hole.
`normalize_email` also used to raise on `+x@example.com`, and because it runs
over every team member's address, one such account broke every email
voter's ballot page. It no longer raises.

**Duplicates by pk.** The detector ordered projects by pk, drafts included,
and every submit re-flagged the whole event. A draft made early and filled
in later with another team's public title and repo made the real project
the "duplicate", taking it out of judging and off the ballot; and an
organizer's "it's different" lasted until the next submit. Now only
submitted projects count, earlier means `submitted_at`, a submit only flags
the project being submitted, and `duplicate_cleared` keeps an organizer's
call.

## What a stranger found in an hour

With every test green, I had someone follow the book's tour from a fresh
clone, as a judge would, and write down everything that disagreed with it.
It found 28 things. The ones that stung:

- **"Save draft" after submitting quietly replaced the submitted scores.**
  The review stayed "submitted", with the old timestamp, and calibration read
  the new numbers. Every test submitted once and stopped. A submitted review
  now only resubmits.
- **Deleting a rubric criterion after scoring was a 500.** Changing a weight
  was refused politely by the trigger; deleting went through a foreign key
  set to RESTRICT, and nobody had wrapped that path. The same shape as the
  track bug the security review found an hour earlier: a database refusal
  reached from a path the app didn't guard.
- **The venue problem.** Rate limits per address are right against a flood
  and wrong at a hackathon, where three hundred people share one NAT
  address. The walkthrough locked itself out after twenty ordinary logins.
  Per-address limits now scale for a crowd, and a per-account limit does the
  job of stopping someone guessing one person's password.
- **The offline proof published no port.** `internal: true` networks can't
  publish ports, so following the README gave a stranger nothing to open.
  The honest instruction is simpler: switch off the Wi-Fi.

None of these were security holes and all of them would have been on camera
in a demo. Reading the docs aloud against the running thing is a test
nothing else replaces.
