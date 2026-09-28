"""Settings come from the environment, so the same image runs the demo and a
real deployment. Nothing here reaches the network: email is kept in the
database for site admins to read, and every static file is served from the
image."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
env = os.environ.get


def _secret_key():
    if key := env("DJANGO_SECRET_KEY"):
        return key
    # The entrypoint writes a random key to the data volume on first boot, so
    # sessions survive a restart without a key ever living in the repo.
    if path := env("DJANGO_SECRET_KEY_FILE"):
        return Path(path).read_text().strip()
    if env("DJANGO_DEBUG") == "1":
        return "insecure-dev-key"
    raise RuntimeError("set DJANGO_SECRET_KEY or DJANGO_SECRET_KEY_FILE")


SECRET_KEY = _secret_key()
DEBUG = env("DJANGO_DEBUG") == "1"
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")

INSTALLED_APPS = [
    # First, so its registration/ templates (password reset) win over the admin's.
    "portal",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "portal.auth.BearerTokenMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ballotbench.urls"
WSGI_APPLICATION = "ballotbench.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": env("POSTGRES_DB", "ballotbench"),
    "USER": env("POSTGRES_USER", "ballotbench"),
    "PASSWORD": env("POSTGRES_PASSWORD", "ballotbench"),
    "HOST": env("POSTGRES_HOST", "localhost"),
    "PORT": env("POSTGRES_PORT", "5432"),
    # Every request is one transaction, so an audit row and the change it
    # records commit together or not at all.
    "ATOMIC_REQUESTS": True,
}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "portal.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "gallery"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "portal.mail.OutboxBackend")

# Behind a TLS-terminating proxy, set DJANGO_SECURE=1: cookies are sent over
# HTTPS only, browsers are told to stay on HTTPS, and the proxy's
# X-Forwarded-Proto header is trusted. Off by default so the laptop demo works
# on plain http://localhost.
if env("DJANGO_SECURE") == "1":
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h and not h.startswith("[")]

# The Ed25519 key that signs participation records, made on first use. In
# Docker it lives on the data volume; in development, in the ignored data/.
SIGNING_KEY_FILE = env("BALLOTBENCH_SIGNING_KEY_FILE", str(BASE_DIR.parent / "data" / "signing_key.pem"))

# Webhooks go only to public addresses, unless this is 1 (say, for a receiver
# on the same private network). See portal/webhooks.py.
WEBHOOKS_ALLOW_PRIVATE = env("BALLOTBENCH_WEBHOOKS_ALLOW_PRIVATE") == "1"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "portal.auth.BearerTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ballotbench API",
    "DESCRIPTION": "Hackathon submissions and judging. Authenticate with `Authorization: Bearer <token>`.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
