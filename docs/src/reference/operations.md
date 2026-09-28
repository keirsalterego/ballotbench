# Running it for real

The image that runs the demo is the image you run an event on. What changes
is configuration: a real secret, your hostname, no demo accounts, TLS in
front, and backups. This chapter covers each, then the maintenance commands
and the few sharp edges I know about.

## A checklist

For an event that people outside your laptop will use:

1. Start from an empty database, with the demo accounts off
   ([below](#demo-accounts)).
2. Put your settings in a `docker-compose.override.yml`
   ([below](#an-override-file)) rather than editing `docker-compose.yml`.
3. Create the first site admin with `createsuperuser`.
4. Put a reverse proxy with TLS and rate limits in front, and set
   `DJANGO_SECURE=1`.
5. Schedule `pg_dump` and `verify_audit`.
6. Before the event, run through the [tour](../guide/tour.md) once on your
   own deployment.

## Environment variables

Settings come from the environment, read in
[`src/ballotbench/settings.py`](https://github.com/keirsalterego/ballotbench/blob/main/src/ballotbench/settings.py),
[`src/entrypoint.sh`](https://github.com/keirsalterego/ballotbench/blob/main/src/entrypoint.sh)
and the seed command. This is all of them.

| Variable | Default | In `docker-compose.yml` | What it does |
|-|-|-|-|
| `DJANGO_SECRET_KEY` | none | not set | signs sessions and CSRF tokens; wins over the file below |
| `DJANGO_SECRET_KEY_FILE` | none | `/data/secret_key` | a file holding the key; the entrypoint writes a random one there on first boot if it's missing or empty |
| `DJANGO_DEBUG` | off | not set | `1` shows Django's debug pages and allows a built-in insecure key; for development only |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | adds `web` | comma-separated host names the portal answers to |
| `DJANGO_SECURE` | off | not set | `1` behind a TLS proxy: secure cookies, HTTPS redirect, HSTS, trust the proxy's scheme header ([below](#tls-and-a-reverse-proxy)) |
| `POSTGRES_DB` | `ballotbench` | not set | database name |
| `POSTGRES_USER` | `ballotbench` | not set | database user |
| `POSTGRES_PASSWORD` | `ballotbench` | `ballotbench` | database password |
| `POSTGRES_HOST` | `localhost` | `db` | database host |
| `POSTGRES_PORT` | `5432` | not set | database port |
| `BALLOTBENCH_DEMO_SEED` | off | `"1"` | `1` loads the fixture event, the open demo event and the demo accounts with their fixed tokens on boot; anything else loads nothing |
| `BALLOTBENCH_FIXTURES` | `/app/fixtures.json` in the image | not set | the fixture file the seed imports on every boot |
| `BALLOTBENCH_TRUSTED_PROXIES` | `0` | not set | how many reverse proxies sit in front; the caller's address is then read from X-Forwarded-For, counting from the right |
| `BALLOTBENCH_ADDRESS_LIMIT_SCALE` | `10` | not set | multiplies every per-address rate limit, so a venue behind one NAT address isn't locked out; per-account limits aren't scaled |
| `DJANGO_EMAIL_BACKEND` | the database outbox | not set | Django's SMTP backend (`django.core.mail.backends.smtp.EmailBackend`, plus the usual `EMAIL_*` settings) to really send mail |
| `WEB_WORKERS` | `3` | not set | gunicorn worker processes |

If neither `DJANGO_SECRET_KEY` nor `DJANGO_SECRET_KEY_FILE` is set, and debug
is off, the portal refuses to start rather than run with a guessable key.
The generated key lives on the `webdata` volume, so sessions survive a
restart and the key never lives in the repository.

The `db` service has its own `POSTGRES_DB`, `POSTGRES_USER` and
`POSTGRES_PASSWORD`, read by the Postgres image. The web service's values
must match them. The Postgres image only reads them when it creates the
database, the first time the `pgdata` volume is used; changing the password
later means `ALTER USER` inside the database as well.

`DJANGO_ALLOWED_HOSTS` must keep `127.0.0.1`: the container's health check
asks for `http://127.0.0.1:8080/projects`, and Django refuses a host it
doesn't know.

## An override file

Compose reads `docker-compose.override.yml` next to `docker-compose.yml`
automatically, so your settings stay out of the file you pull updates into.
For a deployment at `judging.example.org` behind a proxy on the same
machine:

```yaml
# docker-compose.override.yml
services:
  db:
    environment:
      POSTGRES_PASSWORD: a-long-random-password
  web:
    environment:
      POSTGRES_PASSWORD: a-long-random-password
      DJANGO_ALLOWED_HOSTS: judging.example.org,localhost,127.0.0.1
      DJANGO_SECURE: "1"
      BALLOTBENCH_DEMO_SEED: "0"
    # Only the proxy on this machine talks to the portal.
    ports: !override
      - "127.0.0.1:8080:8080"
    # With DJANGO_SECURE=1 a plain-http request is redirected to https,
    # so the health check has to say it came through the proxy.
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8080/projects', headers={'X-Forwarded-Proto': 'https'}), timeout=3)"]
```

`!override` replaces the port list instead of adding to it; it needs Docker
Compose 2.24 or newer. Check what Compose will actually run with
`docker compose config`.

## Demo accounts

With `BALLOTBENCH_DEMO_SEED=1`, every boot makes sure these exist: the site
admin `admin@ballotbench.local`, the organizer `organizer@ballotbench.local`,
two fixture judges and a fixture participant, all with the password
`ballotbench-demo`, and four API tokens whose values are printed in the
README. Everything about them is public, so a real deployment must not have
them.

- **On a new deployment**, set `BALLOTBENCH_DEMO_SEED` to `0` before the
  first boot. None of them is created, including the admin, so create your
  own with [`createsuperuser`](#createsuperuser).
- **If they already exist**, setting the variable to `0` stops the seed
  creating them; it doesn't delete them. Start again from an empty volume
  (`docker compose down -v`, which deletes everything), or switch them off
  from a shell:

```sh
docker compose exec web python manage.py shell -c "
from django.utils import timezone
from portal.models import ApiToken, User
ApiToken.objects.filter(label='demo', revoked_at=None).update(revoked_at=timezone.now())
User.objects.filter(email__in=['admin@ballotbench.local', 'organizer@ballotbench.local',
    'diego.herrera@example.org', 'ines.rocha@example.org', 'priya1@example.org']).update(is_active=False)
"
```

A deactivated user can't sign in, and their tokens are refused.

**The seeded events follow the same switch.** With `BALLOTBENCH_DEMO_SEED` set
to anything but `1`, the seed loads nothing: no fixture event, no demo event,
no accounts. A deployment that already has them keeps them (the seed never
deletes); remove them for good with
`docker compose exec web python manage.py delete_event sample-hack-2026 --yes`
(and `demo-open`), and they won't come back.

## TLS and a reverse proxy

The portal speaks plain HTTP on port 8080. For anything public, put a
reverse proxy in front that terminates TLS, and set `DJANGO_SECURE=1`. That
turns on:

- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`: cookies only over HTTPS;
- `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`: a request
  counts as HTTPS when the proxy says so in `X-Forwarded-Proto`;
- `SECURE_SSL_REDIRECT`: plain-HTTP requests are redirected to HTTPS;
- `SECURE_HSTS_SECONDS` of 30 days: browsers stay on HTTPS;
- `CSRF_TRUSTED_ORIGINS`: `https://` plus each host in
  `DJANGO_ALLOWED_HOSTS`.

It's off by default so the demo works on `http://localhost`. With it on,
the acceptance checker and the isolation probe, which talk plain HTTP to
port 8080, get redirects instead of answers; they're for the demo stack.

Trusting `X-Forwarded-Proto` is only safe if the proxy always sets it
itself and nobody can reach port 8080 except through the proxy. Bind the
port to `127.0.0.1` as in the override above, or keep it off the host
entirely.

The portal limits logins per address and per account, and sign-ups and
password resets per address (in the database, so every worker shares the
counts; see `BALLOTBENCH_ADDRESS_LIMIT_SCALE`). A proxy is still the right
place for a coarse limit on everything, before a request reaches Python. An
nginx example:

```nginx
limit_req_zone $binary_remote_addr zone=bb_auth:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=bb_api:10m rate=10r/s;

server {
    listen 80;
    server_name judging.example.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name judging.example.org;
    ssl_certificate     /etc/letsencrypt/live/judging.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/judging.example.org/privkey.pem;
    client_max_body_size 1m;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    location ~ ^/(login|signup)$ {
        limit_req zone=bb_auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8080;
    }
    location /api/ {
        limit_req zone=bb_api burst=20;
        proxy_pass http://127.0.0.1:8080;
    }
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
```

The limits are a starting point: ten sign-in or sign-up attempts a minute
per address is plenty for a person and slow for a script. A whole venue
behind one NAT address shares that budget, so watch the proxy's log on the
day.

The audit log records the address a request came from, as the portal sees
it. It deliberately doesn't trust `X-Forwarded-For`, so behind a proxy the
address column shows the proxy's address, not the visitor's; the proxy's
own access log has the real one.

## Email

The portal sends no email. Invitation links are shown once, to whoever
makes them, to pass on however they like; that's what lets it run with no
network. `EMAIL_BACKEND` is Django's console backend, so anything Django
itself tries to send is written to the web container's log.

If you add email, it's a change in `settings.py`, since there are no email
variables today. Replace the `EMAIL_BACKEND` line with something like:

```python
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "ballotbench@judging.example.org")
```

and add those variables to your override file. Keep in mind that the stack
then needs the network to reach the mail server.

## Backups

All state is in two Docker volumes: `pgdata` (the database) and `webdata`
(the generated secret key). The database is plain Postgres, so `pg_dump`
works. Take a custom-format dump from the running stack:

```sh
docker compose exec -T db pg_dump -U ballotbench -Fc ballotbench > ballotbench-$(date +%Y%m%d-%H%M).dump
```

`-T` matters: without it Compose allocates a terminal and can mangle the
binary output. I'd run this from cron every hour during an event, and keep
the files off the machine.

To restore, stop the portal, recreate the database empty, load the dump,
start the portal and check the audit chain:

```sh
docker compose stop web
docker compose exec -T db dropdb -U ballotbench ballotbench
docker compose exec -T db createdb -U ballotbench ballotbench
docker compose exec -T db pg_restore -U ballotbench -d ballotbench --no-owner < ballotbench-20260929-1800.dump
docker compose start web
docker compose exec web python manage.py verify_audit
```

Restore into an empty database, not over the live one. `pg_restore` loads
the rows before it creates the triggers, so the audit chain, the deadline
and the other rules come back exactly as they were, and `verify_audit`
should report the chain intact. Losing `webdata` only signs everyone out;
the entrypoint makes a new key.

## Upgrades

```sh
docker compose exec -T db pg_dump -U ballotbench -Fc ballotbench > before-upgrade.dump
git pull
docker compose up -d --build
docker compose logs -f web
```

There's no separate migration step. Every boot runs
`manage.py migrate --noinput`, then the seed, then gunicorn, so a new
version's migrations (including new triggers) are applied when the new
container starts; with no new migrations it's a no-op. If an upgrade goes
wrong, restore the dump you just took rather than trying to migrate
backwards.

The Postgres image is pinned (`postgres:18.4`). A patch release is a change
of tag; a new major version needs a dump and a restore into a fresh volume,
as with any Postgres.

## Management commands

Run them with `docker compose exec web python manage.py <command>`.

### `seed`

Imports the fixture event and creates the open demo event, and with
`BALLOTBENCH_DEMO_SEED=1` the demo accounts. The entrypoint runs it on every
boot. It's idempotent: imported rows are keyed by their fixture ids, so a
second run creates nothing and never overwrites what people changed. You
shouldn't need to run it by hand.

To load another event in the fixture's shape, use the importer from a shell
and give it its own slug:

```sh
docker compose cp other-event.json web:/tmp/other-event.json
docker compose exec web python manage.py shell -c "
import json
from portal.importer import import_event
event, counts = import_event(json.load(open('/tmp/other-event.json')), slug='other-event')
print(event.slug, counts)
"
```

`seed --fixtures other-event.json` looks like the way to do this, but it
names every imported event `sample-hack-2026`, so it fails once the fixture
event exists.

### `createsuperuser`

Creates a site admin, who can do everything an organizer can in every event
and use the Django admin at `/admin/`. You need one when the demo accounts
are off.

```sh
docker compose exec web python manage.py createsuperuser
```

It asks for an email address and a password. From a script:

```sh
docker compose exec -e DJANGO_SUPERUSER_PASSWORD='a long passphrase' web \
  python manage.py createsuperuser --noinput --email you@example.org
```

### `verify_audit`

Recomputes the audit log's hash chain from the first row to the last and
names the first row that doesn't fit: a missing row, a changed `prev_hash`,
or a row edited after it was written. It exits with status 1 if the chain is
broken, so it can run from cron or CI.

```text
audit chain intact: 26 rows, head e1cd7b2acec59727
```

Run it after a restore, before publishing results, and on a schedule. The
database refuses edits to the log from the app and from ordinary SQL; this
catches the one thing that can get past that, a database superuser.

### `normalization_proof`

Recomputes every calibration claim in the method chapter from an event's
scores: which judges carry no weight and why, the agreement test, the
ranking with its intervals, the invariance checks on these scores, and a
benchmark on synthetic events. It takes a few seconds and is deterministic.

```sh
docker compose exec web python manage.py normalization_proof                       # the fixture event
docker compose exec web python manage.py normalization_proof --event your-event    # yours
```

Use it when you want to see the model at work on your own event before you
publish, or to check the book's numbers. It needs an event with reviews.

### `delete_event`

Deletes an event and everything in it. After submissions close, the
database refuses to delete submitted projects, which is what you want
during an event and in the way afterwards; this command is the one
deliberate way round it. It asks for `--yes`, and it writes an
`event.delete` row to the audit log first. The audit rows of the deleted
event stay, since they aren't tied to it by a foreign key.

```sh
docker compose exec web python manage.py delete_event old-hack-2025 --yes
```

## The maintenance flag

Some database rules would stop legitimate maintenance. The fixture's
projects were submitted before a close date that has already passed, so
importing them is, to the deadline trigger, a late submission. Deleting a
closed event means deleting projects after the deadline. The fixture's team
sizes aren't checked against the event's limit either.

For those cases the triggers look at a session setting,
`ballotbench.import`. When it's `on`, the deadline trigger and the
team-size check step aside. Only two pieces of code set it, the fixture
importer and `delete_event`, and both use `SET LOCAL`, so it lasts only
until their own transaction ends, and both write an audit row saying what
they did.

It's not a security boundary. Anyone with SQL access to the database can set
it, or drop a trigger outright. The triggers are there to stop the app, the
admin and a careless shell from breaking the rules by accident. Deliberate
changes by someone with database access are what the audit chain and
`verify_audit` are for. The app connects as the user the Postgres image
creates, which is a superuser, so keep database access to the people who
run the event.

## API tokens

Anyone signed in issues and revokes their own tokens at `/me/tokens`; a
token acts with that person's roles and nothing more, is shown once, and
only its hash is stored. An admin can also issue one for any account from a
shell:

```sh
docker compose exec web python manage.py shell -c "
from portal.auth import issue_token
from portal.models import User
print(issue_token(User.objects.get(email='organizer@example.org'), 'results script'))
"
```

```text
bb_YC5-kg1ErFIezXIBwN0h1Y6ysq9uxVNkEDkaB2_IEmM
```

The label is for you, to tell tokens apart. The token has all of its user's
roles. To revoke it, set its revoked time, from the Django admin (**Api
tokens**) or from a shell:

```sh
docker compose exec web python manage.py shell -c "
from django.utils import timezone
from portal.models import ApiToken
ApiToken.objects.filter(user__email='organizer@example.org', label='results script',
                        revoked_at=None).update(revoked_at=timezone.now())
"
```

A token issued or revoked from a shell isn't in the audit log; one revoked
through the admin is.

## With the network off

Once the images are built, the stack needs no network. The override
`docker-compose.offline.yml` puts both containers on a Docker network with
no route out:

```sh
docker compose down -v
docker compose -f docker-compose.yml -f docker-compose.offline.yml up
```

Docker doesn't publish ports from an internal network, so in this mode the
portal isn't reachable from the host's browser. Check it from inside, as CI
does:

```sh
docker compose exec web python -c "import urllib.request as u; [u.urlopen('http://localhost:8080' + p) for p in ('/projects', '/static/portal/site.css')]"
```

`docker compose up --force-recreate` puts the containers back on the normal
network.

## Known sharp edges

Things I'd want to know before running an event on it:

- An account must confirm its address before it can vote, but anyone with a
  working inbox can sign up. Logins, sign-ups and resets are rate limited;
  a proxy with its own limits is still wise.
- Behind a proxy, set `BALLOTBENCH_TRUSTED_PROXIES` to the number of proxies,
  or the audit log and the rate limits see the proxy's address.
- Mail goes to the outbox table, readable in the admin. For real delivery,
  set `DJANGO_EMAIL_BACKEND` to Django's SMTP backend and configure it.
- Calibration runs inside the organizer's request. On the fixture that's a
  few seconds; there's no background worker.
- Community vote tallies are computed when read. Voiding a ballot after
  publishing changes the public numbers (and is in the audit log).
