"""API tokens: `Authorization: Bearer <token>`.

Tokens are random, shown once, and stored only as SHA-256. A bearer token
isn't a cookie, so a browser never attaches it on its own and the API needs
no CSRF check for it; session-authenticated API calls still get one."""
import hashlib
import secrets

from django.http import HttpResponse
from rest_framework import authentication, exceptions

from .models import ApiToken

PREFIX = "bb_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(user, label: str, token: str | None = None) -> str:
    """Create a token for `user` and return it. Pass `token` only for the
    fixed demo tokens the seed prints."""
    token = token or PREFIX + secrets.token_urlsafe(32)
    ApiToken.objects.create(user=user, label=label, token_hash=hash_token(token))
    return token


class BearerTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != self.keyword.lower():
            return None
        token = token.strip()
        if not token:
            raise exceptions.AuthenticationFailed("empty bearer token")
        row = (ApiToken.objects.select_related("user")
               .filter(token_hash=hash_token(token), revoked_at__isnull=True).first())
        if row is None or not row.user.is_active:
            raise exceptions.AuthenticationFailed("invalid or revoked token")
        return row.user, row

    def authenticate_header(self, request):
        # Makes DRF answer 401 (with WWW-Authenticate) rather than 403 when
        # no credentials were sent.
        return self.keyword


class BearerTokenMiddleware:
    """Pages accept the same bearer tokens as the API, for reading only, so a
    script (or the isolation probe) sees the same role checks a browser does.
    A token never changes anything through a page: page writes need a
    signed-in session, so a leaked token can't mint new tokens at /me/tokens
    or use the admin. Scripts that write use the API."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            return self.get_response(request)        # DRF authenticates API calls itself
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            row = (ApiToken.objects.select_related("user")
                   .filter(token_hash=hash_token(token.strip()), revoked_at__isnull=True).first())
            if row is None or not row.user.is_active:
                return HttpResponse("invalid or revoked token\n", status=401, content_type="text/plain")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return HttpResponse("a token can read pages but not change anything through them; use the API\n",
                                    status=403, content_type="text/plain")
            if request.path.startswith("/admin/") or request.path.startswith("/me/tokens"):
                return HttpResponse("sign in to use this page\n", status=403, content_type="text/plain")
            request.user = row.user
        return self.get_response(request)
