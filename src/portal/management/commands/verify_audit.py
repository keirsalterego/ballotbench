"""Recompute the audit hash chain and name the first row that doesn't fit.
Exit status 1 if the chain is broken, so it can run in CI or cron."""
import sys

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Check the audit log's hash chain from the first row to the last."

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute("""
                SELECT seq, prev_hash, row_hash, portal_audit_hash(a) AS expected
                  FROM portal_auditlog a ORDER BY seq""")
            previous, count, expected_seq = "", 0, 1
            for seq, prev_hash, row_hash, expected in cur:
                count += 1
                if seq != expected_seq:
                    return self.fail(f"row {expected_seq} is missing (next row is {seq})")
                if prev_hash != previous:
                    return self.fail(f"row {seq} doesn't follow row {seq - 1}: its prev_hash was changed")
                if row_hash != expected:
                    return self.fail(f"row {seq} was edited after it was written")
                previous, expected_seq = row_hash, seq + 1
        self.stdout.write(f"audit chain intact: {count} rows, head {previous[:16] or '(empty)'}")

    def fail(self, message):
        self.stderr.write(f"audit chain broken: {message}")
        sys.exit(1)
