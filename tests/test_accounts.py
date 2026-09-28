"""Sign-up and sign-in."""
import pytest

from portal.models import AuditLog, User

pytestmark = pytest.mark.django_db


def signup(client, email, next_url=""):
    return client.post(f"/signup?next={next_url}", {"name": "N", "email": email, "password": "long enough password"})


def test_signup_creates_a_lowercased_account_and_logs_in(web):
    client = web()
    response = signup(client, "New.Person@Example.org")
    assert response.status_code == 302 and response.url == "/me"
    assert User.objects.filter(email="new.person@example.org").exists()
    assert client.get("/me").status_code == 200
    assert AuditLog.objects.filter(action="user.signup").exists()


def test_signup_never_redirects_off_site(web):
    for evil in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
        response = signup(web(), f"x{abs(hash(evil))}@example.org", evil)
        assert response.status_code == 302 and "evil" not in response.url


def test_signup_follows_a_local_next(web):
    response = signup(web(), "local@example.org", "/events/demo-open/me")
    assert response.url == "/events/demo-open/me"


def test_duplicate_email_is_refused_case_insensitively(web):
    response = signup(web(), "PRIYA1@example.org")
    assert response.status_code == 200 and "already exists" in response.content.decode()


def test_passwords_are_hashed(web):
    signup(web(), "hash@example.org")
    assert not User.objects.get(email="hash@example.org").password.startswith("long")


def test_login_with_email(web):
    client = web()
    response = client.post("/login", {"username": "priya1@example.org", "password": "ballotbench-demo"})
    assert response.status_code == 302
    assert client.get("/me").status_code == 200


def test_logout_is_post_only(web):
    client = web("participant")
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
