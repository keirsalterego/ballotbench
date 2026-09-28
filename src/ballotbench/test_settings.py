import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "tests-only")
os.environ.setdefault("POSTGRES_PORT", "5432")

from .settings import *  # noqa: E402,F401,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast, tests only
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
SIGNING_KEY_FILE = os.path.join(tempfile.mkdtemp(prefix="bb-test-"), "signing_key.pem")
