"""Runs on every boot: import the fixture event, create the open demo event
and, with BALLOTBENCH_DEMO_SEED=1, the demo accounts with fixed tokens that
.dogfood.toml and the README use. Safe to run any number of times."""
import json
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from portal import audit
from portal.auth import hash_token, issue_token
from portal.importer import CRITERIA, import_event
from portal.models import ApiToken, Event, Membership, RubricCriterion, Track, User

DEMO_PASSWORD = "ballotbench-demo"
FIXTURE_SLUG = "sample-hack-2026"
DEMO_SLUG = "demo-open"
# judge_a and judge_b both have three or more fixture reviews and share no
# project, so each has scores the other must not see.
DEMO_ACCOUNTS = [
    ("organizer", "organizer@ballotbench.local", "Olu Organizer", "bb_demo_organizer_5c1e0a"),
    ("judge_a", "diego.herrera@example.org", None, "bb_demo_judge_a_8d24f1"),
    ("judge_b", "ines.rocha@example.org", None, "bb_demo_judge_b_3a9e77"),
    ("participant", "priya1@example.org", None, "bb_demo_participant_61b0c4"),
]


class Command(BaseCommand):
    help = "Import fixtures.json and create the demo event and accounts (idempotent)."

    def add_arguments(self, parser):
        default = os.environ.get("BALLOTBENCH_FIXTURES", str(Path(settings.BASE_DIR).parent / "fixtures.json"))
        parser.add_argument("--fixtures", default=default)

    @transaction.atomic
    def handle(self, *args, fixtures, **options):
        data = json.loads(Path(fixtures).read_text())
        event, counts = import_event(data, slug=FIXTURE_SLUG)
        self.stdout.write(f"fixture event {event.slug}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        demo = self.demo_event(data)
        if os.environ.get("BALLOTBENCH_DEMO_SEED") == "1":
            self.demo_accounts(event, demo)

    def demo_event(self, data):
        """An empty event that is open now, for trying the whole flow. Created
        once; its dates are never touched again."""
        now = timezone.now()
        demo, created = Event.objects.get_or_create(slug=DEMO_SLUG, defaults=dict(
            name="Demo Hack (open)", description="An open event for trying the full lifecycle.",
            submissions_open=now - timedelta(days=1), submissions_close=now + timedelta(days=14),
            judging_open=now + timedelta(days=14), judging_close=now + timedelta(days=21)))
        if created:
            for t in data["tracks"][:3]:
                Track.objects.create(event=demo, name=t["name"])
            for position, (key, name) in enumerate(CRITERIA):
                RubricCriterion.objects.create(event=demo, key=key, name=name, position=position)
            audit.record("event.create", event=demo, obj=demo, after={"name": demo.name, "seeded": True})
        return demo

    def demo_accounts(self, fixture_event, demo):
        admin, _ = User.objects.get_or_create(email="admin@ballotbench.local",
                                              defaults={"name": "Ada Admin", "is_staff": True, "is_superuser": True})
        users = {"admin": admin}
        for role, email, name, token in DEMO_ACCOUNTS:
            user, _ = User.objects.get_or_create(email=email, defaults={"name": name or ""})
            users[role] = user
            if not ApiToken.objects.filter(token_hash=hash_token(token)).exists():
                issue_token(user, "demo", token)
        for user in users.values():
            if not user.has_usable_password():
                user.set_password(DEMO_PASSWORD)
                user.save(update_fields=["password"])
        for event in (fixture_event, demo):
            Membership.objects.get_or_create(user=users["organizer"], event=event, role=Membership.Role.ORGANIZER)
        Membership.objects.get_or_create(user=users["judge_a"], event=demo, role=Membership.Role.JUDGE)
        Membership.objects.get_or_create(user=users["participant"], event=demo, role=Membership.Role.PARTICIPANT)

        self.stdout.write("seeded. test logins:")
        for role, _, _, token in DEMO_ACCOUNTS:
            self.stdout.write(f"  {role:<12} Authorization: Bearer {token}")
        people = ", ".join(f"{users[r].email} ({r})" for r in ("admin", "organizer", "judge_a", "judge_b", "participant"))
        self.stdout.write(f"  web logins, password '{DEMO_PASSWORD}': {people}")
