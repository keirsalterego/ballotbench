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


def test_client_ip_trusts_only_the_configured_proxies(monkeypatch, rf):
    from portal.audit import client_ip
    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9", REMOTE_ADDR="10.0.0.2")
    assert client_ip(request) == "10.0.0.2"                    # default: no proxy trusted
    monkeypatch.setenv("BALLOTBENCH_TRUSTED_PROXIES", "1")
    assert client_ip(request) == "203.0.113.9"                 # what our one proxy saw, not the spoofed 6.6.6.6
    monkeypatch.setenv("BALLOTBENCH_TRUSTED_PROXIES", "1")
    assert client_ip(rf.get("/", HTTP_X_FORWARDED_FOR="nonsense", REMOTE_ADDR="10.0.0.2")) == "10.0.0.2"


def test_proof_handles_an_event_with_no_reviews(db):
    import io
    from django.core.management import call_command
    out = io.StringIO()
    call_command("normalization_proof", "--event", "demo-open", stdout=out)
    assert "nothing to calibrate" in out.getvalue()


def test_issue_use_and_revoke_your_own_token(web):
    import re
    from django.test import Client
    client = web("judge_a")
    client.post("/me/tokens", {"label": "script"})
    token = re.search(r"<pre>(bb_[\w-]+)</pre>", client.get("/me/tokens").content.decode()).group(1)
    api = Client(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api.get("/api/judge/scores").status_code == 200
    from portal.models import ApiToken
    row = ApiToken.objects.get(label="script")
    assert row.token_hash != token
    assert web("judge_b").post(f"/me/tokens/{row.pk}/revoke").status_code == 404     # not yours
    client.post(f"/me/tokens/{row.pk}/revoke")
    assert api.get("/api/judge/scores").status_code == 401
    assert AuditLog.objects.filter(action__in=["token.issue", "token.revoke"]).count() >= 2


def test_new_token_is_shown_once(web):
    client = web("participant")
    client.post("/me/tokens", {"label": "once"})
    assert "<pre>bb_" in client.get("/me/tokens").content.decode()
    assert "<pre>bb_" not in client.get("/me/tokens").content.decode()


def test_password_reset_goes_to_the_inbox_and_works_once(web):
    import re
    from django.core import mail
    web().post("/password-reset", {"email": "priya1@example.org"})
    message = mail.outbox[-1]                      # the test runner's in-memory mailbox
    assert message.to == ["priya1@example.org"]
    link = re.search(r"/password-reset/[\w-]+/[\w-]+", message.body).group(0)
    client = web()
    form_url = client.get(link, follow=True).redirect_chain[-1][0]
    response = client.post(form_url, {"new_password1": "a brand new passphrase", "new_password2": "a brand new passphrase"})
    assert response.status_code == 302
    assert web().login(email="priya1@example.org", password="a brand new passphrase")
    assert AuditLog.objects.filter(action="user.password_reset").exists()
    assert "doesn" in web().get(link, follow=True).content.decode()        # used once


def test_password_reset_page_reads_the_same_for_unknown_addresses(web):
    from django.core import mail
    before = len(mail.outbox)
    response = web().post("/password-reset", {"email": "nobody-here@example.org"})
    assert response.status_code == 302 and response.url == "/password-reset/sent"
    assert len(mail.outbox) == before
