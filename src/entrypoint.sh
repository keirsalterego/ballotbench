#!/bin/sh
# Every boot: make a secret key once, migrate, seed (idempotent), serve.
set -eu
if [ -n "${DJANGO_SECRET_KEY_FILE:-}" ] && [ ! -s "$DJANGO_SECRET_KEY_FILE" ]; then
  python -c "import secrets; print(secrets.token_urlsafe(50))" > "$DJANGO_SECRET_KEY_FILE"
fi
python manage.py migrate --noinput
python manage.py seed
exec gunicorn ballotbench.wsgi --bind 0.0.0.0:8080 --workers "${WEB_WORKERS:-3}" --access-logfile -
