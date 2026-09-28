from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = "portal"

    def ready(self):
        from . import schema  # noqa: F401  registers the OpenAPI auth extension
