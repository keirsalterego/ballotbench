"""manage.py import_event bundle.json --slug new-slug [--name "New name"]: a bundle (or a file in
the fixture's shape) as a new event, in one transaction."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import APIException

from portal.bundles import bundle_import


class Command(BaseCommand):
    help = "Create a new event from a JSON bundle."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--slug", required=True, help="the new event's slug")
        parser.add_argument("--name", default="", help="the new event's name (default: the bundle's, plus \" (imported)\")")

    def handle(self, *args, file, slug, name, **options):
        try:
            data = json.loads(Path(file).read_text())
            event, counts = bundle_import(data, slug, name=name)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        except APIException as exc:
            raise CommandError(str(exc.detail)) from exc
        self.stdout.write(f"imported {event.slug}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
