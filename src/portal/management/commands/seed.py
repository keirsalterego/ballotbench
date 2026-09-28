"""Runs on every boot. With BALLOTBENCH_DEMO_SEED=1 (the compose default) it
imports the fixture event, creates the open demo event, and the demo accounts
with the fixed tokens .dogfood.toml and the README use. With anything else it
loads nothing at all: a real deployment starts empty and makes its first
admin with `manage.py createsuperuser`. Safe to run any number of times."""
import json
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

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
        if os.environ.get("BALLOTBENCH_DEMO_SEED") != "1":
            self.stdout.write("demo seed off (BALLOTBENCH_DEMO_SEED is not 1): nothing loaded. "
                              "Make the first admin with `manage.py createsuperuser`.")
            return
        data = json.loads(Path(fixtures).read_text())
        event, counts = import_event(data, slug=self.slug_for(data["event"]))
        self.stdout.write(f"fixture event {event.slug}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        demo = self.demo_event(data)
        self.demo_accounts(event, demo)

    def slug_for(self, ev):
        """The Dogfood fixture keeps the slug .dogfood.toml points at; any other
        fixture file gets one from its name, made unique if it's taken."""
        base = FIXTURE_SLUG if ev["id"] == "evt_01" else slugify(ev["name"])
        if Event.objects.filter(slug=base).exclude(external_id=ev["id"]).exists():
            base = f"{base}-{slugify(ev['id'])}"
        return base

    def demo_event(self, data):
        """An empty event that is open now, for trying the whole flow. Created
        once; its dates are never touched again."""
        now = timezone.now()
        demo, created = Event.objects.get_or_create(slug=DEMO_SLUG, defaults=dict(
            name="Demo Hack (open)", description="An open event for trying the full lifecycle.",
            submissions_open=now - timedelta(days=1), submissions_close=now + timedelta(days=14),
            judging_open=now + timedelta(days=14), judging_close=now + timedelta(days=21),
            voting_mode=Event.VotingMode.ACCOUNT, vote_credits=25,
            voting_open=now + timedelta(days=14), voting_close=now + timedelta(days=21)))
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
            # A fresh row has an empty password, which Django counts as
            # usable; an imported one has an unusable one. Set both, but never
            # overwrite a password someone chose.
            if not user.password or not user.has_usable_password():
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
