"""manage.py deliver_webhooks [--once]: send queued webhook deliveries,
forever (the compose file runs it as its own service) or just once. If the
database goes away it exits, and compose starts it again."""
import time

from django.core.management.base import BaseCommand

from portal.webhooks import deliver_due


class Command(BaseCommand):
    help = "Send due webhook deliveries, retrying failures with backoff."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="send what is due now, then stop")
        parser.add_argument("--interval", type=float, default=2.0, help="seconds between looks at the queue")

    def handle(self, *args, once, interval, **options):
        while True:
            if tried := deliver_due():
                self.stdout.write(f"tried {tried} deliver{'y' if tried == 1 else 'ies'}")
            if once:
                return
            time.sleep(interval)
