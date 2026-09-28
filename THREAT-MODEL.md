# Threat model

Who attacks a hackathon portal, how, and what ballotbench does about it.
The stakes are small prizes and bragging rights, so most attackers are
participants with a browser, a few friends and an evening, not nation
states. Each entry says what stops the attack, where that lives in the code,
and what doesn't stop it.

## People

- **Participants** want their project to win, want to see others' work
  early, and want more time.
- **Their friends** will vote, and will make a few extra accounts if asked.
- **Judges** are mostly honest; a few favour a friend or want to see how
  their scores compare.
- **Organizers** are trusted with the event, but not with each other's
  secrets or with rewriting history quietly.
- **Anyone on the internet** can reach the public pages and the API.

## Attacks

### Sybil voters: one person, many ballots

- **Stops it:** one ballot per account and per inbox, as unique constraints
  on `portal_voter`. Email addresses are normalized first
  (`voting.normalize_email`: lowercase, `+tag` dropped, Gmail dots and
  `googlemail.com` folded), so `a.b+1@googlemail.com` is `ab@gmail.com`. A
  second spelling of an inbox gets no second ballot and is logged as
  `vote.duplicate_refused`. Sign-ups are rate limited per address (10 an
  hour), link requests per address (5 an hour) and per inbox (3 an hour).
  The organizer's abuse panel (`voting.abuse_report`) flags networks (/24,
  or /64 for IPv6) with three or more voters, accounts created less than an
  hour before their first ballot, and three or more identical ballots.
  Organizers void a ballot with a reason; it leaves the tallies and the
  audit log says who did it and why.
- **Doesn't stop it:** someone with many real inboxes, a catch-all domain,
  or a botnet of addresses. There is no CAPTCHA, phone check or proof of
  personhood. Flags are never acted on automatically, because an office or
  a campus shares one network and friends vote alike; a person decides.
  Quadratic voting limits how much any one ballot can do, not how many
  there are.

### Ballot stuffing: one ballot, more weight

- **Stops it:** the `vote_rules` trigger (migration 0012) locks the voter's
  row and refuses any write that would take the sum of votes² past
  `vote_credits`, so two tabs or a replayed request can't overspend.
  It also refuses votes outside the window on the database clock, votes for
  a project that isn't a submitted, non-duplicate project of the voter's
  event (so no voting across events), votes for your own team, and votes by
  unconfirmed or voided voters. The app (`voting.cast`) checks the same
  things first so it can say why; it never trusts a cost sent by the client.
  Ballot writes are rate limited per address and per voter.
- **Doesn't stop it:** an email voter on a team whose members signed up with
  a different address. Own-team votes are refused for accounts in the
  database, and for email voters only when a team member's address reaches
  the same inbox, which only the app can check.

### Reading tallies or results early

- **Stops it:** tallies are shown only to the event's organizers until
  results are published (`voting.public_tallies`), `results.publish` refuses
  while voting is open, and published results are hidden again if voting
  reopens (`results.visible_run`), so nobody votes with the judges' ranking
  in front of them. Unpublished results are a 404, not a 403.
- **Doesn't stop it:** an organizer telling people. Organizers see tallies
  live, by design, so they can spot abuse.

### Scraping drafts

- **Stops it:** every project query goes through `access.visible_projects`:
  drafts are visible to their own team and the event's organizers only, and
  anyone else gets a 404 on the page and the API. The gallery lists
  submitted projects only.
- **Doesn't stop it:** a team member sharing their screen, or a public
  repository linked from the draft.

### Peeking at peer scores

- **Stops it:** judge queries start from `access.judge_assignments(user)`,
  so another judge's review is a 404; naming another judge in
  `/api/judge/scores?judge=` is a 403, never an empty list. Review comments
  go to organizers only. `scripts/isolation_curl.sh` tries all of this over
  HTTP.
- **Doesn't stop it:** judges talking to each other.

### Judge collusion

- **Stops it:** conflict-of-interest triggers (`team_member_zz_not_judge`,
  `membership_judge_not_member`, `assignment_rules`) keep a judge off their
  own team's project and out of any team in an event they judge. Each
  project gets k reviews from different judges, and every review is in the
  audit log.
- **Doesn't stop it:** two judges who agree to push a project look like two
  judges who agree. Calibration can't tell them apart
  ([JUDGING.md](JUDGING.md#10-known-limits)).

### Deadline gaming

- **Stops it:** the `project_deadline` trigger (migration 0002) refuses
  creating, deleting or editing a project after `submissions_close`, on the
  database's clock, whatever path the write takes: page, API, admin or
  shell. The client's clock is never asked. Moving the deadline is an
  audited `event.update`.
- **Doesn't stop it:** pushing to the linked repository after the deadline.
  The portal records `submitted_at`; checking the repository's history
  against it is up to the judges.

### Tampering with results or the audit log

- **Stops it:** published results are frozen to one calibration run, and
  each run stores the SHA-256 of the exact scores it read (`input_digest`).
  The audit log is append only (`audit_readonly`, `audit_no_truncate`) and
  hash chained; `manage.py verify_audit` names the first row that doesn't
  fit. Admin writes are audited like any other.
- **Doesn't stop it:** someone with the database superuser password can
  disable the triggers and rewrite the whole chain from any point onwards.
  Keep a copy of the latest `row_hash` somewhere else (the audit export has
  it) and a rewrite shows.

### CSV formula injection

- **Stops it:** every exported cell that a spreadsheet would read as a
  formula (starting with `=`, `+`, `-`, `@`, tab or carriage return) and
  isn't a plain number is prefixed with `'` (`exports.cell`), so a project
  called `=HYPERLINK(...)` opens as text.
- **Doesn't stop it:** a spreadsheet told to ignore that, or someone pasting
  cells into a formula by hand.

### Comments as a weapon

- **Stops it:** bodies are plain text, escaped by Django's autoescape (no
  `|safe` anywhere), at most 2000 characters (checked by the serializer and
  by a database constraint), and rate limited to 10 per account per 10
  minutes. The event's organizers hide a comment and it disappears for
  everyone else; the row and the audit trail stay.
- **Doesn't stop it:** abuse that's within the rules until an organizer
  reads it. There is no filter or pre-moderation.

### Invite-link leakage

- **Stops it:** team and role invites are single use, only their hash is
  stored, team invites expire in 72 hours (or at the deadline) and role
  invites in 7 days, and both can be revoked. Voting links are single use,
  stored as a hash, and replaced by the next request. A voting link opened
  with GET only shows a button, so a mail scanner that fetches it doesn't
  use it up.
- **Doesn't stop it:** whoever gets a leaked link first. A leaked organizer
  invite is a new organizer; check the Organizers list on the Settings page
  and the `role.accept` rows in the audit log.

### Token and session theft

- **Stops it:** API tokens are random, shown once, stored as SHA-256, and
  revocable by their owner at `/me/tokens` (or by an admin). Session cookies
  are HttpOnly and SameSite=Lax, every session POST needs a CSRF token, and
  no page can be framed except the read-only embed (below). Login
  attempts are limited to 20 per address per 10 minutes, so a password
  can't be guessed at network speed.
- **Doesn't stop it:** tokens don't expire until revoked. Plain HTTP is the
  default so the laptop demo works; behind TLS, `DJANGO_SECURE=1` turns on
  secure cookies, HSTS and the redirect to HTTPS. The login limit is per address, so a botnet gets
  20 guesses per address.

### Flooding

- **Stops it:** the rate limits above, counted in Postgres
  (`ratelimit.allow`) so every worker shares them. IPv6 is limited per /64,
  because one subscriber usually holds a whole /64.
- **Doesn't stop it:** a real denial of service. Put a proxy in front, and
  set `BALLOTBENCH_TRUSTED_PROXIES` to the number of proxies so the limits
  and the audit log see the visitor's address (read from X-Forwarded-For,
  counting from the right, so a client can't choose it), not the proxy's.

### Webhooks as a way into the private network

- **Stops it:** a webhook URL must be http(s) and every address its name
  resolves to must be public (`webhooks.public`: `is_global`, not
  multicast, not site-local `fec0::/10`; an IPv6 address carrying an IPv4
  one, mapped, compatible, translated or NAT64 `64:ff9b::/96`, is judged by
  the IPv4 one). That's checked when the organizer saves it and again right
  before each send, and the connection goes to the address that was checked,
  so a DNS answer that changes between the check and the request (DNS
  rebinding) can't redirect it. Redirects aren't followed, and each delivery
  is signed with HMAC-SHA256 so the receiver can tell it's from this portal.
- **Doesn't stop it:** an organizer choosing to deliver to their own network
  with `BALLOTBENCH_WEBHOOKS_ALLOW_PRIVATE=1`. Webhook payloads carry what the
  audit log carries for that event, so a webhook URL is a copy of the log:
  only organizers can add one, and adding one is itself audited. It keeps
  sending only while the organizer who added it (or who last resumed it)
  still organizes the event or is staff; otherwise the next change pauses it,
  with a `webhook.pause` audit row saying why.

### A webhook receiver holding up the sender

- **Stops it:** one deadline of 10 seconds per attempt, for connecting,
  sending and reading the answer together (a socket timeout alone counts
  each read afresh, so a receiver sending a byte every few seconds could
  hold the sender for ever). Only the status line is read, at most 16 KiB
  looking for it, and never the body. Sends happen with no transaction or
  row lock held: the sender claims a round of due deliveries, one per
  webhook, by moving each `next_attempt_at` a lease (2 minutes) ahead under
  `SKIP LOCKED`, commits, sends them all at once, then records each result.
  A sender that dies mid-send leaves its deliveries due again when the lease
  runs out.
- **Doesn't stop it:** a slow receiver still delays the other webhooks'
  deliveries by up to one deadline per round, since a round waits for its
  slowest send. The DNS lookup before each send isn't under the deadline;
  the system resolver's own timeouts bound it.

### The embed as a window in

- **Stops it:** `/embed/<slug>` is the only page any site may frame
  (`frame-ancestors *`, no X-Frame-Options); everything else is `DENY`. It
  renders as an anonymous visitor whatever cookie or token comes with the
  request, so it never shows a draft or unpublished results, and it has no
  forms, so framing it can't trick anyone into clicking something that
  changes state.
- **Doesn't stop it:** someone embedding a public gallery where you'd rather
  they didn't. It's public anyway.

### Forged records and certificates

- **Stops it:** records are Ed25519 signatures over canonical JSON; the
  public key is at `/.well-known/ballotbench-signing-key`, and `/verify`
  checks one without needing an account. Changing one character of a record
  makes it fail. Records never contain scores. The private key lives on the
  data volume, created with mode 0600, never in the repository.
- **Doesn't stop it:** someone who can read the data volume can sign
  anything. There's no revocation list and no key rotation yet: a new key
  makes every old record fail to verify.
