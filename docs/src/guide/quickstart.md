# Run it in five minutes

You need Docker with the Compose plugin. Nothing else: no Python, no
Postgres, no accounts anywhere.

```sh
git clone https://github.com/keirsalterego/ballotbench.git
cd ballotbench
docker compose up
```

The first run builds the image, starts Postgres, creates the tables, loads
the shared Dogfood fixture event and prints something like:

```text
fixture event sample-hack-2026: 8 tracks, 30 judges, 40 teams, 91 members, 41 projects, 126 reviews, 1 duplicates
seeded. test logins:
  organizer    Authorization: Bearer bb_demo_organizer_5c1e0a
  judge_a      Authorization: Bearer bb_demo_judge_a_8d24f1
  judge_b      Authorization: Bearer bb_demo_judge_b_3a9e77
  participant  Authorization: Bearer bb_demo_participant_61b0c4
```

Open <http://localhost:8080>. You're looking at the public gallery.

## Sign in

Every demo account has the password `ballotbench-demo`:

| Who | Email |
|-|-|
| Organizer | `organizer@ballotbench.local` |
| Judge A | `diego.herrera@example.org` |
| Judge B | `ines.rocha@example.org` |
| Participant | `priya1@example.org` |
| Site admin | `admin@ballotbench.local` |

The bearer tokens are for scripts and the API: pass them as an
`Authorization` header.

## Two events are waiting

- **Sample Hack 2026** is the fixture: 41 projects, 126 reviews, on its real
  dates, so submissions closed in March 2026. Sign in as the organizer, open
  it, and go straight to *Calibration and results*.
- **Demo Hack (open)** is empty and open for submissions for two weeks from
  your first boot. Use it to walk through an event yourself; the
  [tour](tour.md) does exactly that.

## Check what it claims

```sh
python3 run.py .dogfood.toml                                   # the official Dogfood checker
sh scripts/isolation_curl.sh                                   # tries to reach what it shouldn't
docker compose exec web python manage.py normalization_proof   # the judging maths, recomputed
docker compose exec web python manage.py verify_audit          # the audit log's hash chain
```

## With the network off

Once the images are built, the portal needs no network. Prove it with an
override that puts the containers on a network with no way out:

```sh
docker compose down -v
docker compose -f docker-compose.yml -f docker-compose.offline.yml up
```

## Start over

`docker compose down -v` deletes the database volume. The next `up` seeds a
fresh copy.
