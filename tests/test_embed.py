"""The embeddable gallery: public, frameable, and blind to whoever is signed
in, so a draft can't leak into someone else's page."""
import pytest
from django.db import connection

from portal.models import Project

from .conftest import EVENT

pytestmark = pytest.mark.django_db


def make_draft():
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    project = Project.objects.filter(event__slug=EVENT).order_by("pk").first()
    Project.objects.filter(pk=project.pk).update(status="draft", submitted_at=None, title="Secret Draft Title")
    return project


def test_embed_is_public_and_frameable(web):
    response = web().get(f"/embed/{EVENT}")
    assert response.status_code == 200 and b"Glass Signal" in response.content
    assert "X-Frame-Options" not in response
    assert response["Content-Security-Policy"] == "frame-ancestors *"
    assert b"<header" not in response.content and b"Log in" not in response.content


@pytest.mark.parametrize("path", ["/projects", "/login", f"/events/{EVENT}/results", "/verify", "/embed/no-such"])
def test_every_other_page_refuses_framing(web, path):
    response = web().get(path)
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-ancestors *" not in response.get("Content-Security-Policy", "")


@pytest.mark.parametrize("role", [None, "organizer", "participant"])
def test_drafts_never_appear_whoever_is_signed_in(web, api, role):
    make_draft()
    for client in (web(role), api(role)):
        body = client.get(f"/embed/{EVENT}").content.decode()
        assert "Secret Draft Title" not in body and "submitted projects" in body


def test_results_only_once_published(web):
    organizer = web("organizer")
    organizer.post(f"/events/{EVENT}/manage/calibration")
    assert b"Results" not in organizer.get(f"/embed/{EVENT}").content      # not even for the organizer
    organizer.post(f"/events/{EVENT}/manage/publish")
    body = web().get(f"/embed/{EVENT}").content.decode()
    assert "<h2>Results</h2>" in body and "1. " in body


def test_embed_script(web):
    response = web().get("/embed.js")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
    body = response.content.decode()
    assert "e.origin !== origin" in body and "e.source !== frame.contentWindow" in body
