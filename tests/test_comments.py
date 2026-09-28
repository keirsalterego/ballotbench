"""Comments: who may write them, who may hide them, and that a hidden one is
gone for everyone but the organizers, on the page and in the API."""
import pytest

from portal import comments
from portal.models import AuditLog, Comment, Project, User

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db
XSS = '<script>alert("pwned")</script><img src=x onerror=alert(1)>'


@pytest.fixture
def project():
    return Project.objects.filter(event__slug=EVENT, status="submitted").order_by("pk").first()


def post(client, project, body):
    return client.post(f"/api/projects/{project.pk}/comments", {"body": body}, content_type="application/json")


def test_signed_in_users_comment_on_the_page_and_the_api(web, api, project):
    assert web("participant").post(f"/projects/{project.pk}/comments", {"body": "Lovely demo."}).status_code == 302
    response = post(api("judge_b"), project, "The repo link is broken.")
    assert response.status_code == 201 and response.json()["body"] == "The repo link is broken."
    listed = api().get(f"/api/projects/{project.pk}/comments").json()
    assert [c["body"] for c in listed] == ["Lovely demo.", "The repo link is broken."]
    assert "Lovely demo." in web().get(f"/projects/{project.pk}").content.decode()
    assert AuditLog.objects.filter(action="comment.create", event=project.event).count() == 2


def test_anonymous_cant_comment(web, api, project):
    assert post(api(), project, "hi").status_code == 401
    assert web().post(f"/projects/{project.pk}/comments", {"body": "hi"}).status_code == 302   # to the login page
    assert not Comment.objects.filter(project=project).exists()


def test_empty_and_over_long_bodies_are_refused(api, web, project):
    assert post(api("participant"), project, "   ").status_code == 400
    assert post(api("participant"), project, "x" * 2001).status_code == 400
    assert post(api("participant"), project, "x" * 2000).status_code == 201
    web("participant").post(f"/projects/{project.pk}/comments", {"body": "y" * 2001})
    assert not Comment.objects.filter(body="y" * 2001).exists()


def test_the_database_caps_the_length_too(project):
    from django.db import IntegrityError, transaction
    with pytest.raises(IntegrityError), transaction.atomic():
        Comment.objects.create(project=project, author=User.objects.get(email=EMAILS["participant"]), body="z" * 2001)


def test_drafts_take_no_comments(api):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ballotbench.import = 'on'")
    own = Project.objects.filter(event__slug=EVENT, team__members__user__email=EMAILS["participant"]).first()
    Project.objects.filter(pk=own.pk).update(status="draft", submitted_at=None)
    assert post(api("participant"), own, "note to self").status_code == 409


def test_comments_are_escaped(web, api, project):
    post(api("participant"), project, XSS)
    page = web().get(f"/projects/{project.pk}").content.decode()
    assert XSS not in page and "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;pwned&quot;)&lt;/script&gt;" in page


def test_organizers_hide_and_unhide_and_hidden_means_gone_for_everyone_else(web, api, project):
    comment = Comment.objects.get(pk=post(api("participant"), project, "Spam, buy now").json()["id"])
    organizer = web("organizer")
    assert organizer.post(f"/comments/{comment.pk}/moderate", {"action": "hide"}).status_code == 302
    for role in (None, "participant", "judge_a"):
        assert "Spam, buy now" not in web(role).get(f"/projects/{project.pk}").content.decode()
        assert api(role).get(f"/api/projects/{project.pk}/comments").json() == []
    page = organizer.get(f"/projects/{project.pk}").content.decode()
    assert "Spam, buy now" in page and "Hidden from everyone but the organizers" in page
    assert api("organizer").get(f"/api/projects/{project.pk}/comments").json()[0]["hidden"] is True
    organizer.post(f"/comments/{comment.pk}/moderate", {"action": "unhide"})
    assert "Spam, buy now" in web().get(f"/projects/{project.pk}").content.decode()
    actions = list(AuditLog.objects.filter(object_type="comment", object_id=str(comment.pk))
                   .order_by("seq").values_list("action", flat=True))
    assert actions == ["comment.create", "comment.hide", "comment.unhide"]


def test_only_the_events_organizers_can_hide(web, api, project):
    comment = Comment.objects.get(pk=post(api("participant"), project, "fine").json()["id"])
    for role in ("participant", "judge_a"):
        assert web(role).post(f"/comments/{comment.pk}/moderate", {"action": "hide"}).status_code == 403
    assert Comment.objects.get(pk=comment.pk).hidden_at is None


def test_comments_are_rate_limited(api, monkeypatch, project):
    monkeypatch.setattr(comments, "COMMENT_LIMIT", (2, 600))
    client = api("participant")
    assert [post(client, project, f"c{i}").status_code for i in range(3)] == [201, 201, 429]
    assert post(api("judge_a"), project, "someone else").status_code == 201, "the limit is per account"
