# For organizers

You organize an event if you created it, or someone sent you an organizer
invitation for it. Site admins can do everything organizers can, in every
event.

## Creating an event

**My events → New event.** Anyone who already organizes an event, and any
site admin, can create one; you become its first organizer. Every event
starts with a three-criterion rubric you can change.

The slug is part of every URL and can't change later. All times are UTC.

## The windows

| Window | What it controls |
|-|-|
| Submissions open → close | Teams can form, and projects can be created, edited and submitted. Enforced by the database clock. |
| Judging open → close | Judges can save and submit reviews. |
| Voting open → close | Public voting, if you turn it on. Results can't be published while it's open. |

Moving a window takes effect immediately: extending the deadline reopens
editing at once.

## Tracks, prizes and the rubric

Tracks group projects and limit which judges see what: a judge with tracks
only reviews projects in those tracks. A judge with no tracks reviews
anything.

The rubric is a list of criteria, each with a weight and a range. A review's
score is the weighted mean of its criteria, each scaled to 0..1 by its own
range. Change it freely until the first score arrives; after that the
database freezes it.

## Judges and co-organizers

Invite them with a link from the settings page. Links are single use and
last seven days. A judge invitation can carry tracks. Nobody can be both a
judge and on a team in the same event: the database refuses either order.

## Handing out reviews

**Hand out reviews** shows a plan before it changes anything. It tops every
submitted project up to k reviews, gives each new review to the idlest judge
who may take it, and adds a few bridging reviews if some judges share no
projects with the rest (calibration needs them connected). Projects no
eligible judge is left for are listed, so you know to invite someone.

You can also give one project to one judge by hand on the **Progress** page,
and take back a review nobody has started.

## Watching progress

**Progress** lists each judge's assigned, started, finished and stepped-aside
counts, and each project's finished reviews, fewest first. Projects below k
are flagged *low coverage*.

## The community vote

Turn it on in Settings with **Voting mode**: *signed-in accounts* (anyone
with an account votes once) or *confirmed email addresses* (anyone votes
once per inbox, after opening a link we mail them). **Vote credits** is each
voter's budget: n votes for one project cost n² credits. Send people to
`/events/<slug>/vote`; the gallery and every project page link there too.

With the network off, confirmation links can't leave the box. They land in
**Outbound emails** in the admin, where a site admin can read them.

**Community vote** in the organizer menu shows how many ballots are in
while voting is open, and the tallies once it closes (only organizers see
them until you publish results, which you can't do while voting is open),
and a list of things worth a second look: several
voters on one network, accounts made just before their ballot, identical
ballots, and sign-ups refused because another spelling of the same inbox
had already voted. None of these void anything by themselves. If you decide
a ballot is fake, void it with a reason; it leaves the tallies for good and
the audit log records it. There's no undo: voiding a ballot and counting it
again would show you what that voter chose.

## Duplicates

When a project has the same repository and title as an earlier one (or the
same team resubmits the same repository or title), it's flagged and left out
of the rankings. Only submitted projects count, and the one submitted later
is the copy. **Duplicates** lets you confirm it or put it back; a project you
put back is never flagged again.

## Calibration and results

Run calibration as often as you like; each run is kept. Publishing freezes
the public results to one run. You can hide them again. See
[Reading the calibration page](../judging/reading.md).

## The audit log

**Audit log** lists every change in the event: who, when, from which address,
and the before and after values. Filter by kind of change or by person. Rows
can't be edited or deleted, even by the admin; `verify_audit` checks the hash
chain.

## Exports

Every stage as CSV. Cells that a spreadsheet would run as a formula (a title
starting with `=`, say) are prefixed with a quote, so opening an export is
safe.
