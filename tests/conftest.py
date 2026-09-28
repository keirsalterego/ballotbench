"""Every test runs against real Postgres, because the triggers are part of
what's under test. The fixture event and the demo accounts are seeded once
per session, exactly as the container does on boot."""
import os
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import Client

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures.json"
TOKENS = {
    "organizer": "bb_demo_organizer_5c1e0a",
    "judge_a": "bb_demo_judge_a_8d24f1",
    "judge_b": "bb_demo_judge_b_3a9e77",
    "participant": "bb_demo_participant_61b0c4",
}
EMAILS = {
    "admin": "admin@ballotbench.local",
    "organizer": "organizer@ballotbench.local",
    "judge_a": "diego.herrera@example.org",
    "judge_b": "ines.rocha@example.org",
    "participant": "priya1@example.org",
}
EVENT = "sample-hack-2026"


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    os.environ["BALLOTBENCH_DEMO_SEED"] = "1"
    with django_db_blocker.unblock():
        call_command("seed", fixtures=str(FIXTURES), stdout=open(os.devnull, "w"))


@pytest.fixture
def api(db):
    """api("judge_a") is a client that sends judge_a's bearer token."""
    def make(role=None):
        if role is None:
            return Client()
        return Client(HTTP_AUTHORIZATION=f"Bearer {TOKENS[role]}")
    return make


@pytest.fixture
def web(db):
    """web("organizer") is a client logged in with a session, like a browser."""
    def make(role=None):
        client = Client()
        if role is not None:
            assert client.login(email=EMAILS[role], password="ballotbench-demo")
        return client
    return make
