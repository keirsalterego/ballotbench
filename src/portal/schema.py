"""Teach drf-spectacular about our bearer tokens, so the OpenAPI document
says how to authenticate."""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = "portal.auth.BearerTokenAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer",
                "description": "An API token. The demo seed prints four; anyone signed in can issue their own at /me/tokens."}
