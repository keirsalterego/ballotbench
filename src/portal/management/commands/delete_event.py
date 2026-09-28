"""Delete a whole event, closed or not. After submissions close the database
refuses to delete projects, so this is the one deliberate way to remove a
past event: it uses the same maintenance flag as the importer, for its own
transaction only, and leaves an audit row behind (the audit log keeps its
rows; they aren't tied to the event by a foreign key)."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from portal import audit
from portal.models import Event


class Command(BaseCommand):
    help = "Delete an event and everything in it."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--yes", action="store_true", help="really delete it")

    @transaction.atomic
    def handle(self, slug, yes, **options):
        event = Event.objects.filter(slug=slug).first()
        if event is None:
            raise CommandError(f"no event {slug}")
        if not yes:
            raise CommandError(f"this deletes {event.name} with {event.projects.count()} projects; pass --yes")
        counts = {"projects": event.projects.count(), "teams": event.teams.count(),
                  "assignments": event.assignments.count()}
        audit.record("event.delete", event=event, obj=event, before={"name": event.name, **counts})
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ballotbench.import = 'on'")
        Event.objects.filter(published_run__isnull=False, pk=event.pk).update(published_run=None)
        event.delete()
        self.stdout.write(f"deleted {slug}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
