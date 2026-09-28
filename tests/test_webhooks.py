"""Webhooks: queued with the change (and rolled back with it), signed, sent
only to public addresses, retried with backoff, managed by organizers only."""
import hashlib
import hmac
import ipaddress
import json
import socket
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.management import call_command
from django.db import transaction

from portal import audit, webhooks
from portal.access import db_now
from portal.models import Event, Webhook, WebhookDelivery

from .conftest import EMAILS, EVENT

pytestmark = pytest.mark.django_db
PAGE = f"/events/{EVENT}/manage/webhooks"


@pytest.fixture
def event():
    return Event.objects.get(slug=EVENT)


@pytest.fixture
def hook(event):
    return Webhook.objects.create(event=event, url="https://hooks.example.org/in", secret="s3cret")


@pytest.fixture
def dns(monkeypatch):
    """dns({"host": "1.2.3.4"}): answers for getaddrinfo, and no real lookups."""
    def install(table):
        def fake(host, port, *args, **kwargs):
            if host not in table:
                raise socket.gaierror("no such host")
            family = socket.AF_INET6 if ":" in table[host] else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (table[host], port))]
        monkeypatch.setattr(webhooks.socket, "getaddrinfo", fake)
    return install


def test_a_change_queues_a_delivery(web, hook):
    web("organizer").post(f"/events/{EVENT}/manage/tracks", {"name": "Robotics"})
    delivery = WebhookDelivery.objects.get(webhook=hook, action="track.create")
    assert delivery.status == "pending" and delivery.payload["event"] == EVENT
    assert delivery.payload["after"] == {"name": "Robotics"} and delivery.payload["actor"] == EMAILS["organizer"]


def test_filter_and_pause(web, event, hook):
    reviews_only = Webhook.objects.create(event=event, url="https://x.example.org/", secret="x", actions=["review"])
    paused = Webhook.objects.create(event=event, url="https://y.example.org/", secret="y", active=False)
    web("organizer").post(f"/events/{EVENT}/manage/tracks", {"name": "Space"})
    assert WebhookDelivery.objects.filter(webhook=hook).count() == 1
    assert not WebhookDelivery.objects.filter(webhook__in=[reviews_only, paused]).exists()


def test_rolled_back_change_is_never_announced(event, hook):
    with pytest.raises(RuntimeError), transaction.atomic():
        audit.record("track.create", event=event, obj=event, after={"name": "Ghost"})
        assert WebhookDelivery.objects.filter(webhook=hook).count() == 1     # queued with the change...
        raise RuntimeError
    assert not WebhookDelivery.objects.filter(webhook=hook).exists()         # ...and gone with it


def test_signature_verifies(event, hook):
    audit.record("project.submit", event=event, obj=event, after={"title": "X"})
    delivery = WebhookDelivery.objects.get(webhook=hook)
    sent = {}

    def send(url, body, headers):
        sent.update(url=url, body=body, headers=headers)
        return 204
    assert webhooks.attempt(delivery, send)
    expected = "sha256=" + hmac.new(b"s3cret", sent["body"], hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, sent["headers"]["X-Ballotbench-Signature"])
    assert json.loads(sent["body"])["action"] == "project.submit"
    assert not hmac.compare_digest(expected, webhooks.signature("wrong", sent["body"]))
    delivery.refresh_from_db()
    assert delivery.status == "delivered" and delivery.attempts == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://10.1.2.3/x", "http://192.168.0.1/",
                                 "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://[fe80::1]/",
                                 "http://[::ffff:127.0.0.1]/", "http://100.64.0.1/", "http://0.0.0.0/",
                                 "ftp://example.org/", "file:///etc/passwd", "http:///nohost",
                                 # IPv6 spellings of IPv4 addresses, and site-local IPv6
                                 "http://[::7f00:1]/", "http://[::ffff:0:7f00:1]/", "http://[64:ff9b::a00:1]/",
                                 "http://[64:ff9b::a9fe:a9fe]/", "http://[fec0::1]/"])
def test_private_and_odd_urls_are_refused(url):
    with pytest.raises(webhooks.Refused):
        webhooks.resolve(url)


def test_an_ipv4_inside_ipv6_is_judged_by_the_ipv4():
    """NAT64 to a public address is fine; it's where the packet goes."""
    assert webhooks.public(ipaddress.ip_address("64:ff9b::5db8:d822"))          # 93.184.216.34
    assert webhooks.public(ipaddress.ip_address("2606:4700::1111"))
    assert not webhooks.public(ipaddress.ip_address("::ffff:0:0"))


def test_a_name_that_resolves_privately_is_refused(dns):
    dns({"internal.example.org": "10.0.0.5", "public.example.org": "93.184.216.34"})
    with pytest.raises(webhooks.Refused, match="private"):
        webhooks.resolve("https://internal.example.org/hook")
    assert webhooks.resolve("https://public.example.org/hook")[2] == "93.184.216.34"


def test_allow_private_lifts_the_check(settings):
    settings.WEBHOOKS_ALLOW_PRIVATE = True
    assert webhooks.resolve("http://127.0.0.1:9/")[2] == "127.0.0.1"


def test_refused_at_save_time(web, dns):
    dns({"internal.example.org": "192.168.1.1"})
    response = web("organizer").post(PAGE, {"url": "https://internal.example.org/", "actions": ""})
    assert response.status_code == 422 and b"private address" in response.content
    assert not Webhook.objects.exists()


def test_refused_again_at_send_time(event, hook, dns, monkeypatch):
    """The name was public when saved; now it points inside. Nothing connects."""
    dns({"hooks.example.org": "127.0.0.1"})
    monkeypatch.setattr(webhooks.socket, "create_connection", lambda *a, **k: pytest.fail("connected"))
    audit.record("track.create", event=event, obj=event)
    assert webhooks.deliver_due() == 1
    delivery = WebhookDelivery.objects.get(webhook=hook)
    assert delivery.status == "pending" and "private address" in delivery.last_error


def test_retry_backoff_then_give_up(event, hook):
    audit.record("track.create", event=event, obj=event)
    delivery = WebhookDelivery.objects.get(webhook=hook)
    for n in range(1, webhooks.MAX_ATTEMPTS + 1):
        before = db_now()
        assert not webhooks.attempt(delivery, lambda *a: 503)
        delivery.refresh_from_db()
        assert delivery.attempts == n and delivery.last_error == "answered HTTP 503"
        if n < webhooks.MAX_ATTEMPTS:
            wait = delivery.next_attempt_at - before
            assert delivery.status == "pending"
            assert timedelta(seconds=webhooks.BACKOFF * 2 ** (n - 1)) <= wait < timedelta(
                seconds=webhooks.BACKOFF * 2 ** (n - 1) + 5)
    assert delivery.status == "failed"


def test_only_due_deliveries_are_sent(event, hook):
    audit.record("track.create", event=event, obj=event)
    calls = []
    assert webhooks.deliver_due(lambda *a: calls.append(a) or 500) == 1
    assert webhooks.deliver_due(lambda *a: calls.append(a) or 200) == 0      # backing off
    WebhookDelivery.objects.update(next_attempt_at=db_now())
    assert webhooks.deliver_due(lambda *a: calls.append(a) or 200) == 1
    assert WebhookDelivery.objects.get().status == "delivered" and len(calls) == 2


def test_real_delivery_over_http(settings, event):
    """End to end on loopback: the command POSTs, the receiver checks the
    signature. Needs ALLOW_PRIVATE, as any receiver on this machine would."""
    settings.WEBHOOKS_ALLOW_PRIVATE = True
    got = {}

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.update(body=body, signature=self.headers["X-Ballotbench-Signature"])
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    threading.Thread(target=server.handle_request, daemon=True).start()
    Webhook.objects.create(event=event, url=f"http://127.0.0.1:{server.server_port}/hook", secret="k")
    audit.record("results.publish", event=event, obj=event)
    call_command("deliver_webhooks", "--once", stdout=open("/dev/null", "w"))
    server.server_close()
    assert hmac.compare_digest(webhooks.signature("k", got["body"]), got["signature"])
    assert WebhookDelivery.objects.get().status == "delivered"


def test_redirects_are_not_followed(settings, event):
    settings.WEBHOOKS_ALLOW_PRIVATE = True

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.handle_request, daemon=True).start()
    Webhook.objects.create(event=event, url=f"http://127.0.0.1:{server.server_port}/", secret="k")
    audit.record("results.publish", event=event, obj=event)
    webhooks.deliver_due()
    server.server_close()
    assert WebhookDelivery.objects.get().last_error == "answered HTTP 307"


def test_organizers_only(web, api, event, hook, dns):
    assert web().get(PAGE).status_code == 302
    for role in ("participant", "judge_a"):
        assert web(role).get(PAGE).status_code == 403
        assert api(role).get(PAGE).status_code == 403
        assert web(role).post(f"{PAGE}/{hook.pk}/toggle").status_code == 403
        assert web(role).post(f"{PAGE}/{hook.pk}/delete").status_code == 403
    organizer = web("organizer")
    assert organizer.get(PAGE).status_code == 200
    # Another event's URL can't reach this event's webhook.
    assert organizer.post(f"/events/demo-open/manage/webhooks/{hook.pk}/toggle").status_code == 404
    organizer.post(f"{PAGE}/{hook.pk}/toggle")
    hook.refresh_from_db()
    assert hook.active is False
    dns({"hooks.example.net": "93.184.216.34"})
    organizer.post(PAGE, {"url": "https://hooks.example.net/in", "actions": "project, review.submit"})
    made = Webhook.objects.get(url="https://hooks.example.net/in")
    assert made.actions == ["project", "review.submit"] and len(made.secret) == 64
    assert made.secret not in json.dumps(list(audit.AuditLog.objects.filter(action="webhook.create").values("after")))
    organizer.post(f"{PAGE}/{hook.pk}/delete")
    assert not Webhook.objects.filter(pk=hook.pk).exists()


@pytest.fixture
def raw_receiver():
    """raw_receiver(answer) is the URL of a one-shot receiver on loopback that
    reads the request, then calls answer(send), send(bytes) writing to it."""
    listener = socket.create_server(("127.0.0.1", 0))

    def start(answer):
        def serve():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.recv(65536)
                    answer(conn.sendall)
            except OSError:
                pass                    # the sender hung up, as it should
        threading.Thread(target=serve, daemon=True).start()
        return f"http://127.0.0.1:{listener.getsockname()[1]}/"
    yield start
    listener.close()


def drip(data, every):
    def answer(send):
        for byte in data:
            time.sleep(every)
            send(bytes([byte]))
    return answer


@pytest.mark.parametrize("answer, error", [
    pytest.param(drip(b"HTTP/1.1 200 OK\r\n\r\n", every=0.3), "TimeoutError", id="slow-status-line"),
    pytest.param(lambda send: (send(b"HTTP/1.1 200 OK\r\n"), drip(b"X-Slow: y\r\n\r\n", every=0.3)(send)), "",
                 id="slow-headers"),
    pytest.param(lambda send: send(b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 204 No Content\r\n\r\n"), "",
                 id="interim-answer"),
    pytest.param(lambda send: send(b"x" * 100_000), "HTTPException: no status line", id="endless-head"),
])
def test_an_attempt_has_one_deadline_and_reads_only_the_head(settings, event, monkeypatch, raw_receiver,
                                                              answer, error):
    """A socket timeout counts each read afresh, so a receiver dripping a byte
    at a time within it could hold the one sender for ever."""
    settings.WEBHOOKS_ALLOW_PRIVATE = True
    monkeypatch.setattr(webhooks, "TIMEOUT", 2)
    hook = Webhook.objects.create(event=event, url=raw_receiver(answer), secret="k")
    delivery = WebhookDelivery.objects.create(webhook=hook, action="x", payload={})
    started = time.monotonic()
    assert webhooks.attempt(delivery) == (not error)
    assert time.monotonic() - started < webhooks.TIMEOUT + 1
    delivery.refresh_from_db()
    assert delivery.last_error.startswith(error)


def test_a_slow_receiver_holds_up_no_other(event, hook):
    """Sends go out together, one per webhook a round: the slow one here only
    answers once the other has been sent, which it couldn't if they queued."""
    other = Webhook.objects.create(event=event, url="https://other.example.org/", secret="o")
    for _ in range(3):
        WebhookDelivery.objects.create(webhook=hook, action="x", payload={})
    WebhookDelivery.objects.create(webhook=other, action="x", payload={})
    other_sent = threading.Event()

    def send(url, body, headers):
        if url == other.url:
            other_sent.set()
            return 200
        return 200 if other_sent.wait(timeout=3) else 504
    started = time.monotonic()
    assert webhooks.deliver_due(send) == 4
    assert time.monotonic() - started < 2
    assert set(WebhookDelivery.objects.values_list("status", flat=True)) == {"delivered"}


def test_a_sender_that_dies_mid_send_leaves_a_lease(event, hook):
    """The claim is committed before the send, not held open across it; if the
    sender dies, the delivery waits out the lease and is sent again."""
    WebhookDelivery.objects.create(webhook=hook, action="x", payload={})

    def crash(*args):
        raise RuntimeError("worker killed")
    with pytest.raises(RuntimeError):
        webhooks.deliver_due(crash)
    delivery = WebhookDelivery.objects.get()
    assert delivery.status == "pending" and delivery.attempts == 0
    assert delivery.next_attempt_at - db_now() > timedelta(minutes=1)
    assert webhooks.deliver_due(lambda *a: 200) == 0                          # still leased
    WebhookDelivery.objects.update(next_attempt_at=db_now())
    assert webhooks.deliver_due(lambda *a: 200) == 1
    assert WebhookDelivery.objects.get().status == "delivered"
