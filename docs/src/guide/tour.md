# A whole event, start to finish

This walks the open demo event through its life: set up, teams, submissions,
the deadline, judging, calibration, results. It's also the script of the
five-minute demo video. Keep two browser windows open (a normal one and a
private one) so you can be two people at once.

## 1. The organizer sets it up

Sign in as `organizer@ballotbench.local`, open **My events**, then **Manage**
next to *Demo Hack (open)*.

- **Dates and settings.** Four windows: submissions, judging, voting and
  (implicitly) results. `k`, reviews per project, defaults to 3.
- **Tracks and prizes.** Add a track called *Hardware*; add a prize for it.
- **Rubric.** Three criteria to start: functionality, quality, innovation,
  each 1 to 5, weight 1. Give *functionality* weight 2. The weights can
  change freely until the first score comes in; after that the database
  refuses, so nobody can tune weights after seeing who they favour.
- **Invite a judge.** Under *Invite judges and organizers*, pick *Judge*,
  tick a track, and make a link. It's shown once and works once. The portal
  sends no email; send the link however you like.

## 2. Participants form a team

In the private window, create an account, then **My events → join as a
participant** on the demo event.

- **Create team**, then **Make an invite link**. Open it signed in as someone
  else and they join. The link then stops working; a team can't grow past the
  event's size limit, even if two people accept at the same moment.
- **Start your project.** Save it as a draft: only the team and the organizers
  can see it. **Submit** it and it appears in the public gallery. You can keep
  editing until the deadline.

## 3. The deadline

As the organizer, move **Submissions close** to a minute from now and save.
Wait for it. Back in the participant's window, try to save the project: the
answer is *That window is closed*, with the time. The same happens through the
API (a 409 with the close time) and in the admin. Behind all three, a trigger
in the database refuses the write on its own clock, so even a bug in the app
couldn't let a late edit through.

## 4. Judging

As the organizer, open **Hand out reviews**. The preview shows which judge
gets which project: every project gets k reviews, the idlest judge picks
next, nobody reviews their own team or outside their tracks. Apply it.

Sign in as the judge. **Your reviews** lists only your projects and how long
they'll take. Each has a scoresheet: one bubble per score per criterion, a
comment only the organizers see, *Save draft* and *Submit review*. If you
know the team, **I have a conflict** steps you aside; the organizer sees the
project needs another judge.

Meanwhile, the organizer's **Progress** page shows each judge's assigned,
started and finished counts and which projects are short of reviews. *Keep
this page up to date* refreshes it every 20 seconds.

## 5. Calibration

Open **Calibration and results** and run it. For the fixture event this is
where it gets interesting:

- `jdg_07` gave every project 4 on everything. They're listed as *gave every
  project the same score* and carry no weight.
- `jdg_01` and `jdg_23` wrote one review each: nothing to compare with, so no
  weight either.
- The ranking shows raw rank, calibrated rank, and the range each rank could
  plausibly be. On the fixture these ranges are wide, and the page says why:
  the judges agree no more than chance would.

[Reading the calibration page](../judging/reading.md) goes through it line by
line.

## 6. Publish

**Publish results** freezes the ranking to this calibration run. Until then,
the results page and API answer 404 to everyone but the organizers. The public
page shows each project's calibrated score, its plausible rank range, and the
fingerprint of the scores it came from.

## 7. A community vote

In **Settings**, set **Voting mode** to signed-in accounts and put the voting
window after submissions close (the portal refuses it earlier). While it's
open, **Community vote** shows how many ballots are in and who cast them,
with flags for crowded networks, brand-new accounts and identical ballots,
but not the per-project tallies: those appear once voting closes.

A voter opens **Vote** on the event: the projects come in an order made for
them, they spread 25 credits (n votes on a project cost n²), and they can't
vote for their own team. An account has to confirm its email address first:
the link is sent at sign-up, and with the network off it lands in the
outbox, which the site admin reads under **Admin → Outbound emails**.

Results can't be published while the vote is open. Close it, publish, and the
public results show the community votes next to the judges' ranking.

## 8. Export

**Exports** has a CSV for every stage: registrations, teams, projects,
assignments, every score, the calibrated results, each judge's calibration,
and the audit log with its hash chain. Every download is itself in the audit
log.
