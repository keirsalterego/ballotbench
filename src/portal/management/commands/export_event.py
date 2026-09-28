"""manage.py export_event <slug> > bundle.json: the whole event as a bundle."""
import json

from django.core.management.base import BaseCommand, CommandError

from portal import audit
from portal.bundles import export_bundle
from portal.models import Event


class Command(BaseCommand):
    help = "Print an event as a JSON bundle (see portal/bundles.py)."

    def add_arguments(self, parser):
        parser.add_argument("slug")

    def handle(self, *args, slug, **options):
        event = Event.objects.filter(slug=slug).first()
        if event is None:
            raise CommandError(f"no event called {slug}")
        bundle = export_bundle(event)
        audit.record("export.bundle", event=event, obj=event, after={"via": "manage.py export_event"})
        self.stdout.write(json.dumps(bundle, ensure_ascii=False, indent=1))
